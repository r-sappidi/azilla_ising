#!/usr/bin/env python3
"""Screen static-overflow placements and exact-replay provisioning finalists."""
from __future__ import annotations

import argparse, csv, json, time
from pathlib import Path

from azilla_cycle_model.dynamic_scheduler import select_batch_balanced_core_pairs
from azilla_cycle_model.events import EventCompressedPerformanceModel, EventPerformanceConfig
from azilla_cycle_model.exact_events import RamulatorEventPerformanceModel
from azilla_cycle_model.metrics import write_exact_event_metrics
from azilla_cycle_model.performance import PerformanceConfig
from azilla_cycle_model.workload import BlockOccupancyDataset, Geometry, ScheduledBlock

ROOT = Path(__file__).resolve().parents[1]
ENGINE_CONFIGS = ((4, 2, 4), (4, 4, 8), (4, 8, 16))
SCALES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
TRAFFIC_WEIGHTS = (0, 1, 2)


def load_entries(manifest: Path, names: set[str]):
    entries = {e["name"]: e for e in json.loads(manifest.read_text())["datasets"]}
    return [entries[name] for name in sorted(names)]


def workload(entry):
    geometry = Geometry(**entry["geometry"])
    path = ROOT / entry["dataset"]
    dataset = BlockOccupancyDataset.load(path)
    schedule = [ScheduledBlock(a, b) for a, b in sorted(dataset.active_block_pairs())]
    return geometry, path, dataset, schedule


def screen(entries, output: Path):
    rows = []
    for entry in entries:
        geometry, _, dataset, schedule = workload(entry)
        for h0, h1, cross in ENGINE_CONFIGS:
            for scale in SCALES:
                for traffic in TRAFFIC_WEIGHTS:
                    started = time.perf_counter()
                    placement = select_batch_balanced_core_pairs(
                        geometry, schedule, h0_mvm_count=h0, h1_mvm_count=h1,
                        cross_mvm_count=cross, cir_cost_scale=scale,
                        traffic_cycle_weight=traffic,
                    )
                    result = EventCompressedPerformanceModel(
                        geometry, dataset, EventPerformanceConfig(
                            h0_mvm_count=h0, h1_mvm_count=h1,
                            cross_mvm_count=cross, execution_mode="hybrid",
                        ),
                    ).run(schedule=schedule, hybrid_core_pairs=placement.core_pairs)
                    row = dict(
                        dataset=entry["name"], h0_mvms=h0, h1_mvms=h1,
                        cross_mvms=cross, cir_cost_scale=scale,
                        traffic_weight=traffic,
                        iteration_cycles=result.iteration_cycles,
                        core_pairs=len(placement.core_pairs),
                        cir_pairs=len(placement.cir_pairs),
                        weight_block_reads=result.weight_block_reads,
                        physical_link_flits=result.physical_link_flits,
                        wall_seconds=time.perf_counter()-started,
                        accuracy=result.accuracy,
                    )
                    rows.append(row)
                    print("SCREEN", entry["name"], h0, h1, cross, scale,
                          traffic, result.iteration_cycles, flush=True)
    output.mkdir(parents=True, exist_ok=True)
    with (output/"screen.csv").open("w", newline="") as f:
        w=csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def exact(entries, output: Path):
    screen_rows=list(csv.DictReader((output/"screen.csv").open()))
    rows=[]
    for entry in entries:
        geometry, path, dataset, schedule = workload(entry)
        for h0,h1,cross in ENGINE_CONFIGS:
            candidates=[r for r in screen_rows if r["dataset"]==entry["name"] and
                        (int(r["h0_mvms"]),int(r["h1_mvms"]),int(r["cross_mvms"]))==(h0,h1,cross)]
            best=min(candidates,key=lambda r:(int(r["iteration_cycles"]),int(r["physical_link_flits"]),float(r["cir_cost_scale"]),int(r["traffic_weight"])))
            scale=float(best["cir_cost_scale"]); traffic=int(best["traffic_weight"])
            placement=select_batch_balanced_core_pairs(
                geometry,schedule,h0_mvm_count=h0,h1_mvm_count=h1,
                cross_mvm_count=cross,cir_cost_scale=scale,
                traffic_cycle_weight=traffic)
            run_dir=output/"exact"/entry["name"]/f"e{h0}-{h1}-{cross}"
            started=time.perf_counter()
            model=RamulatorEventPerformanceModel(
                geometry,dataset,dataset_path=str(path),
                ramulator_library=str(ROOT/"build/cycle_model_ramulator/libazilla_ramulator.so"),
                ramulator_config=str(ROOT/"tb/ramulator_128x32.yaml"),mem_lanes=16,
                ticks_per_cycle=40,idle_tick_modulus=7600,
                config=PerformanceConfig(h0_mvm_count=h0,h1_mvm_count=h1,
                    cross_mvm_count=cross,fifo_depth=4,timing_only=True,
                    max_cycles=1_000_000_000,execution_mode="hybrid"))
            try: result=model.run(schedule=schedule,hybrid_core_pairs=placement.core_pairs)
            finally: model.close()
            paths=write_exact_event_metrics(run_dir/"metrics",result,geometry)
            row=dict(dataset=entry["name"],h0_mvms=h0,h1_mvms=h1,cross_mvms=cross,
                cir_cost_scale=scale,traffic_weight=traffic,
                iteration_cycles=result.iteration_cycles,
                core_pairs=len(placement.core_pairs),cir_pairs=len(placement.cir_pairs),
                weight_block_reads=result.weight_block_reads,
                physical_link_flits=result.counters.physical_link_flits,
                wall_seconds=time.perf_counter()-started,accuracy=result.accuracy,
                metrics_summary=str(paths["summary"]))
            rows.append(row); print("EXACT",entry["name"],h0,h1,cross,result.iteration_cycles,flush=True)
            (run_dir/"point.json").write_text(json.dumps(row,indent=2,sort_keys=True)+"\n")
    with (output/"exact_summary.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--names",required=True)
    p.add_argument("--stage",choices=("screen","exact"),required=True);a=p.parse_args()
    entries=load_entries(a.manifest,set(a.names.split(",")))
    (screen if a.stage=="screen" else exact)(entries,a.output_dir)

if __name__=="__main__":main()
