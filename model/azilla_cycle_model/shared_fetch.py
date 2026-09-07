"""Experimental single-fetch/two-unicast delivery, not a released baseline.

One extra 1KiB replay register buffer per source lane; fill and drain do not
overlap. This deliberately makes no dual-port SRAM or multicast assumption.
"""
from .hierarchy import DmaCommand
from .memory import DramWeightStreamer, StreamerNodeOutput, StreamerOutputs
from .core_iteration_pipeline import CoreIterationPipeline


class PairReplay:
    IDLE, FILL, COMMAND, SEND = range(4)

    def __init__(self):
        self.state = self.IDLE
        self.command = None
        self.rows = [0] * 32
        self.row = 0
        self.direction = 0

    def outputs(self):
        command = self.command
        if command is not None and self.direction:
            command = DmaCommand(command.block_b, command.block_a,
                                 command.block_b, command.block_a)
        return StreamerNodeOutput(self.state == self.COMMAND, command,
                                  self.state == self.SEND, self.rows[self.row])

    def tick(self, *, command=None, weight_valid=False, weight_data=0,
             command_ready=False, weight_ready=False, rst=False):
        if rst:
            self.__init__()
        elif self.state == self.IDLE and command is not None:
            if command.block_a >= command.block_b:
                raise ValueError('shared fetch requires canonical unordered off-diagonal jobs')
            self.command = command
            self.row = self.direction = 0
            self.state = self.FILL
        elif self.state == self.FILL and weight_valid:
            self.rows[self.row] = weight_data
            if self.row == 31:
                self.row = 0
                self.state = self.COMMAND
            else:
                self.row += 1
        elif self.state == self.COMMAND and command_ready:
            self.state = self.SEND
        elif self.state == self.SEND and weight_ready:
            if self.row < 31:
                self.row += 1
            elif not self.direction:
                self.direction = 1
                self.row = 0
                self.state = self.COMMAND
            else:
                self.state = self.IDLE
                self.row = 0


class SharedFetchStreamer:
    def __init__(self, **kwargs):
        self.base = DramWeightStreamer(**kwargs)
        self.replay = [PairReplay() for _ in range(kwargs['mvm_count'])]

    @property
    def slots(self):
        return self.base.slots

    def outstanding(self):
        return self.base.outstanding()

    def outputs(self):
        o = self.base.outputs()
        return StreamerOutputs(o.scheduler_ready, tuple(r.outputs() for r in self.replay),
                               o.memory_requests, o.outstanding,
                               o.idle and all(r.state == r.IDLE for r in self.replay))

    def tick(self, *, scheduler_commands, node_command_ready, node_weight_ready,
             memory_request_ready, memory_responses, rst=False):
        o = self.base.outputs()
        cr = [r.state == r.IDLE for r in self.replay]
        wr = [r.state == r.FILL for r in self.replay]
        for e, r in enumerate(self.replay):
            n = o.node[e]
            r.tick(command=n.command if n.command_valid and cr[e] else None,
                   weight_valid=n.weight_valid and wr[e], weight_data=n.weight_data,
                   command_ready=node_command_ready[e], weight_ready=node_weight_ready[e], rst=rst)
        self.base.tick(scheduler_commands=scheduler_commands,
                       node_command_ready=cr, node_weight_ready=wr,
                       memory_request_ready=memory_request_ready,
                       memory_responses=memory_responses, rst=rst)


class SharedFetchPipeline(CoreIterationPipeline):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.streamers = {s: SharedFetchStreamer(mvm_count=n,
            total_block_count=self.geometry.total_blocks,
            request_lanes=self.mem_lanes, response_lanes=self.mem_lanes)
            for s, n in self.engines.items()}
        self.audit['fetch_policy'] = 'single_fetch_two_unicast_v1'
        self.audit['extra_replay_register_bytes'] = 1024 * sum(self.engines.values())
