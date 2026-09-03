"""Flit- and cycle-accurate model of the configured one-VC FlooNoC mesh."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable


# Azilla wrapper port order.
LOCAL, NORTH, SOUTH, EAST, WEST = range(5)
PORT_COUNT = 5
# azilla_floo_router's explicit port permutation.  Floo arbitration priorities
# operate on the right-hand indices, not on Azilla's public port numbers.
AZ_TO_FLOO = (4, 2, 0, 1, 3)
FLOO_TO_AZ = (2, 3, 1, 4, 0)


@dataclass(frozen=True, slots=True)
class Flit:
    data: int = 0
    packet_type: int = 0
    dest_x: int = 0
    dest_y: int = 0
    source_id: int = 0
    epoch: int = 0
    block_id: int = 0
    last: bool = True


@dataclass(frozen=True, slots=True)
class InterconnectConfig:
    """Timing contract for every directed inter-H1 physical link.

    ``forward_latency_cycles`` is additional latency beyond the legacy
    directly connected RTL mesh. Zero therefore preserves the existing RTL
    differential. ``flit_interval_cycles`` is the minimum number of cycles
    between accepted logical 256-bit flits. ``max_inflight_flits`` is the
    credit window shared by flits in flight, queued at the receiver, and
    awaiting credit return. A credit becomes reusable
    ``credit_return_latency_cycles`` after the destination router accepts the
    flit.
    """

    forward_latency_cycles: int = 0
    flit_interval_cycles: int = 1
    max_inflight_flits: int = 4
    credit_return_latency_cycles: int = 0

    def __post_init__(self) -> None:
        if self.forward_latency_cycles < 0:
            raise ValueError("link forward latency cannot be negative")
        if self.flit_interval_cycles <= 0:
            raise ValueError("link flit interval must be positive")
        if self.max_inflight_flits <= 0:
            raise ValueError("link credit capacity must be positive")
        if self.credit_return_latency_cycles < 0:
            raise ValueError("link credit-return latency cannot be negative")

    @property
    def is_rtl_direct(self) -> bool:
        return (
            self.forward_latency_cycles == 0
            and self.flit_interval_cycles == 1
            and self.credit_return_latency_cycles == 0
        )


@dataclass(slots=True)
class _DirectedLink:
    """One credit-controlled, ordered, directed physical link."""

    config: InterconnectConfig
    cycle: int = 0
    next_launch_cycle: int = 0
    credits_in_use: int = 0
    inflight: deque[tuple[int, Flit]] = field(default_factory=deque)
    arrived: deque[Flit] = field(default_factory=deque)
    credit_returns: deque[int] = field(default_factory=deque)

    @property
    def can_launch(self) -> bool:
        return (
            self.cycle >= self.next_launch_cycle
            and self.credits_in_use < self.config.max_inflight_flits
        )

    @property
    def output(self) -> Flit | None:
        return self.arrived[0] if self.arrived else None

    @property
    def has_data(self) -> bool:
        return bool(self.inflight or self.arrived)

    def reset(self) -> None:
        self.cycle = 0
        self.next_launch_cycle = 0
        self.credits_in_use = 0
        self.inflight.clear()
        self.arrived.clear()
        self.credit_returns.clear()

    def _advance_control(self, new_cycle: int) -> None:
        self.cycle = new_cycle
        while self.credit_returns and self.credit_returns[0] <= self.cycle:
            self.credit_returns.popleft()
            self.credits_in_use -= 1
        while self.inflight and self.inflight[0][0] <= self.cycle:
            _, flit = self.inflight.popleft()
            self.arrived.append(flit)
        if self.credits_in_use < 0:
            raise RuntimeError("link returned more credits than it consumed")

    def tick(
        self,
        source_flit: Flit | None,
        *,
        launch_accepted: bool,
        delivery_accepted: bool,
    ) -> None:
        """Sample one edge after Mesh computes the two handshakes."""

        if delivery_accepted:
            if self.config.forward_latency_cycles == 0:
                if not launch_accepted or source_flit is None:
                    raise RuntimeError("direct-link delivery lacks a launch")
            else:
                if not self.arrived:
                    raise RuntimeError("link delivered without an arrived flit")
                self.arrived.popleft()
            self.credit_returns.append(
                self.cycle + self.config.credit_return_latency_cycles + 1
            )

        if launch_accepted:
            if source_flit is None or not self.can_launch:
                raise RuntimeError("link launch violated ready/valid")
            self.credits_in_use += 1
            self.next_launch_cycle = (
                self.cycle + self.config.flit_interval_cycles
            )
            if self.config.forward_latency_cycles > 0:
                self.inflight.append((
                    self.cycle + self.config.forward_latency_cycles,
                    source_flit,
                ))

        self._advance_control(self.cycle + 1)

    def advance_idle(self, cycles: int) -> None:
        if cycles < 0:
            raise ValueError("cannot advance a link backward")
        if self.has_data:
            raise RuntimeError("cannot skip a link containing flit data")
        self._advance_control(self.cycle + cycles)


@dataclass(slots=True)
class _InputFifo:
    depth: int
    queue: list[Flit] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        # cc_stream_fifo uses ready_o = ~full and does not make space from a
        # simultaneous pop visible combinationally.
        return len(self.queue) < self.depth

    @property
    def head(self) -> Flit | None:
        return self.queue[0] if self.queue else None


@dataclass(frozen=True, slots=True)
class RouterOutputs:
    input_ready: tuple[bool, ...]
    output_flits: tuple[Flit | None, ...]
    selected_inputs: tuple[int | None, ...]


class FlooRouter:
    """Configured behavior of ``azilla_floo_router``/``floo_router``.

    This covers the instantiated configuration: XY routing, one physical and
    virtual channel, input FIFOs, no output FIFOs, unicast only, and wormhole
    output arbitration.
    """

    def __init__(self, x: int, y: int, fifo_depth: int = 4):
        if fifo_depth < 2:
            raise ValueError("RTL FIFO depth must be at least two")
        self.x = x
        self.y = y
        self.fifos = [_InputFifo(fifo_depth) for _ in range(PORT_COUNT)]
        self.rr_priority = [0] * PORT_COUNT
        # Registered state in floo_wormhole_arbiter and its LockIn
        # cc_rr_arb_tree. Requests are snapshotted at packet boundaries so a
        # late request cannot affect arbitration for an in-flight packet.
        self.request_snapshot = [set() for _ in range(PORT_COUNT)]
        self.last_handshake = [False] * PORT_COUNT
        self.arbiter_locked = [False] * PORT_COUNT
        self.locked_requests = [set() for _ in range(PORT_COUNT)]

    def reset(self) -> None:
        for fifo in self.fifos:
            fifo.queue.clear()
        self.rr_priority = [0] * PORT_COUNT
        self.request_snapshot = [set() for _ in range(PORT_COUNT)]
        self.last_handshake = [False] * PORT_COUNT
        self.arbiter_locked = [False] * PORT_COUNT
        self.locked_requests = [set() for _ in range(PORT_COUNT)]

    def route(self, flit: Flit) -> int:
        # FlooNoC resolves X first.  The wrapper reverses the Floo Y naming,
        # yielding Azilla NORTH for decreasing y and SOUTH for increasing y.
        if flit.dest_x == self.x and flit.dest_y == self.y:
            return LOCAL
        if flit.dest_x < self.x:
            return WEST
        if flit.dest_x > self.x:
            return EAST
        if flit.dest_y < self.y:
            return NORTH
        return SOUTH

    @staticmethod
    def _select_rr(requests: list[int], priority: int) -> int | None:
        if not requests:
            return None
        above = [index for index in requests if index >= priority]
        return min(above) if above else min(requests)

    @staticmethod
    def _fair_next(requests: list[int], old_priority: int) -> int:
        """Mirror FairArb's next priority calculation from cc_rr_arb_tree."""
        if not requests:
            return old_priority
        above = [index for index in requests if index > old_priority]
        return min(above) if above else min(requests)

    def _request_vectors(self) -> tuple[list[set[int]], list[set[int]]]:
        """Return live and cc_rr-visible requests in Floo port numbering."""

        current = [set() for _ in range(PORT_COUNT)]
        for input_port, fifo in enumerate(self.fifos):
            if fifo.head is not None:
                current[self.route(fifo.head)].add(AZ_TO_FLOO[input_port])

        visible: list[set[int]] = []
        for output_port in range(PORT_COUNT):
            snapshot_next = (
                current[output_port]
                if not self.request_snapshot[output_port] or
                self.last_handshake[output_port]
                else self.request_snapshot[output_port]
            )
            visible.append(
                self.locked_requests[output_port]
                if self.arbiter_locked[output_port] else snapshot_next
            )
        return current, visible

    def outputs(self) -> RouterOutputs:
        current_requests, visible_requests = self._request_vectors()

        selected: list[int | None] = [None] * PORT_COUNT
        output_flits: list[Flit | None] = [None] * PORT_COUNT
        for output_port in range(PORT_COUNT):
            floo_choice = self._select_rr(
                list(visible_requests[output_port]),
                self.rr_priority[output_port],
            )
            choice = None if floo_choice is None else FLOO_TO_AZ[floo_choice]
            if choice is not None:
                head = self.fifos[choice].head
                # The registered choice is qualified by the live input valid.
                if (floo_choice in current_requests[output_port] and
                        head is not None and self.route(head) == output_port):
                    selected[output_port] = choice
                    output_flits[output_port] = head

        return RouterOutputs(
            tuple(fifo.ready for fifo in self.fifos),
            tuple(output_flits),
            tuple(selected),
        )

    def tick(
        self,
        input_flits: Iterable[Flit | None],
        output_ready: Iterable[bool],
        *,
        rst: bool = False,
    ) -> RouterOutputs:
        inputs = tuple(input_flits)
        ready = tuple(output_ready)
        if len(inputs) != PORT_COUNT or len(ready) != PORT_COUNT:
            raise ValueError("router requires five inputs and outputs")
        before = self.outputs()
        if rst:
            self.reset()
            return before

        current_requests, visible_requests = self._request_vectors()
        popped: set[int] = set()
        for output_port, (flit, is_ready, input_port) in enumerate(zip(
            before.output_flits, ready, before.selected_inputs
        )):
            snapshot_next = (
                current_requests[output_port]
                if not self.request_snapshot[output_port] or
                self.last_handshake[output_port]
                else self.request_snapshot[output_port]
            )
            requests = visible_requests[output_port]
            request_valid = bool(requests)
            last_transfer = (
                flit is not None and is_ready and input_port is not None and
                flit.last
            )

            # Floo grants the RR arbiter only for an accepted last flit. Its
            # fair next priority is derived from the snapshotted request set.
            if last_transfer:
                self.rr_priority[output_port] = self._fair_next(
                    list(requests), self.rr_priority[output_port]
                )

            self.request_snapshot[output_port] = set(snapshot_next)
            self.last_handshake[output_port] = last_transfer
            self.arbiter_locked[output_port] = request_valid and not last_transfer
            self.locked_requests[output_port] = set(requests)

            if flit is not None and is_ready and input_port is not None:
                popped.add(input_port)

        # FIFO push/pop use pre-edge ready and head state.  A full FIFO cannot
        # accept on the same edge it is popped.
        for input_port, fifo in enumerate(self.fifos):
            push = inputs[input_port] is not None and before.input_ready[input_port]
            pop = input_port in popped
            if pop:
                fifo.queue.pop(0)
            if push:
                fifo.queue.append(inputs[input_port])  # type: ignore[arg-type]
        return before


