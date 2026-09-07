"""Directed core engine control, mirroring core_local_mvm_engine.sv."""
from dataclasses import dataclass, field
from .compute import MVM


@dataclass
class CoreLocalTiming:
    state: str = "idle"
    beat: int = 0
    source: int = 0
    mvm: MVM = field(default_factory=lambda: MVM(timing_only=True))

    @property
    def job_ready(self):
        return self.state == "idle"

    @property
    def weight_ready(self):
        return self.state == "load"

    @property
    def result_valid(self):
        return self.state == "result"

    def tick(self, *, job_valid=False, source=0, weight_valid=False,
             result_ready=True):
        old = self.state
        start = old == "load" and weight_valid and self.beat == 31
        done = self.mvm.done
        self.mvm.tick(start=start, state=0, weight_data=0)
        if old == "idle" and job_valid:
            self.source = source
            self.beat = 0
            self.state = "load"
        elif old == "load" and weight_valid:
            if self.beat == 31:
                self.beat = 0
                self.state = "run"
            else:
                self.beat += 1
        elif old == "run" and done:
            self.state = "result"
        elif old == "result" and result_ready:
            self.state = "idle"
