"""Exact between-edge output caching and quiescent-network identity steps."""
from .noc import FlooRouter, Mesh


class FastFlooRouter(FlooRouter):
    def __init__(self, *args, **kwargs):
        self._cached_output = None
        super().__init__(*args, **kwargs)

    def outputs(self):
        if self._cached_output is None:
            self._cached_output = super().outputs()
        return self._cached_output

    def reset(self):
        self._cached_output = None
        super().reset()

    def tick(self, *args, **kwargs):
        try:
            return super().tick(*args, **kwargs)
        finally:
            self._cached_output = None

    def quiescent(self):
        # A drained FIFO alone is insufficient: the last-flit handshake may
        # still need to clear registered arbiter snapshots on the next edge.
        return (not any(f.queue for f in self.fifos)
                and not any(self.last_handshake)
                and not any(self.arbiter_locked)
                and not any(self.request_snapshot)
                and not any(self.locked_requests))


class FastMesh(Mesh):
    def __init__(self, width, height, fifo_depth=4, interconnect=None):
        super().__init__(width, height, fifo_depth, interconnect)
        self.routers = [FastFlooRouter(x, y, fifo_depth)
                        for y in range(height) for x in range(width)]

    def tick(self, local_injections=None, local_ready=None):
        if (not local_injections and not self.has_link_data
                and all(r.quiescent() for r in self.routers)):
            # Still advance link credit/serialization deadlines and absolute
            # time exactly, including credits returning after the last flit.
            self.advance_idle(1)
            return {i: True for i in range(len(self.routers))}, {}
        return super().tick(local_injections, local_ready)
