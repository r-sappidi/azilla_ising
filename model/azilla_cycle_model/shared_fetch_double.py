"""Opt-in two-slot forwarding; no changes to the original frozen baseline."""
from .shared_fetch import PairReplay, SharedFetchStreamer, SharedFetchPipeline
from .memory import DramWeightStreamer


class DoublePairReplay:
    IDLE, FILL = PairReplay.IDLE, PairReplay.FILL

    def __init__(self):
        self.slots = [PairReplay(), PairReplay()]
        self.fill_slot = self.drain_slot = 0
        self.fill_row = self.drain_beat = 0

    @property
    def state(self):
        # SharedFetchStreamer uses state only for its input handshake/idle.
        return self.slots[self.fill_slot].state

    @property
    def idle(self):
        return all(s.state == s.IDLE for s in self.slots)

    def outputs(self):
        return self.slots[self.drain_slot].outputs()

    def tick(self, *, command=None, weight_valid=False, weight_data=0,
             command_ready=False, weight_ready=False, rst=False):
        if rst:
            self.__init__()
            return
        fill = self.fill_slot
        drain = self.drain_slot
        fill_accept = weight_valid and self.slots[fill].state == PairReplay.FILL
        drain_accept = weight_ready and self.slots[drain].outputs().weight_valid
        for i, slot in enumerate(self.slots):
            slot.tick(command=command if i == fill else None,
                      weight_valid=weight_valid and i == fill,
                      weight_data=weight_data,
                      command_ready=command_ready and i == drain,
                      weight_ready=weight_ready and i == drain)
        if fill_accept:
            self.fill_row = (self.fill_row + 1) % 32
            if not self.fill_row:self.fill_slot ^= 1
        if drain_accept:
            self.drain_beat = (self.drain_beat + 1) % 64
            if not self.drain_beat:self.drain_slot ^= 1


class DoubleFetchStreamer(SharedFetchStreamer):
    def __init__(self, **kwargs):
        self.base = DramWeightStreamer(**kwargs)
        self.replay = [DoublePairReplay() for _ in range(kwargs['mvm_count'])]

    def outputs(self):
        from .memory import StreamerOutputs
        o = self.base.outputs()
        return StreamerOutputs(o.scheduler_ready, tuple(r.outputs() for r in self.replay),
                               o.memory_requests, o.outstanding,
                               o.idle and all(r.idle for r in self.replay))


class DoubleFetchPipeline(SharedFetchPipeline):
    def __init__(self, *args, **kwargs):
        kwargs['cir_matched_delivery']=True
        super().__init__(*args, **kwargs)
        self.streamers = {s: DoubleFetchStreamer(mvm_count=n,
            total_block_count=self.geometry.total_blocks,
            request_lanes=self.mem_lanes, response_lanes=self.mem_lanes)
            for s,n in self.engines.items()}
        self.audit['fetch_policy'] = 'single_fetch_two_slot_cir_routes_v3'
        self.audit['delivery_contract'] = 'H0 per-core outputs; H1 per-child packet locks; shared mesh unchanged'
        self.audit['extra_replay_register_bytes'] = 2048 * sum(self.engines.values())
