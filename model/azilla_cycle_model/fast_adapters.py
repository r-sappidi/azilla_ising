"""Opt-in H0 routing optimization; original adapter remains the oracle.

Cache immutable combinational outputs and select only destinations named by
valid engines or existing locks. Cache keys include all inputs and lock state,
so changed ready signals and direct diagnostic lock edits cannot go stale.
"""
from .adapters import H0Adapter, H0AdapterOutputs, RoutedPartial


class FastH0Adapter(H0Adapter):
    def __init__(self, core_count, mvm_count, base_block_id=0):
        super().__init__(core_count, mvm_count, base_block_id)
        self._empty = (RoutedPartial(),) * core_count
        self._key = None
        self._output = None
        self._destinations = ()

    def reset(self):
        super().reset()
        self._key = None

    def outputs(self, partials, core_ready):
        key = (tuple(partials), tuple(core_ready), tuple(self.locked))
        if key == self._key:
            return self._output
        selected = {}
        for engine, partial in enumerate(partials):
            core = partial.block_id - self.base_block_id
            if partial.valid and 0 <= core < self.core_count:
                selected.setdefault(core, engine)
        for core, engine in enumerate(self.locked):
            if engine is not None:
                selected[core] = engine
        ready = [False] * self.mvm_count
        outputs = list(self._empty) if selected else self._empty
        # Match reference core order even for adversarial duplicate locks.
        self._destinations = tuple(sorted(selected))
        for core in self._destinations:
            engine = selected[core]
            partial = partials[engine]
            outputs[core] = RoutedPartial(partial.valid, partial.data, partial.block_id)
            ready[engine] = core_ready[core]
        self._output = H0AdapterOutputs(tuple(ready), tuple(outputs))
        self._key = key
        return self._output

    def tick(self, partials, core_ready, *, rst=False):
        before = self.outputs(partials, core_ready)
        if rst:
            self.reset()
            return before
        for core in self._destinations:
            output = before.core_partials[core]
            if not (output.valid and core_ready[core]):
                continue
            selected = next((index for index, ready in enumerate(before.engine_ready)
                             if ready and partials[index].block_id == output.block_id), None)
            if self.locked[core] is None:
                if selected is not None and not partials[selected].last:
                    self.locked[core] = selected
            elif partials[self.locked[core]].last:
                self.locked[core] = None
        return before


def activate():
    """Patch only this process; no changes to the frozen reference module."""
    from . import exact_events
    exact_events.H0Adapter = FastH0Adapter
