#!/usr/bin/env python3
"""Freeze a matched three-family 64K/128K/256K width experiment plan."""
import argparse
import json
from pathlib import Path
from run_memory_width_sweep import digest, dump, ROOT


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--configs-manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--certificates", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    source = ROOT / "results/paper_canonical/datasets/manifest.json"
    datasets = json.loads(source.read_text())["datasets"]
    names = {f"{family}_n{n}{seed}" for n in (65536, 131072, 262144)
             for family, seed in (("toroidal_d4", ""), ("uniform_d16", "_s1"),
                                  ("community_d16", "_s1"))}
    workloads = []
    for d in datasets:
        if d["name"] not in names:
            continue
        g = d["geometry"]
        arguments = ["--dataset", d["dataset"]]
        for option, key in (("mesh-x", "mesh_x"), ("mesh-y", "mesh_y"),
                            ("h0-per-h1", "h0_per_h1"), ("cores-per-h0", "cores_per_h0")):
            arguments += ["--" + option, str(g[key])]
        arguments += ["--h0-mvms", "4", "--h1-mvms", "2", "--cross-mvms", "4",
                      "--idle-refresh-period-ticks", "7600"]
        workloads.append({"name": d["name"], "arguments": arguments,
                          "input_sha256": {d["dataset"]: digest(ROOT / d["dataset"])}})
    if {w["name"] for w in workloads} != names:
        raise ValueError("canonical dataset matrix incomplete")
    lib = "build/cycle_model_ramulator/libazilla_ramulator.so"
    dump(args.output, {"configs_manifest": str(args.configs_manifest.resolve()),
         "ramulator_library": lib, "ramulator_library_sha256": digest(ROOT / lib),
         "certificates": {str(w): {m: str(args.certificates.resolve() / f"x{w}_{m}.json")
                                    for m in ("cir", "cores-only")} for w in (32, 64, 128)},
         "workloads": workloads,
         "scope": "identity mapping; 54 matched runs; large geometries are projections"})
    print(f"Prepared {len(workloads)*6} gated points: {args.output}")


if __name__ == "__main__":
    main()
