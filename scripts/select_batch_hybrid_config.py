#!/usr/bin/env python3
"""Freeze one batch-scheduler configuration from tuning-set results."""

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--summary", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
rows = list(csv.DictReader(args.summary.open()))
grouped = defaultdict(list)
for row in rows:
    grouped[(float(row["cir_cost_scale"]), int(row["traffic_weight"]))].append(row)
ranked = []
for (scale, weight), points in grouped.items():
    speedups = [float(row["speedup_over_fixed_oracle"]) for row in points]
    regrets = [float(row["regret_to_searched"]) for row in points]
    ranked.append({
        "cir_cost_scale": scale,
        "traffic_weight": weight,
        "geomean_speedup": math.exp(
            sum(math.log(value) for value in speedups) / len(speedups)
        ),
        "minimum_speedup": min(speedups),
        "maximum_regret": max(regrets),
        "points": len(points),
    })
ranked.sort(key=lambda row: (
    -row["geomean_speedup"], -row["minimum_speedup"],
    row["maximum_regret"], row["cir_cost_scale"], row["traffic_weight"],
))
selected = dict(ranked[0])
selected["selection_rule"] = (
    "maximum tuning-set geometric-mean speedup; ties prefer worst-case "
    "speedup then regret"
)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(selected, indent=2) + "\n")
print(json.dumps(selected, indent=2))