class Mesh:
    """Synchronous rectangular mesh of :class:`FlooRouter` instances."""

    def __init__(
        self,
        width: int,
        height: int,
        fifo_depth: int = 4,
        interconnect: InterconnectConfig | None = None,
    ):
        if width <= 0 or height <= 0:
            raise ValueError("mesh dimensions must be positive")
        self.width = width
        self.height = height
        self.interconnect = interconnect or InterconnectConfig()
        self.routers = [FlooRouter(x, y, fifo_depth)
                        for y in range(height) for x in range(width)]
        self.links: dict[tuple[int, int], _DirectedLink] = {}
        for node, router in enumerate(self.routers):
            for out_port, nx, ny, _ in self._neighbors(router):
                if 0 <= nx < self.width and 0 <= ny < self.height:
                    self.links[(node, out_port)] = _DirectedLink(
                        self.interconnect
                    )
        self.cycle = 0

    def _index(self, x: int, y: int) -> int:
        return y * self.width + x

    @staticmethod
    def _neighbors(router: FlooRouter):
        return (
            (NORTH, router.x, router.y - 1, SOUTH),
            (SOUTH, router.x, router.y + 1, NORTH),
            (EAST, router.x + 1, router.y, WEST),
            (WEST, router.x - 1, router.y, EAST),
        )

    def link_ready(
        self,
        node: int,
        output_port: int,
        router_outputs: list[RouterOutputs] | None = None,
    ) -> bool:
        """Return the source-router ready seen on one cardinal output."""

        link = self.links.get((node, output_port))
        if link is None or not link.can_launch:
            return False
        if self.interconnect.forward_latency_cycles > 0:
            return True
        outputs = router_outputs or [router.outputs() for router in self.routers]
        router = self.routers[node]
        _, nx, ny, neighbor_input = next(
            item for item in self._neighbors(router) if item[0] == output_port
        )
        neighbor = self._index(nx, ny)
        return outputs[neighbor].input_ready[neighbor_input]

    @property
    def has_link_data(self) -> bool:
        return any(link.has_data for link in self.links.values())

    @property
    def has_data(self) -> bool:
        return (
            self.has_link_data
            or any(fifo.queue for router in self.routers for fifo in router.fifos)
        )

    def advance_idle(self, cycles: int) -> None:
        """Bulk-advance serializer/credit timers while no flits exist."""

        if self.has_data:
            raise RuntimeError("cannot skip a non-empty mesh")
        for link in self.links.values():
            link.advance_idle(cycles)
        self.cycle += cycles

    def reset(self) -> None:
        for router in self.routers:
            router.reset()
        for link in self.links.values():
            link.reset()
        self.cycle = 0

    def tick(
        self,
        local_injections: dict[int, Flit] | None = None,
        local_ready: dict[int, bool] | None = None,
    ) -> tuple[dict[int, bool], dict[int, Flit]]:
        """Advance one edge and return pre-edge injection-ready/ejections."""

        local_injections = local_injections or {}
        local_ready = local_ready or {}
        comb = [router.outputs() for router in self.routers]
        inputs: list[list[Flit | None]] = [[None] * PORT_COUNT for _ in self.routers]
        output_ready: list[list[bool]] = [[False] * PORT_COUNT for _ in self.routers]
        injection_ready: dict[int, bool] = {}
        ejections: dict[int, Flit] = {}

        for node, router in enumerate(self.routers):
            inputs[node][LOCAL] = local_injections.get(node)
            injection_ready[node] = comb[node].input_ready[LOCAL]
            output_ready[node][LOCAL] = local_ready.get(node, True)
            if comb[node].output_flits[LOCAL] is not None:
                ejections[node] = comb[node].output_flits[LOCAL]  # type: ignore[assignment]

            for out_port, nx, ny, neighbor_in in self._neighbors(router):
                if 0 <= nx < self.width and 0 <= ny < self.height:
                    neighbor = self._index(nx, ny)
                    link = self.links[(node, out_port)]
                    if self.interconnect.forward_latency_cycles == 0:
                        if link.can_launch:
                            inputs[neighbor][neighbor_in] = (
                                comb[node].output_flits[out_port]
                            )
                        output_ready[node][out_port] = (
                            link.can_launch
                            and comb[neighbor].input_ready[neighbor_in]
                        )
                    else:
                        inputs[neighbor][neighbor_in] = link.output
                        output_ready[node][out_port] = link.can_launch
                else:
                    output_ready[node][out_port] = False

        for node, router in enumerate(self.routers):
            router.tick(inputs[node], output_ready[node])
        for node, router in enumerate(self.routers):
            for out_port, nx, ny, neighbor_in in self._neighbors(router):
                if not (0 <= nx < self.width and 0 <= ny < self.height):
                    continue
                neighbor = self._index(nx, ny)
                link = self.links[(node, out_port)]
                source_flit = comb[node].output_flits[out_port]
                launch_accepted = (
                    source_flit is not None and output_ready[node][out_port]
                )
                delivery_accepted = (
                    launch_accepted
                    if self.interconnect.forward_latency_cycles == 0
                    else link.output is not None
                    and comb[neighbor].input_ready[neighbor_in]
                )
                link.tick(
                    source_flit,
                    launch_accepted=launch_accepted,
                    delivery_accepted=delivery_accepted,
                )
        self.cycle += 1
        return injection_ready, ejections
