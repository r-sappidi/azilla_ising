"""Explicit gather, remote publication, and filtered H1-to-H0 cache fill plan."""
from dataclasses import dataclass
from .scheduler import compile_cores_only_schedule


@dataclass(frozen=True)
class CoreStatePublicationPlan:
    gather_cycles: int
    remote_publications: tuple
    local_writes_by_h1: tuple

    @property
    def local_fill_cycles(self):
        return max((len(writes) for writes in self.local_writes_by_h1), default=0)


def core_state_publication_plan(geometry, records):
    compiled=compile_cores_only_schedule(geometry,records)
    by_h1=[[] for _ in range(geometry.node_count)]
    for global_h0,jobs in enumerate(compiled.h0):
        sources=sorted({job.block_b for job in jobs})
        h1=global_h0//geometry.h0_per_h1
        local_h0=global_h0%geometry.h0_per_h1
        by_h1[h1].extend((local_h0,source) for source in sources)
    return CoreStatePublicationPlan(
        geometry.blocks_per_h1,tuple(compiled.state_publications),
        tuple(tuple(writes) for writes in by_h1))
