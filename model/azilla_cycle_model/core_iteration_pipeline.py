"""Full-core validation pipeline; explicit lifecycle and banked operand storage.

Kept separate from the released kernel until integrated VCS differentials pass.
"""
from collections import Counter, deque
from .core_iteration import CoreIteration
from .core_state_bank import CoreStateBank
from .memory import DramWeightStreamer
from .noc import Mesh, Flit, LOCAL, NORTH, SOUTH, EAST, WEST


class CoreIterationPipeline:
    def __init__(self, geometry, backend, queues, engines, *, ticks_per_cycle=40,
                 fifo_depth=4, interconnect=None, observer=None, noc_observer=None,
                 stall_period=0, use_mesh=True, mem_lanes=16, memory_observer=None,
                 timing_only=False, compress_idle=False, mesh=None,
                 cir_matched_delivery=False):
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
        self.cores={c:CoreIteration(timing_only=timing_only) for c in range(geometry.total_blocks)}
        self.compress_idle=compress_idle
        self.running_cores=set(self.cores)
        self.state_banks={h:CoreStateBank(geometry.total_blocks, lanes=geometry.cores_per_h0)
                          for h in range(geometry.node_count*geometry.h0_per_h1)}
        self.phase={}
        self.state_observer=None
        self.control_accepted=set()
        self.control_received=[]
        self.last_network_activity=False
        self.active_source={}
        self.mesh=mesh if mesh is not None else Mesh(geometry.mesh_x,geometry.mesh_y,fifo_depth,interconnect)
        self.observer=observer
        self.noc_observer=noc_observer
        self.memory_observer=memory_observer
        self.stall_period=stall_period
        self.ticks_per_cycle=ticks_per_cycle
        self.use_mesh=use_mesh
        self.cir_matched_delivery=cir_matched_delivery
        self.external_locks={}
        self.external_beats=Counter()
        self.packet_locks={}
        self.beats=Counter()
        self.completed=0
        self.cycle=0
        self.audit={"source_capacity_blocks":{s:2*n for s,n in engines.items()},
                    "peak_source_blocks":Counter(),"peak_outstanding_requests":Counter(),
                    "local_delivery_beats":Counter()}

    def _core(self,dst):
        if dst not in self.cores:
            self.cores[dst]=CoreIteration()
        return self.cores[dst]

    def _event(self,event,system,dst,src):
        if self.observer:
            self.observer(self.cycle,event,system,dst,src)

    def _noc(self,scope,node,direction,flit,accepted):
        if self.noc_observer:
            self.noc_observer(self.cycle,scope,node,direction,flit,accepted)

    def tick(self, phase=None):
        phase = phase or {}
        self.phase = phase
        self.control_accepted=set()
        self.control_received=[]
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
        jobs={}; weights={}; used_h0=set(); used_source=set()
        next_active=self.active_source.copy()
        rr=True
        allow_weight=not(self.stall_period>0 and self.cycle%self.stall_period<5)
        comb=[router.outputs() for router in self.mesh.routers]
        injection={};ready={n:False for n in range(g.node_count)}
        selected={}
        external_choice=dict(self.external_locks)
        external_accepted=[]
        if self.cir_matched_delivery:
            # H1 engines arbitrate independently per destination H0; H0-local
            # lanes instead arbitrate per core and never consume this link.
            for p,node in ports.items():
                if total_h0<=p[0]<cross_start and node.weight_valid:
                    dst=node.command.block_a;h0=dst//g.cores_per_h0
                    if self.active_source.get(dst)==p:
                        external_choice.setdefault(h0,p)
            for n,o in enumerate(comb):
                flit=o.output_flits[LOCAL]
                if flit is not None and flit.packet_type==3:
                    external_choice.setdefault(flit.block_id//g.cores_per_h0,
                                                (cross_start+flit.source_id,-1))
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
                          last=self.beats[p]==31,data=node.weight_data)
                injection[owner]=flit;selected[owner]=p
                wr[p]=comb[owner].input_ready[LOCAL]
                self._noc("inject",owner,"local",flit,wr[p])
            for node,flit in phase.get('control_packets',{}).items():
                if node in injection:
                    raise RuntimeError('control phase overlaps undrained weight injection')
                injection[node]=flit
                accepted=comb[node].input_ready[LOCAL]
                self._noc('inject',node,'local',flit,accepted)
                if accepted:self.control_accepted.add(node)
            for node,o in enumerate(comb):
                flit=o.output_flits[LOCAL]
                if flit is None:
                    continue
                if flit.packet_type != 3:
                    ready[node]=True
                    self.control_received.append((node,flit))
                    self._noc('eject',node,'local',flit,True)
                    continue
                dst=flit.block_id
                core=self._core(dst)
                owner=cross_start+flit.source_id
                if core.outputs().job_ready:
                    jobs[dst]=flit.epoch
                    next_active[dst]=(owner,-1)
                if (self.active_source.get(dst)==(owner,-1) and core.outputs().weight_ready
                        and dst//g.cores_per_h0 not in used_h0 and allow_weight
                        and (not self.cir_matched_delivery or
                             external_choice.get(dst//g.cores_per_h0)==(owner,-1))):
                    weights[dst]=flit.data;ready[node]=True
                    used_h0.add(dst//g.cores_per_h0)
                    if self.cir_matched_delivery:
                        external_accepted.append((dst//g.cores_per_h0,(owner,-1)))
                self._event("eject" if ready[node] else "eject_stall",owner,dst,flit.epoch)
                self._noc("eject",node,"local",flit,ready[node])
        for p,node in ports.items():
            s,e=p
            if self.use_mesh and s>=cross_start:
                continue
            if node.command_valid:
                dst=node.command.block_a
                if dst not in jobs and self._core(dst).outputs().job_ready:
                    jobs[dst]=node.command.block_b;cr[p]=True
                    next_active[dst]=p
        for p,node in ports.items():
            s,e=p
            if self.use_mesh and s>=cross_start:
                continue
            if node.weight_valid:
                dst=node.command.block_a
                h0=dst//g.cores_per_h0
                route_available=(h0 not in used_h0 and s not in used_source)
                if self.cir_matched_delivery:
                    route_available=(dst not in weights and
                        (s<total_h0 or (external_choice.get(h0)==p and h0 not in used_h0)))
                if self.active_source.get(dst)==p and route_available:
                    wr[p]=self._core(dst).outputs().weight_ready and allow_weight
                    if wr[p]: weights[dst]=node.weight_data
                    if not self.cir_matched_delivery:
                        used_h0.add(h0);used_source.add(s)
                    elif s>=total_h0:
                        used_h0.add(h0)
                        if wr[p]:external_accepted.append((h0,p))
        for h0,source in external_accepted:
            self.external_beats[h0]=(self.external_beats[h0]+1)%32
            if self.external_beats[h0]:self.external_locks[h0]=source
            else:self.external_locks.pop(h0,None)
        for s,stream in self.streamers.items():
            cmds=[]
            for e,available in enumerate(out[s].scheduler_ready):
                command=self.queues[s].popleft() if phase.get("dispatch_enable",False) and available and self.queues[s] else None
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
            self.last_network_activity=bool(injection) or any(
                f is not None for o in comb for f in o.output_flits) or self.mesh.has_link_data
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
                    if flit.packet_type==3:
                        self._event("link" if accepted else "link_stall",cross_start+flit.source_id,flit.block_id,flit.epoch)
            self.mesh.tick(injection,ready)
        global_inputs=phase.get('core_inputs',{})
        if (not self.compress_idle or any(global_inputs.get(k,False) for k in
                ('rst','init_start','weight_init_valid','iter_start','partials_done','commit','done'))):
            self.running_cores=set(self.cores)
        self.running_cores.update(jobs)
        self.running_cores.update(weights)
        bank_requests={h:{} for h in self.state_banks}
        for dst in sorted(self.running_cores):
            core=self.cores[dst]
            output=core.outputs()
            if output.state_req_valid:
                bank_requests[dst//g.cores_per_h0][dst%g.cores_per_h0]=output.state_req_block_id
        bank_ready={h:bank.ready(bank_requests[h]) for h,bank in self.state_banks.items()}
        bank_responses={h:bank.responses.copy() for h,bank in self.state_banks.items()}
        for h,bank in self.state_banks.items():
            write=phase.get('bank_writes',{}).get(h)
            if phase.get("publish_valid"):
                index=phase["publish_index"]
                write=(index,self.cores[index].state_current)
            bank.tick(bank_requests[h],write=write)
        next_running=set()
        for dst in sorted(self.running_cores):
            core=self.cores[dst]
            h,lane=divmod(dst,g.cores_per_h0)
            response=bank_responses[h].get(lane)
            if core.outputs().job_done:
                self.completed+=1
                self._event("retire",self.active_source[dst][0],dst,core.source)
            if self.state_observer:
                if bank_ready[h].get(lane,False):
                    self.state_observer(self.cycle,"request",dst,bank_requests[h][lane])
                if response:
                    self.state_observer(self.cycle,"response",dst,response.data)
            args=dict(phase.get("core_inputs",{}))
            args.update(phase.get("per_core",{}).get(dst,{}))
            core.tick(**args,job_valid=dst in jobs,source=jobs.get(dst,0),
                      transpose=dst>jobs.get(dst,0),
                      weight_valid=dst in weights,weight_data=weights.get(dst,0),
                      state_req_ready=bank_ready[h].get(lane,False),
                      state_rsp_valid=response is not None,
                      state_rsp_data=0 if response is None else response.data)
            if (core.off_state != core.OFF_IDLE or
                core.core_state in (core.INIT,core.FINALIZE,core.COMMIT) or
                (core.core_state==core.ACCUMULATE and
                 (not core.diagonal_captured or core.partials_done_pending))):
                next_running.add(dst)
        self.running_cores=next_running
        self.active_source=next_active
        self.backend.tick(self.ticks_per_cycle)
        for s,stream in self.streamers.items():
            self.request_ready[s]=[r is not None and self.backend.send(s,r.address,r.tag)
                                   for r in stream.outputs().memory_requests]
            self.responses[s]=[self.backend.pop(s) for _ in range(self.mem_lanes)]
        self.cycle+=1
