"""Concurrent canonical memory, directed core, and mesh timing pipeline.

Small VCS integration checks exercise the same control path used here. Overall
iteration/state initialization remains the responsibility of the caller.
"""
from collections import Counter, deque
from .core_local import CoreLocalTiming
from .memory import DramWeightStreamer
from .noc import Mesh, Flit, LOCAL, NORTH, SOUTH, EAST, WEST


class CoreMemoryPipeline:
    def __init__(self, geometry, backend, queues, engines, *, ticks_per_cycle=40,
                 fifo_depth=4, interconnect=None, observer=None, noc_observer=None,
                 stall_period=0, use_mesh=True, mem_lanes=16, memory_observer=None):
        self.geometry=geometry
        self.backend=backend
        self.queues={s:deque(q) for s,q in queues.items()}
        self.engines=engines
        self.streamers={s:DramWeightStreamer(mvm_count=n,
            total_block_count=geometry.total_blocks,request_lanes=mem_lanes,response_lanes=mem_lanes)
            for s,n in engines.items()}
        self.mem_lanes=mem_lanes
        self.request_ready={s:[False]*mem_lanes for s in engines}
        self.responses={s:[None]*mem_lanes for s in engines}
        self.cores={}
        self.active_source={}
        self.mesh=Mesh(geometry.mesh_x,geometry.mesh_y,fifo_depth,interconnect)
        self.observer=observer
        self.noc_observer=noc_observer
        self.memory_observer=memory_observer
        self.stall_period=stall_period
        self.ticks_per_cycle=ticks_per_cycle
        self.use_mesh=use_mesh
        self.packet_locks={}
        self.beats=Counter()
        self.completed=0
        self.cycle=0
        self.audit={"source_capacity_blocks":{s:2*n for s,n in engines.items()},
                    "peak_source_blocks":Counter(),"peak_outstanding_requests":Counter(),
                    "local_delivery_beats":Counter()}

    def _core(self,dst):
        if dst not in self.cores:
            self.cores[dst]=CoreLocalTiming()
        return self.cores[dst]

    def _event(self,event,system,dst,src):
        if self.observer:
            self.observer(self.cycle,event,system,dst,src)

    def _noc(self,scope,node,direction,flit,accepted):
        if self.noc_observer:
            self.noc_observer(self.cycle,scope,node,direction,flit,accepted)

    def tick(self):
        g=self.geometry
        total_h0=g.node_count*g.h0_per_h1
        cross_start=total_h0+g.node_count
        out={s:stream.outputs() for s,stream in self.streamers.items()}
        if self.memory_observer:
            for s,o in out.items():
                for lane,request in enumerate(o.memory_requests):
                    if request is not None and self.request_ready[s][lane]:
                        self.memory_observer(self.cycle,"request",s,request.tag)
                for response in self.responses[s]:
                    if response is not None:
                        self.memory_observer(self.cycle,"response",s,response.tag)
        ports={(s,e):node for s,o in out.items() for e,node in enumerate(o.node)}
        cr={p:False for p in ports}; wr={p:False for p in ports}
        jobs={}; weights=set(); used_h0=set(); used_source=set()
        next_active=self.active_source.copy()
        rr=not(self.stall_period>0 and self.cycle%self.stall_period<5)
        comb=[router.outputs() for router in self.mesh.routers]
        injection={};ready={n:False for n in range(g.node_count)}
        selected={}
        if self.use_mesh:
            for p,node in ports.items():
                system,engine=p
                if system<cross_start:
                    continue
                cr[p]=True
                owner=system-cross_start
                if node.weight_valid and owner not in self.packet_locks:
                    self.packet_locks[owner]=p
            for owner,p in self.packet_locks.items():
                node=ports[p]
                if not node.weight_valid:
                    continue
                destination=node.command.block_a//g.blocks_per_h1
                flit=Flit(packet_type=3,dest_x=destination%g.mesh_x,
                          dest_y=destination//g.mesh_x,source_id=owner,
                          block_id=node.command.block_a,epoch=node.command.block_b,
                          last=self.beats[p]==31)
                injection[owner]=flit;selected[owner]=p
                wr[p]=comb[owner].input_ready[LOCAL]
                self._noc("inject",owner,"local",flit,wr[p])
            for node,o in enumerate(comb):
                flit=o.output_flits[LOCAL]
                if flit is None:
                    continue
                dst=flit.block_id
                core=self._core(dst)
                owner=cross_start+flit.source_id
                if core.job_ready:
                    jobs[dst]=flit.epoch
                    next_active[dst]=(owner,-1)
                if (self.active_source.get(dst)==(owner,-1) and core.weight_ready
                        and dst//g.cores_per_h0 not in used_h0):
                    weights.add(dst);ready[node]=True
                    used_h0.add(dst//g.cores_per_h0)
                self._event("eject" if ready[node] else "eject_stall",owner,dst,flit.epoch)
                self._noc("eject",node,"local",flit,ready[node])
        for p,node in ports.items():
            s,e=p
            if self.use_mesh and s>=cross_start:
                continue
            if node.command_valid:
                dst=node.command.block_a
                if dst not in jobs and self._core(dst).job_ready:
                    jobs[dst]=node.command.block_b;cr[p]=True
                    next_active[dst]=p
        for p,node in ports.items():
            s,e=p
            if self.use_mesh and s>=cross_start:
                continue
            if node.weight_valid:
                dst=node.command.block_a
                h0=dst//g.cores_per_h0
                if self.active_source.get(dst)==p and h0 not in used_h0 and s not in used_source:
                    weights.add(dst);wr[p]=self._core(dst).weight_ready
                    used_h0.add(h0);used_source.add(s)
        for s,stream in self.streamers.items():
            cmds=[]
            for e,available in enumerate(out[s].scheduler_ready):
                command=self.queues[s].popleft() if self.cycle>0 and available and self.queues[s] else None
                cmds.append(command)
                if command:
                    self._event("schedule",s,command.block_a,command.block_b)
                node=ports[(s,e)]
                if node.command_valid and cr[(s,e)]:
                    self._event("command",s,node.command.block_a,node.command.block_b)
                if node.weight_valid:
                    self._event("weight" if wr[(s,e)] else "weight_stall",s,
                                node.command.block_a,node.command.block_b)
                    if wr[(s,e)] and (not self.use_mesh or s<cross_start):
                        self.audit["local_delivery_beats"][s]+=1
            stream.tick(scheduler_commands=cmds,
                        node_command_ready=[cr[(s,e)] for e in range(self.engines[s])],
                        node_weight_ready=[wr[(s,e)] for e in range(self.engines[s])],
                        memory_request_ready=self.request_ready[s],memory_responses=self.responses[s])
            occupied=sum(slot.allocated for lane in stream.slots for slot in lane)
            self.audit["peak_source_blocks"][s]=max(self.audit["peak_source_blocks"][s],occupied)
            self.audit["peak_outstanding_requests"][s]=max(self.audit["peak_outstanding_requests"][s],stream.outstanding())
        if self.use_mesh:
            for owner,p in selected.items():
                if wr[p]:
                    self.beats[p]=(self.beats[p]+1)%32
                    if self.beats[p]==0:
                        del self.packet_locks[owner]
            for node,o in enumerate(comb):
                for port,name in ((NORTH,"north"),(SOUTH,"south"),(EAST,"east"),(WEST,"west")):
                    flit=o.output_flits[port]
                    if flit is None:
                        continue
                    accepted=self.mesh.link_ready(node,port,comb)
                    self._noc("link",node,name,flit,accepted)
                    self._event("link" if accepted else "link_stall",cross_start+flit.source_id,flit.block_id,flit.epoch)
            self.mesh.tick(injection,ready)
        for dst,core in list(self.cores.items()):
            if core.result_valid and rr:
                self.completed+=1
                self._event("retire",self.active_source[dst][0],dst,core.source)
            core.tick(job_valid=dst in jobs,source=jobs.get(dst,0),
                      weight_valid=dst in weights,result_ready=rr)
            if core.job_ready:
                del self.cores[dst]
        self.active_source=next_active
        self.backend.tick(self.ticks_per_cycle)
        for s,stream in self.streamers.items():
            self.request_ready[s]=[r is not None and self.backend.send(s,r.address,r.tag)
                                   for r in stream.outputs().memory_requests]
            self.responses[s]=[self.backend.pop(s) for _ in range(self.mem_lanes)]
        self.cycle+=1

    def run(self,max_cycles):
        expected=sum(len(q) for q in self.queues.values())
        while self.completed<expected:
            if self.cycle>=max_cycles:
                raise TimeoutError("integrated core pipeline timed out")
            self.tick()
        return self.cycle
