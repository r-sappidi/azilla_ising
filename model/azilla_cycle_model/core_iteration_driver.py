"""Independent external controller for full-core integration validation.

Controls initialization, explicit local frozen-state writes, compute drain,
update and commit from model handshakes; never consumes an RTL timing trace.
The local state-load stimulus is not an inter-package publication model.
"""
from collections import deque
from .hierarchy import DmaCommand


class CoreIterationDriver:
    def __init__(self, pipeline, queues, *, iterations=2, diagonal_rows=None,
                 initial_states=None, noise_seeds=None, coeff_a=1, coeff_b=1,
                 noise_amplitude=0, initialized=False, local_publish=True):
        self.pipeline=pipeline
        self.queues=queues
        self.iterations=iterations
        self.diagonal_rows=diagonal_rows or {}
        self.initial_states=initial_states or {}
        self.noise_seeds=noise_seeds or {}
        self.coeff_a,self.coeff_b,self.noise=coeff_a,coeff_b,noise_amplitude
        self.phase='publish' if initialized and local_publish else ('start' if initialized else 'initial_idle')
        self.local_publish=local_publish
        self.row=0
        self.block=0
        self.iteration=0
        self.commit_delay=0
        self.finished=False
        self.next_states=[]
        self.jobs_per_iteration=sum(len(q) for q in queues.values())

    def tick(self):
        p=self.pipeline
        inputs=dict(coeff_a=self.coeff_a,coeff_b=self.coeff_b,noise_amplitude=self.noise)
        phase=dict(core_inputs=inputs,per_core={})
        old=self.phase
        if old=='initial_idle':
            self.phase='init'
        elif old=='init':
            inputs['init_start']=True
            phase['per_core']={c:dict(init_state=self.initial_states.get(c,0),
                                     noise_seed=self.noise_seeds.get(c,0)) for c in p.cores}
            self.phase='weights'
        elif old=='weights':
            inputs['weight_init_valid']=True
            for c in p.cores:
                phase['per_core'][c]=dict(weight_init_data=self.diagonal_rows.get(c,[0]*32)[self.row])
            self.row+=1
            if self.row==32:self.phase='wait_init'
        elif old=='wait_init':
            if all(c.outputs().init_done for c in p.cores.values()):
                self.phase='publish' if self.local_publish else 'start'
                old=self.phase
        if old=='publish':
            phase.update(publish_valid=True,publish_index=self.block)
            self.block+=1
            if self.block==p.geometry.total_blocks:self.phase='start'
        elif old=='start':
            inputs['iter_start']=True
            p.queues={s:deque(q) for s,q in self.queues.items()}
            self.phase='compute'
        elif old=='compute':
            phase['dispatch_enable']=True
        elif old=='done':
            inputs['partials_done']=True
            self.phase='wait_update'
        elif old=='wait_update':
            if all(c.outputs().iter_done for c in p.cores.values()):
                self.next_states.append({i:c.outputs().state_next for i,c in p.cores.items()})
                inputs['commit']=True
                self.phase='commit_delay'
                self.commit_delay=2
        elif old=='commit_delay':
            self.commit_delay-=1
            if self.commit_delay==0:
                self.iteration+=1
                if self.iteration>=self.iterations:
                    self.phase='finish'
                    self.finished=True
                else:
                    self.block=0
                    self.phase='publish' if self.local_publish else 'start'
        elif old=='finish':
            self.finished=True
        p.tick(phase)
        if (self.phase=='compute' and old=='compute' and
            p.completed==(self.iteration+1)*self.jobs_per_iteration and
            all(s.outputs().idle for s in p.streamers.values())):
            self.phase='done'
        return phase
