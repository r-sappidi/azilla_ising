"""Command line entry point for model validation utilities."""

from __future__ import annotations

import argparse

from .workload import (
    BlockOccupancyDataset, Geometry, IsingDataset, load_schedule,
    validate_schedule,
)
from .performance import (
    DirectPerformanceModel, PerformanceConfig, RamulatorPerformanceModel,
)
from .events import (
    EventCompressedPerformanceModel, EventPerformanceConfig,
    EventTimingProfile,
)
from .exact_events import RamulatorEventPerformanceModel
from .mapping_adapter import (
    MappingArtifact, map_sparse_dataset, write_permuted_dataset,
)
from .scheduler import compile_schedule


def main() -> None:
    parser = argparse.ArgumentParser(prog="azilla-cycle-model")
    subparsers = parser.add_subparsers(dest="command", required=True)
    mapping = subparsers.add_parser("map-graph")
    mapping.add_argument("--dataset", required=True)
    mapping.add_argument("--output-dir", required=True)
    mapping.add_argument("--write-permuted-dataset")
    mapping.add_argument("--mesh-x", type=int, required=True)
    mapping.add_argument("--mesh-y", type=int, required=True)
    mapping.add_argument("--h0-per-h1", type=int, required=True)
    mapping.add_argument("--cores-per-h0", type=int, required=True)
    mapping.add_argument("--device", default="auto")
    mapping.add_argument("--require-cuda", action="store_true")
    mapping.add_argument("--macro-iterations", type=int, default=8)
    mapping.add_argument("--micro-iterations", type=int, default=6)
    mapping.add_argument("--candidate-swaps", type=int, default=64)
    mapping.add_argument("--seed", type=int)
    validate = subparsers.add_parser("validate-schedule")
    validate.add_argument("--dataset", required=True)
    validate.add_argument("--schedule", required=True)
    validate.add_argument("--mesh-x", type=int, required=True)
    validate.add_argument("--mesh-y", type=int, required=True)
    validate.add_argument("--h0-per-h1", type=int, required=True)
    validate.add_argument("--cores-per-h0", type=int, required=True)
    simulate = subparsers.add_parser("simulate-direct")
    simulate.add_argument("--dataset", required=True)
    simulate.add_argument("--schedule")
    simulate.add_argument("--mesh-x", type=int, required=True)
    simulate.add_argument("--mesh-y", type=int, required=True)
    simulate.add_argument("--h0-per-h1", type=int, required=True)
    simulate.add_argument("--cores-per-h0", type=int, required=True)
    simulate.add_argument("--h0-mvms", type=int, default=1)
    simulate.add_argument("--h1-mvms", type=int, default=1)
    simulate.add_argument("--cross-mvms", type=int, default=1)
    simulate.add_argument("--fifo-depth", type=int, default=4)
    simulate.add_argument("--iterations", type=int, default=1)
    simulate.add_argument("--dense", action="store_true")
    simulate.add_argument("--timing-only", action="store_true")
    simulate.add_argument("--max-cycles", type=int, default=1_000_000_000)
    ramulator = subparsers.add_parser("simulate-ramulator")
    for action in simulate._actions[1:]:
        if action.dest in {"dense", "timing_only"}:
            ramulator.add_argument(action.option_strings[0], action="store_true")
        else:
            kwargs = {"dest": action.dest, "required": action.required}
            if action.type is not None:
                kwargs["type"] = action.type
            if action.default is not None:
                kwargs["default"] = action.default
            ramulator.add_argument(*action.option_strings, **kwargs)
    ramulator.add_argument("--ramulator-library", required=True)
    ramulator.add_argument("--ramulator-config", required=True)
    ramulator.add_argument("--mem-lanes", type=int, default=16)
    ramulator.add_argument("--ticks-per-cycle", type=int, default=40)
    mapped_ramulator = subparsers.add_parser("simulate-mapped-ramulator")
    mapped_ramulator.add_argument("--artifact", required=True)
    mapped_ramulator.add_argument("--dataset", required=True)
    mapped_ramulator.add_argument("--ramulator-library", required=True)
    mapped_ramulator.add_argument("--ramulator-config", required=True)
    mapped_ramulator.add_argument("--h0-mvms", type=int, default=1)
    mapped_ramulator.add_argument("--h1-mvms", type=int, default=1)
    mapped_ramulator.add_argument("--cross-mvms", type=int, default=1)
    mapped_ramulator.add_argument("--fifo-depth", type=int, default=4)
    mapped_ramulator.add_argument("--mem-lanes", type=int, default=16)
    mapped_ramulator.add_argument("--ticks-per-cycle", type=int, default=40)
    mapped_ramulator.add_argument("--iterations", type=int, default=1)
    mapped_ramulator.add_argument("--timing-only", action="store_true")
    mapped_ramulator.add_argument(
        "--max-cycles", type=int, default=1_000_000_000
    )
    events = subparsers.add_parser("simulate-events")
    events.add_argument("--dataset", required=True)
    events.add_argument("--schedule")
    events.add_argument("--mesh-x", type=int, required=True)
    events.add_argument("--mesh-y", type=int, required=True)
    events.add_argument("--h0-per-h1", type=int, required=True)
    events.add_argument("--cores-per-h0", type=int, required=True)
    events.add_argument("--h0-mvms", type=int, default=1)
    events.add_argument("--h1-mvms", type=int, default=1)
    events.add_argument("--cross-mvms", type=int, default=1)
    events.add_argument("--fifo-depth", type=int, default=4)
    events.add_argument("--iterations", type=int, default=1)
    events.add_argument("--dense", action="store_true")
    events.add_argument("--h0-block-cycles", type=int, default=67)
    events.add_argument("--h1-block-cycles", type=int, default=67)
    events.add_argument("--cross-block-cycles", type=int, default=67)
    mapped_events = subparsers.add_parser("simulate-mapped-events")
    mapped_events.add_argument("--artifact", required=True)
    mapped_events.add_argument("--h0-mvms", type=int, default=1)
    mapped_events.add_argument("--h1-mvms", type=int, default=1)
    mapped_events.add_argument("--cross-mvms", type=int, default=1)
    mapped_events.add_argument("--fifo-depth", type=int, default=4)
    mapped_events.add_argument("--iterations", type=int, default=1)
    mapped_events.add_argument("--h0-block-cycles", type=int, default=67)
    mapped_events.add_argument("--h1-block-cycles", type=int, default=67)
    mapped_events.add_argument("--cross-block-cycles", type=int, default=67)
    exact_events = subparsers.add_parser("simulate-exact-events")
    exact_events.add_argument("--dataset", required=True)
    exact_events.add_argument("--schedule")
    exact_events.add_argument("--mesh-x", type=int, required=True)
    exact_events.add_argument("--mesh-y", type=int, required=True)
    exact_events.add_argument("--h0-per-h1", type=int, required=True)
    exact_events.add_argument("--cores-per-h0", type=int, required=True)
    exact_events.add_argument("--h0-mvms", type=int, default=1)
    exact_events.add_argument("--h1-mvms", type=int, default=1)
    exact_events.add_argument("--cross-mvms", type=int, default=1)
    exact_events.add_argument("--fifo-depth", type=int, default=4)
    exact_events.add_argument("--ramulator-library", required=True)
    exact_events.add_argument("--ramulator-config", required=True)
    exact_events.add_argument("--mem-lanes", type=int, default=16)
    exact_events.add_argument("--ticks-per-cycle", type=int, default=40)
    exact_events.add_argument("--idle-refresh-period-ticks", type=int, default=0)
    exact_events.add_argument("--max-cycles", type=int, default=1_000_000_000)
    mapped_exact_events = subparsers.add_parser(
        "simulate-mapped-exact-events"
    )
    mapped_exact_events.add_argument("--artifact", required=True)
    mapped_exact_events.add_argument("--dataset", required=True)
    for action in exact_events._actions[7:]:
        kwargs = {"dest": action.dest, "required": action.required}
        if action.type is not None:
            kwargs["type"] = action.type
        if action.default is not None:
            kwargs["default"] = action.default
        mapped_exact_events.add_argument(*action.option_strings, **kwargs)
    args = parser.parse_args()

    if args.command == "map-graph":
        geometry = Geometry(
            args.mesh_x, args.mesh_y, args.h0_per_h1, args.cores_per_h0
        )
        artifact = map_sparse_dataset(
            args.dataset,
            geometry,
            device=args.device,
            macro_iterations=args.macro_iterations,
            micro_iterations=args.micro_iterations,
            candidate_swaps=args.candidate_swaps,
            seed=args.seed,
            require_cuda=args.require_cuda,
        )
        artifact.save(args.output_dir)
        if args.write_permuted_dataset:
            write_permuted_dataset(
                args.dataset, args.write_permuted_dataset, artifact
            )
        counts = compile_schedule(geometry, artifact.schedule()).counts
        print(
            f"PASS mode=map-graph spins={geometry.spin_count} "
            f"blocks={len(artifact.block_pairs)} "
            f"h0={counts[0]} h1={counts[1]} cross={counts[2]} "
            f"artifact={args.output_dir}"
        )
        if args.write_permuted_dataset:
            print(f"permuted_dataset={args.write_permuted_dataset}")
    elif args.command == "validate-schedule":
        dataset = BlockOccupancyDataset.load(args.dataset)
        schedule = load_schedule(args.schedule)
        geometry = Geometry(
            args.mesh_x, args.mesh_y, args.h0_per_h1, args.cores_per_h0
        )
        validate_schedule(dataset, geometry, schedule)
        print(
            f"PASS schedule records={len(schedule)} spins={geometry.spin_count} "
            f"nodes={geometry.node_count}"
        )
    elif args.command in {
        "simulate-direct", "simulate-ramulator", "simulate-mapped-ramulator",
    }:
        dataset = IsingDataset.load(args.dataset)
        if args.command == "simulate-mapped-ramulator":
            artifact = MappingArtifact.load(args.artifact)
            geometry = artifact.geometry
            schedule = artifact.schedule()
            sparse = True
        else:
            geometry = Geometry(
                args.mesh_x, args.mesh_y, args.h0_per_h1, args.cores_per_h0
            )
            schedule = load_schedule(args.schedule) if args.schedule else None
            sparse = not args.dense
        if schedule is not None:
            validate_schedule(dataset, geometry, schedule)
        performance_config = PerformanceConfig(
            h0_mvm_count=args.h0_mvms,
            h1_mvm_count=args.h1_mvms,
            cross_mvm_count=args.cross_mvms,
            fifo_depth=args.fifo_depth,
            max_cycles=args.max_cycles,
            timing_only=args.timing_only,
        )
        if args.command == "simulate-direct":
            model = DirectPerformanceModel(geometry, dataset, performance_config)
        else:
            model = RamulatorPerformanceModel(
                geometry, dataset,
                ramulator_library=args.ramulator_library,
                ramulator_config=args.ramulator_config,
                dataset_path=args.dataset,
                mem_lanes=args.mem_lanes,
                ticks_per_cycle=args.ticks_per_cycle,
                config=performance_config,
            )
        try:
            result = model.run(
                iterations=args.iterations, schedule=schedule,
                sparse=sparse,
            )
        finally:
            if isinstance(model, RamulatorPerformanceModel):
                model.close()
        print(
            f"PASS mode={args.command.removeprefix('simulate-')} "
            f"spins={geometry.spin_count} "
            f"initialization_cycles={result.initialization_cycles} "
            f"total_cycles={result.total_cycles}"
        )
        for iteration in result.iterations:
            print(f"iteration={iteration.iteration} cycles={iteration.cycles}")
        counters = result.counters
        print(
            f"noc injected_flits={counters.injected_flits} "
            f"ejected_flits={counters.ejected_flits} "
            f"physical_link_flits={counters.physical_link_flits} "
            f"injection_stalls={counters.injection_stalls}"
        )
    elif args.command in {"simulate-events", "simulate-mapped-events"}:
        if args.command == "simulate-mapped-events":
            artifact = MappingArtifact.load(args.artifact)
            dataset = artifact.occupancy_dataset()
            geometry = artifact.geometry
            schedule = artifact.schedule()
            validate_schedule(dataset, geometry, schedule)
            sparse = True
        else:
            dataset = BlockOccupancyDataset.load(args.dataset)
            geometry = Geometry(
                args.mesh_x, args.mesh_y, args.h0_per_h1, args.cores_per_h0
            )
            schedule = load_schedule(args.schedule) if args.schedule else None
            if schedule is not None:
                validate_schedule(dataset, geometry, schedule)
            sparse = not args.dense
        profile = EventTimingProfile(
            h0_block_cycles=args.h0_block_cycles,
            h1_block_cycles=args.h1_block_cycles,
            cross_block_cycles=args.cross_block_cycles,
        )
        result = EventCompressedPerformanceModel(
            geometry, dataset,
            EventPerformanceConfig(
                h0_mvm_count=args.h0_mvms,
                h1_mvm_count=args.h1_mvms,
                cross_mvm_count=args.cross_mvms,
                fifo_depth=args.fifo_depth,
                profile=profile,
            ),
        ).run(
            schedule=schedule, sparse=sparse,
            iterations=args.iterations,
        )
        print(
            f"PASS mode={args.command.removeprefix('simulate-')} "
            f"accuracy={result.accuracy} "
            f"profile={result.calibration} "
            f"spins={geometry.spin_count} "
            f"initialization_cycles={result.initialization_cycles} "
            f"iteration_cycles={result.iteration_cycles} "
            f"total_cycles={result.total_cycles}"
        )
        print(
            f"jobs h0={result.scheduled_h0} h1={result.scheduled_h1} "
            f"cross={result.scheduled_cross}"
        )
        print(
            f"compression active_network_cycles={result.network_active_cycles} "
            f"skipped_cycles={result.skipped_cycles}"
        )
        print(
            f"noc injected_flits={result.injected_flits} "
            f"ejected_flits={result.ejected_flits} "
            f"physical_link_flits={result.physical_link_flits}"
        )
    elif args.command in {
        "simulate-exact-events", "simulate-mapped-exact-events",
    }:
        if args.command == "simulate-mapped-exact-events":
            artifact = MappingArtifact.load(args.artifact)
            dataset = artifact.occupancy_dataset()
            geometry = artifact.geometry
            schedule = artifact.schedule()
            validate_schedule(dataset, geometry, schedule)
        else:
            dataset = BlockOccupancyDataset.load(args.dataset)
            geometry = Geometry(
                args.mesh_x, args.mesh_y, args.h0_per_h1, args.cores_per_h0
            )
            schedule = load_schedule(args.schedule) if args.schedule else None
            if schedule is not None:
                validate_schedule(dataset, geometry, schedule)
        config = PerformanceConfig(
            h0_mvm_count=args.h0_mvms,
            h1_mvm_count=args.h1_mvms,
            cross_mvm_count=args.cross_mvms,
            fifo_depth=args.fifo_depth,
            timing_only=True,
            max_cycles=args.max_cycles,
        )
        model = RamulatorEventPerformanceModel(
            geometry,
            dataset,
            dataset_path=args.dataset,
            ramulator_library=args.ramulator_library,
            ramulator_config=args.ramulator_config,
            mem_lanes=args.mem_lanes,
            ticks_per_cycle=args.ticks_per_cycle,
            idle_tick_modulus=(
                args.idle_refresh_period_ticks
                if args.idle_refresh_period_ticks else None
            ),
            config=config,
        )
        try:
            result = model.run(schedule=schedule)
        finally:
            model.close()
        print(
            f"PASS mode={args.command.removeprefix('simulate-')} "
            f"accuracy={result.accuracy} spins={geometry.spin_count} "
            f"initialization_cycles={result.initialization_cycles} "
            f"iteration_cycles={result.iteration_cycles} "
            f"total_cycles={result.total_cycles}"
        )
        print(
            f"jobs h0={result.scheduled_h0} h1={result.scheduled_h1} "
            f"cross={result.scheduled_cross}"
        )
        counters = result.counters
        print(
            f"noc injected_flits={counters.injected_flits} "
            f"ejected_flits={counters.ejected_flits} "
            f"physical_link_flits={counters.physical_link_flits} "
            f"injection_stalls={counters.injection_stalls} "
            f"ejection_stalls={counters.ejection_stalls} "
            f"link_stalls={counters.link_stalls}"
        )


if __name__ == "__main__":
    main()
