"""Opt-in exact-equivalent implementation; validation coverage is external.

Run the usual exact-event CLI arguments with this module instead of ``cli``.
No active process or frozen reference snapshot is modified by this entrypoint.
"""
def activate():
    from . import exact_events, core_iteration_pipeline, cross
    from .fast_memory import FastDramWeightStreamer, FastRamulatorBackend
    from .fast_compute import FastHierarchyNode
    from .fast_core import FastCoreIteration
    from .fast_noc import FastMesh
    exact_events.DramWeightStreamer = FastDramWeightStreamer
    exact_events.RamulatorBackend = FastRamulatorBackend
    exact_events.HierarchyNode = FastHierarchyNode
    cross.HierarchyNode = FastHierarchyNode
    core_iteration_pipeline.DramWeightStreamer = FastDramWeightStreamer
    core_iteration_pipeline.CoreIteration = FastCoreIteration
    exact_events.Mesh = FastMesh
    core_iteration_pipeline.Mesh = FastMesh


def main():
    import sys
    # This implementation is explicitly timing-only, not a replacement for
    # arithmetic, calibrated, or RTL validation commands.
    if len(sys.argv) < 2 or sys.argv[1] not in (
            'simulate-exact-events', 'simulate-mapped-exact-events'):
        raise SystemExit('fast_cli supports only exact-event timing commands')
    activate()
    from .cli import main as cli_main
    cli_main()


if __name__ == '__main__':
    main()
