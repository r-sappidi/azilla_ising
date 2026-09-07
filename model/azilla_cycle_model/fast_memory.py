"""Exact memoization of streamer outputs and empty response FIFOs.

No DRAM latency is replaced. An empty response queue cannot gain an item until
the backend is advanced or a request submitted. Streamer outputs are immutable
combinational views; cache them only between state-changing edges.
"""
from .memory import DramWeightStreamer
from .ramulator import RamulatorBackend


class FastDramWeightStreamer(DramWeightStreamer):
    def __init__(self, **kwargs):
        self._cached_output = None
        super().__init__(**kwargs)

    def reset(self):
        self._cached_output = None
        return super().reset()

    def outputs(self):
        if self._cached_output is None:
            self._cached_output = super().outputs()
        return self._cached_output

    def tick(self, **kwargs):
        # Validate through the original implementation on active cycles. Idle
        # edges with ordinary well-shaped inputs are a proven identity update.
        before = self.outputs()
        allowed = {'rst', 'scheduler_commands', 'node_command_ready',
                   'node_weight_ready', 'memory_request_ready', 'memory_responses'}
        lengths = {'scheduler_commands': self.mvm_count,
                   'node_command_ready': self.mvm_count,
                   'node_weight_ready': self.mvm_count,
                   'memory_request_ready': self.request_lanes,
                   'memory_responses': self.response_lanes}
        normal = not (kwargs.keys() - allowed) and all(
            not kwargs.get(k) or len(kwargs[k]) == n for k, n in lengths.items())
        if (normal and before.idle and not kwargs.get('rst', False)
                and not any(kwargs.get('scheduler_commands') or ())
                and not any(r is not None for r in (kwargs.get('memory_responses') or ()))):
            return before
        try:
            return super().tick(**kwargs)
        finally:
            self._cached_output = None


class FastRamulatorBackend(RamulatorBackend):
    def __init__(self, *args, **kwargs):
        self._empty_systems = set()
        super().__init__(*args, **kwargs)

    def tick(self, count):
        self._empty_systems.clear()
        return super().tick(count)

    def send(self, system, address, tag):
        self._empty_systems.discard(system)
        return super().send(system, address, tag)

    def pop(self, system):
        if system in self._empty_systems:
            return None
        result = super().pop(system)
        if result is None:
            self._empty_systems.add(system)
        return result
