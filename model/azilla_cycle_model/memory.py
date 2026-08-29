"""Cycle models for the tagged RTL weight-streaming path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .config import ArchitectureConfig
from .hierarchy import DmaCommand
from .fixed import unsigned


@dataclass(frozen=True, slots=True)
class MemoryRequest:
    address: int
    tag: int


@dataclass(frozen=True, slots=True)
class MemoryResponse:
    data: int
    tag: int


@dataclass(frozen=True, slots=True)
class StreamerNodeOutput:
    command_valid: bool
    command: DmaCommand | None
    weight_valid: bool
    weight_data: int


@dataclass(frozen=True, slots=True)
class StreamerOutputs:
    scheduler_ready: tuple[bool, ...]
    node: tuple[StreamerNodeOutput, ...]
    memory_requests: tuple[MemoryRequest | None, ...]
    outstanding: int
    idle: bool


@dataclass(slots=True)
class _Slot:
    allocated: bool = False
    requests_done: bool = False
    command_sent: bool = False
    command: DmaCommand | None = None
    request_count: int = 0
    response_valid: list[bool] = field(default_factory=lambda: [False] * 32)
    send_beat: int = 0
    data: list[int] = field(default_factory=lambda: [0] * 32)

    def clear(self) -> None:
        self.allocated = False
        self.requests_done = False
        self.command_sent = False
        self.command = None
        self.request_count = 0
        self.response_valid = [False] * len(self.response_valid)
        self.send_beat = 0
        self.data = [0] * len(self.data)


class DramWeightStreamer:
    """Mirror of ``rtl/dram_weight_streamer.sv``."""

    def __init__(
        self,
        *,
        mvm_count: int,
        total_block_count: int,
        request_lanes: int = 16,
        response_lanes: int | None = None,
        max_outstanding: int = 64,
        config: ArchitectureConfig | None = None,
    ):
        if mvm_count <= 0 or total_block_count <= 0 or request_lanes <= 0:
            raise ValueError("streamer geometry must be positive")
        self.config = config or ArchitectureConfig()
        self.mvm_count = mvm_count
        self.total_block_count = total_block_count
        self.request_lanes = request_lanes
        self.response_lanes = response_lanes or request_lanes
        self.max_outstanding = max_outstanding
        self.engine_id_width = max(1, (mvm_count - 1).bit_length())
        self.beat_width = max(1, (self.config.spin_count - 1).bit_length())
        self.slots = [[_Slot(response_valid=[False] * self.config.spin_count,
                             data=[0] * self.config.spin_count)
                       for _ in range(2)] for _ in range(mvm_count)]
        self.head = [0] * mvm_count
        self.tail = [0] * mvm_count

    def reset(self) -> None:
        for engine in self.slots:
            for slot in engine:
                slot.clear()
        self.head = [0] * self.mvm_count
        self.tail = [0] * self.mvm_count

    def encode_tag(self, engine: int, buffer: int, beat: int) -> int:
        return (engine << (self.beat_width + 1)) | (buffer << self.beat_width) | beat

    def decode_tag(self, tag: int) -> tuple[int, int, int]:
        beat_mask = (1 << self.beat_width) - 1
        return (tag >> (self.beat_width + 1),
                (tag >> self.beat_width) & 1, tag & beat_mask)

    def block_address(self, block_a: int, block_b: int, beat: int) -> int:
        low, high = sorted((block_a, block_b))
        block_bytes = (self.config.spin_count * self.config.spin_count *
                       self.config.weight_width // 8)
        tx_bytes = self.config.data_width // 8
        return ((low * self.total_block_count + high) * block_bytes +
                beat * tx_bytes)

    def outstanding(self) -> int:
        return sum(slot.request_count - sum(slot.response_valid)
                   for engine in self.slots for slot in engine)

    def outputs(self) -> StreamerOutputs:
        scheduler_ready = tuple(
            not self.slots[engine][self.tail[engine]].allocated
            for engine in range(self.mvm_count)
        )
        node: list[StreamerNodeOutput] = []
        for engine in range(self.mvm_count):
            slot = self.slots[engine][self.head[engine]]
            complete = slot.allocated and all(slot.response_valid)
            node.append(StreamerNodeOutput(
                command_valid=complete and not slot.command_sent,
                command=slot.command,
                weight_valid=complete and slot.command_sent,
                weight_data=slot.data[slot.send_beat] if complete else 0,
            ))

        requests: list[MemoryRequest | None] = [None] * self.request_lanes
        lane = 0
        outstanding = self.outstanding()
        for engine in range(self.mvm_count):
            for buffer in range(2):
                slot = self.slots[engine][buffer]
                if (lane < self.request_lanes and
                        lane < self.max_outstanding - outstanding and
                        slot.allocated and not slot.requests_done):
                    beat = slot.request_count
                    command = slot.command
                    if command is None:
                        raise RuntimeError("allocated streamer slot lacks metadata")
                    requests[lane] = MemoryRequest(
                        self.block_address(command.block_a, command.block_b, beat),
                        self.encode_tag(engine, buffer, beat),
                    )
                    lane += 1
        return StreamerOutputs(
            scheduler_ready=scheduler_ready,
            node=tuple(node),
            memory_requests=tuple(requests),
            outstanding=outstanding,
            idle=not any(slot.allocated for engine in self.slots for slot in engine),
        )

    def tick(
        self,
        *,
        rst: bool = False,
        scheduler_commands: Sequence[DmaCommand | None] | None = None,
        node_command_ready: Sequence[bool] | None = None,
        node_weight_ready: Sequence[bool] | None = None,
        memory_request_ready: Sequence[bool] | None = None,
        memory_responses: Sequence[MemoryResponse | None] | None = None,
    ) -> StreamerOutputs:
        scheduler_commands = tuple(scheduler_commands or [None] * self.mvm_count)
        node_command_ready = tuple(node_command_ready or [False] * self.mvm_count)
        node_weight_ready = tuple(node_weight_ready or [False] * self.mvm_count)
        memory_request_ready = tuple(
            memory_request_ready or [False] * self.request_lanes
        )
        memory_responses = tuple(memory_responses or [None] * self.response_lanes)
        if len(scheduler_commands) != self.mvm_count:
            raise ValueError("one scheduler command is required per engine")
        if len(node_command_ready) != self.mvm_count or len(node_weight_ready) != self.mvm_count:
            raise ValueError("one node ready is required per engine")
        if len(memory_request_ready) != self.request_lanes:
            raise ValueError("request-ready lane count mismatch")
        if len(memory_responses) != self.response_lanes:
            raise ValueError("response lane count mismatch")

        before = self.outputs()
        if rst:
            self.reset()
            return before

        # Save pre-edge head slots because all sequential actions use them.
        old_heads = list(self.head)

        for engine, command in enumerate(scheduler_commands):
            if command is not None and before.scheduler_ready[engine]:
                buffer = self.tail[engine]
                slot = self.slots[engine][buffer]
                slot.allocated = True
                slot.requests_done = False
                slot.command_sent = False
                slot.command = command
                slot.request_count = 0
                slot.response_valid = [False] * self.config.spin_count
                slot.send_beat = 0
                self.tail[engine] ^= 1

        for lane, request in enumerate(before.memory_requests):
            if request is not None and memory_request_ready[lane]:
                engine, buffer, beat = self.decode_tag(request.tag)
                slot = self.slots[engine][buffer]
                if beat == self.config.spin_count - 1:
                    slot.requests_done = True
                    slot.request_count = self.config.spin_count
                else:
                    slot.request_count += 1

        for response in memory_responses:
            if response is None:
                continue
            engine, buffer, beat = self.decode_tag(response.tag)
            if not (0 <= engine < self.mvm_count and 0 <= beat < self.config.spin_count):
                raise ValueError(f"invalid response tag {response.tag:#x}")
            slot = self.slots[engine][buffer]
            if not slot.allocated or slot.response_valid[beat]:
                raise ValueError(f"stale or duplicate response tag {response.tag:#x}")
            slot.data[beat] = unsigned(response.data, self.config.data_width)
            slot.response_valid[beat] = True

        for engine in range(self.mvm_count):
            buffer = old_heads[engine]
            slot = self.slots[engine][buffer]
            node_output = before.node[engine]
            if node_output.command_valid and node_command_ready[engine]:
                slot.command_sent = True
            if node_output.weight_valid and node_weight_ready[engine]:
                if slot.send_beat == self.config.spin_count - 1:
                    slot.clear()
                    self.head[engine] ^= 1
                else:
                    slot.send_beat += 1
        return before


class FixedLatencyMemory:
    """Deterministic test backend; not a replacement for Ramulator2."""

    def __init__(self, latency: int, data_for_address):
        if latency < 1:
            raise ValueError("latency must be positive")
        self.latency = latency
        self.data_for_address = data_for_address
        self.cycle = 0
        self.pending: list[tuple[int, MemoryRequest]] = []

    def tick(self, requests: Sequence[MemoryRequest | None], response_lanes: int):
        for request in requests:
            if request is not None:
                self.pending.append((self.cycle + self.latency, request))
        ready = [True] * len(requests)
        due = [item for item in self.pending if item[0] <= self.cycle]
        self.pending = [item for item in self.pending if item[0] > self.cycle]
        responses: list[MemoryResponse | None] = [None] * response_lanes
        for lane, (_, request) in enumerate(due[:response_lanes]):
            responses[lane] = MemoryResponse(
                self.data_for_address(request.address), request.tag
            )
        for item in due[response_lanes:]:
            self.pending.append(item)
        self.cycle += 1
        return ready, responses
