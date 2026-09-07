"""One stateful full-iteration controller for the cores-only timing path.

Uses the same cores, operand banks, Ramulator and mesh throughout every phase
and epoch. No router reset or RTL-derived timestamp occurs at phase boundaries.
"""
from collections import deque
from .core_iteration_driver import CoreIterationDriver
from .core_state_publication import core_state_publication_plan
from .noc import Flit


class CoreFullController:
    def __init__(self,pipeline,queues,records,**driver_kwargs):
        self.pipeline=pipeline
        self.driver=CoreIterationDriver(pipeline,queues,local_publish=False,**driver_kwargs)
        self.plan=core_state_publication_plan(pipeline.geometry,records)
        self.mode=None
        self.offset=0
        self.quiet=0
        self.tables=[]
        self.pending=[]
        self.release_index=0
        self.local_observer=None
        self.phase_observer=None
        self.phase_cycles={}
        self._barrier_done=False

    @property
    def finished(self):return self.driver.finished

    def _empty(self):
        mesh=self.pipeline.mesh
        return not mesh.has_link_data and not any(f.queue for r in mesh.routers for f in r.fifos)

    def _observe_phase(self,phase):
        key=(self.driver.iteration,phase)
        if key not in self.phase_cycles:
            self.phase_cycles[key]=self.pipeline.cycle
            if self.phase_observer:self.phase_observer(self.pipeline.cycle,self.driver.iteration,phase)

    def tick(self):
        p=self.pipeline;g=p.geometry
        # The start immediately following initialization is derived from
        # init_done; no external trace supplies its cycle.
        starting=(self.mode is None and (self.driver.phase=='start' or
            (self.driver.phase=='wait_init' and all(c.outputs().init_done for c in p.cores.values()))))
        if self.mode is None and self.driver.phase=='done' and not self._barrier_done:
            self.mode='completion'
            self.pending=set(range(g.node_count));self.quiet=0
        if self.mode is None:
            self._observe_phase('start' if starting else self.driver.phase)
            self.driver.tick()
            if starting:
                self.mode='gather';self.offset=0
                self.tables=[{} for _ in range(g.node_count)]
                self._barrier_done=False
            return
        self._observe_phase(self.mode)
        old=self.mode
        phase={'core_inputs':dict(coeff_a=self.driver.coeff_a,coeff_b=self.driver.coeff_b,
                                  noise_amplitude=self.driver.noise)}
        if old=='gather':
            for node in range(g.node_count):
                block=node*g.blocks_per_h1+self.offset
                data=p.cores[block].state_current
                self.tables[node][block]=data
                if self.local_observer:self.local_observer(p.cycle,'gather',node,-1,block,data)
        elif old=='publication':
            pubs=self.plan.remote_publications
            while self.release_index<len(pubs) and 2*self.release_index<=self.offset:
                block,destination=pubs[self.release_index]
                source=block//g.blocks_per_h1
                self.pending[source].append(Flit(data=self.tables[source][block],
                    packet_type=0,dest_x=destination%g.mesh_x,dest_y=destination//g.mesh_x,
                    source_id=source,epoch=self.driver.iteration,block_id=block,last=True))
                self.release_index+=1
            phase['control_packets']={n:q[0] for n,q in enumerate(self.pending) if q}
        elif old=='fill':
            writes={}
            for node,queue in enumerate(self.plan.local_writes_by_h1):
                if self.offset>=len(queue):continue
                local,block=queue[self.offset];h0=node*g.h0_per_h1+local
                data=self.tables[node][block]
                writes[h0]=(block,data)
                if self.local_observer:self.local_observer(p.cycle,'fill',node,h0,block,data)
            phase['bank_writes']=writes
        elif old=='completion':
            phase['control_packets']={n:Flit(packet_type=2,source_id=n,epoch=self.driver.iteration,
                dest_x=n%g.mesh_x,dest_y=n//g.mesh_x,last=True) for n in self.pending}
        p.tick(phase)
        self.offset+=1
        if old=='gather' and self.offset==self.plan.gather_cycles:
            self.mode='publication';self.offset=0;self.release_index=0
            self.pending=[deque() for _ in range(g.node_count)];self.quiet=0
        elif old=='publication':
            for node in p.control_accepted:self.pending[node].popleft()
            for node,flit in p.control_received:
                if flit.packet_type!=0 or flit.epoch!=self.driver.iteration:
                    raise RuntimeError('unexpected state publication response')
                self.tables[node][flit.block_id]=flit.data
            self.quiet=self.quiet+1 if (self.release_index==len(self.plan.remote_publications)
                and not any(self.pending) and self._empty() and not p.last_network_activity) else 0
            if self.quiet==4:
                self.mode='fill' if self.plan.local_fill_cycles else None;self.offset=0
        elif old=='fill' and self.offset==self.plan.local_fill_cycles:
            self.mode=None
        elif old=='completion':
            self.pending.difference_update(p.control_accepted)
            self.quiet=self.quiet+1 if (not self.pending and self._empty() and not p.last_network_activity) else 0
            if self.quiet==4:
                self.mode=None;self._barrier_done=True
