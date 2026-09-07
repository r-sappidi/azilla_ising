"""Structured exports for exact-event mapping-performance diagnostics."""

from __future__ import annotations

import csv
from dataclasses import asdict
import json
from pathlib import Path
from typing import Iterable

from .events import EventPerformanceConfig, EventPerformanceResult
from .exact_events import ExactEventResult, Transfer
from .workload import Geometry


def _output_path(prefix: str | Path, suffix: str) -> Path:
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    return prefix.with_name(prefix.name + suffix)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty metrics table {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_exact_event_metrics(
    prefix: str | Path,
    result: ExactEventResult,
    geometry: Geometry,
) -> dict[str, Path]:
    """Write summary JSON plus NoC, node, and DRAM counter tables."""

    noc_rows = []
    for resource in result.noc_resources:
        row = asdict(resource)
        row["window_utilization"] = (
            resource.accepted_flits / result.iteration_cycles
            if result.iteration_cycles else 0.0
        )
        row["acceptance_fraction"] = resource.acceptance_fraction
        row["backpressure"] = resource.backpressure
        noc_rows.append(row)

    node_rows = [asdict(node) for node in result.nodes]
    dram_rows = []
    for dram in result.dram:
        row = asdict(dram)
        attempts = dram.accepted_requests + dram.rejected_requests
        row["request_rejection_fraction"] = (
            dram.rejected_requests / attempts if attempts else 0.0
        )
        dram_rows.append(row)

    counters = result.counters
    if result.execution_mode == "cores-only":
        effective_mvm_engines = {
            "per_h0_destination_cores": geometry.cores_per_h0,
            "h1": 0,
            "cross": 0,
            "system_total": geometry.total_blocks,
        }
    else:
        engines_by_level = {
            level: [row.engines for row in result.dram if row.level == level]
            for level in ("h0", "h1", "cross")
        }
        effective_mvm_engines = {
            "per_h0": (engines_by_level["h0"][0]
                       if engines_by_level["h0"] else 0),
            "per_h1_local": (engines_by_level["h1"][0]
                             if engines_by_level["h1"] else 0),
            "per_h1_cross": (engines_by_level["cross"][0]
                             if engines_by_level["cross"] else 0),
            "system_total": sum(row.engines for row in result.dram),
        }
    summary = {
        "schema_version": 1,
        "accuracy": result.accuracy,
        "execution_mode": result.execution_mode,
        "cores_only_ablation": {
            "unordered_interaction_blocks": result.unordered_interaction_blocks,
            "directed_core_jobs": result.directed_core_jobs,
            "weight_block_reads": result.weight_block_reads,
            "logical_weight_blocks_stored": result.logical_weight_blocks_stored,
            "remote_state_packets": result.remote_state_packets,
            "core_weight_buffers": result.core_weight_buffers,
            "pipeline_contract": result.core_pipeline_contract,
            "top_level_partial_packets": counters.type_flits[1] // 4,
        },
        "effective_mvm_engines": effective_mvm_engines,
        "geometry": {
            "mesh_x": geometry.mesh_x,
            "mesh_y": geometry.mesh_y,
            "h0_per_h1": geometry.h0_per_h1,
            "cores_per_h0": geometry.cores_per_h0,
            "spins_per_core": 32,
            "spin_count": geometry.spin_count,
        },
        "timing_cycles": {
            "initialization": result.initialization_cycles,
            "iteration": result.iteration_cycles,
            "total": result.total_cycles,
        },
        "package": {
            "chiplet_link_latency_cycles": (
                result.chiplet_link_latency_cycles
            ),
        },
        "interconnect": asdict(result.interconnect),
        "scheduled_jobs": {
            "h0": result.scheduled_h0,
            "h1": result.scheduled_h1,
            "cross": result.scheduled_cross,
            "total": (
                result.scheduled_h0
                + result.scheduled_h1
                + result.scheduled_cross
            ),
        },
        "noc": {
            "injected_flits": counters.injected_flits,
            "ejected_flits": counters.ejected_flits,
            "physical_link_flits": counters.physical_link_flits,
            "injection_stalls": counters.injection_stalls,
            "ejection_stalls": counters.ejection_stalls,
            "link_stalls": counters.link_stalls,
            "state_flits": counters.type_flits[0],
            "partial_flits": counters.type_flits[1],
            "epoch_done_flits": counters.type_flits[2],
            "other_flits": counters.type_flits[3],
            "average_hops": result.average_hops,
            "hop_histogram": {
                str(hops): flits for hops, flits in result.hop_histogram
            },
        },
        "nodes": node_rows,
        "dram_systems": dram_rows,
        "noc_resources": noc_rows,
    }

    paths = {
        "summary": _output_path(prefix, "_summary.json"),
        "noc": _output_path(prefix, "_noc.csv"),
        "nodes": _output_path(prefix, "_nodes.csv"),
        "dram": _output_path(prefix, "_dram.csv"),
    }
    with paths["summary"].open("w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _write_csv(paths["noc"], noc_rows)
    _write_csv(paths["nodes"], node_rows)
    _write_csv(paths["dram"], dram_rows)
    return paths


def write_event_performance_metrics(
    prefix: str | Path,
    result: EventPerformanceResult,
    geometry: Geometry,
    config: EventPerformanceConfig,
) -> dict[str, Path]:
    """Export calibrated-event timing and exact-replay traffic counters.

    The file names intentionally differ from exact-event exports so that a
    result directory cannot silently confuse calibrated endpoint timing with
    Ramulator-derived timing.
    """

    noc_rows = []
    for resource in result.noc_resources:
        row = asdict(resource)
        row["window_utilization"] = (
            resource.accepted_flits / result.iteration_cycles
            if result.iteration_cycles else 0.0
        )
        row["acceptance_fraction"] = resource.acceptance_fraction
        row["backpressure"] = resource.backpressure
        noc_rows.append(row)
    node_rows = [asdict(node) for node in result.nodes]
    work_rows = [asdict(resource) for resource in result.work_resources]

    type_flits = {
        "state_flits": sum(row.state_flits for row in result.noc_resources
                           if row.scope == "inject"),
        "partial_flits": sum(row.partial_flits for row in result.noc_resources
                             if row.scope == "inject"),
        "epoch_done_flits": sum(
            row.epoch_done_flits for row in result.noc_resources
            if row.scope == "inject"
        ),
        "other_flits": sum(row.other_flits for row in result.noc_resources
                           if row.scope == "inject"),
    }
    summary = {
        "schema_version": 1,
        "model": "calibrated-event-compressed",
        "accuracy": result.accuracy,
        "calibration": result.calibration,
        "execution_mode": config.execution_mode,
        "cores_only_ablation": {
            "unordered_interaction_blocks": result.unordered_interaction_blocks,
            "directed_core_jobs": result.directed_core_jobs,
            "weight_block_reads": result.weight_block_reads,
            "logical_weight_blocks_stored": result.logical_weight_blocks_stored,
            "remote_state_packets": result.remote_state_packets,
            "top_level_partial_packets": sum(
                row.partial_flits for row in result.noc_resources
                if row.scope == "inject"
            ) // 4,
        },
        "geometry": {
            "mesh_x": geometry.mesh_x,
            "mesh_y": geometry.mesh_y,
            "h0_per_h1": geometry.h0_per_h1,
            "cores_per_h0": geometry.cores_per_h0,
            "spins_per_core": 32,
            "spin_count": geometry.spin_count,
        },
        "timing_cycles": {
            "initialization": result.initialization_cycles,
            "iteration": result.iteration_cycles,
            "total": result.total_cycles,
            "network_active": result.network_active_cycles,
            "network_skipped": result.skipped_cycles,
        },
        "timing_profile": asdict(config.profile),
        "mvm_engines": {
            "h0": config.h0_mvm_count,
            "h1": config.h1_mvm_count,
            "cross": config.cross_mvm_count,
        },
        "effective_mvm_engines": (
            {
                "per_h0_destination_cores": geometry.cores_per_h0,
                "h1": 0,
                "cross": 0,
                "system_total": geometry.total_blocks,
            }
            if config.execution_mode == "cores-only" else
            ({
                "per_h0_destination_cores": geometry.cores_per_h0,
                "per_h0_cir": config.h0_mvm_count,
                "h1": config.h1_mvm_count,
                "cross": config.cross_mvm_count,
                "system_total": geometry.total_blocks + geometry.node_count * (
                    geometry.h0_per_h1 * config.h0_mvm_count
                    + config.h1_mvm_count + config.cross_mvm_count
                ),
            } if config.execution_mode == "hybrid" else
            {
                "per_h0": config.h0_mvm_count,
                "h1": config.h1_mvm_count,
                "cross": config.cross_mvm_count,
                "system_total": geometry.node_count * (
                    geometry.h0_per_h1 * config.h0_mvm_count
                    + config.h1_mvm_count + config.cross_mvm_count
                ),
            })
        ),
        "package": {
            "chiplet_link_latency_cycles": (
                result.chiplet_link_latency_cycles
            ),
        },
        "interconnect": asdict(result.interconnect),
        "scheduled_jobs": {
            "h0": result.scheduled_h0,
            "h1": result.scheduled_h1,
            "cross": result.scheduled_cross,
            "total": (
                result.scheduled_h0
                + result.scheduled_h1
                + result.scheduled_cross
            ),
        },
        "estimated_dram_requests": sum(
            row.estimated_dram_requests for row in result.work_resources
        ),
        "noc": {
            "injected_flits": result.injected_flits,
            "ejected_flits": result.ejected_flits,
            "physical_link_flits": result.physical_link_flits,
            "injection_stalls": result.injection_stalls,
            "link_stalls": result.link_stalls,
            "average_hops": result.average_hops,
            "hop_histogram": {
                str(hops): flits for hops, flits in result.hop_histogram
            },
            **type_flits,
        },
        "nodes": node_rows,
        "work_resources": work_rows,
        "noc_resources": noc_rows,
    }
    paths = {
        "summary": _output_path(prefix, "_event_summary.json"),
        "noc": _output_path(prefix, "_event_noc.csv"),
        "nodes": _output_path(prefix, "_event_nodes.csv"),
        "work": _output_path(prefix, "_event_work.csv"),
    }
    with paths["summary"].open("w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _write_csv(paths["noc"], noc_rows)
    _write_csv(paths["nodes"], node_rows)
    _write_csv(paths["work"], work_rows)
    return paths


def write_transfer_trace(
    path: str | Path,
    transfers: Iterable[Transfer],
    geometry: Geometry,
) -> Path:
    """Write accepted injection, hop, and ejection records in RTL CSV form."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "cycle", "event", "scope", "node", "x", "y", "direction",
        "packet_type", "source_id", "epoch", "block_id", "dest_x",
        "dest_y", "last", "valid", "ready", "inflight",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for transfer in sorted(transfers):
            (
                cycle, scope, node, direction, packet_type, source_id,
                epoch, block_id, dest_x, dest_y, last,
            ) = transfer
            writer.writerow({
                "cycle": cycle,
                "event": "accept",
                "scope": scope,
                "node": node,
                "x": node % geometry.mesh_x,
                "y": node // geometry.mesh_x,
                "direction": direction,
                "packet_type": packet_type,
                "source_id": source_id,
                "epoch": epoch,
                "block_id": block_id,
                "dest_x": dest_x,
                "dest_y": dest_y,
                "last": last,
                "valid": 1,
                "ready": 1,
                "inflight": "",
            })
    return path
