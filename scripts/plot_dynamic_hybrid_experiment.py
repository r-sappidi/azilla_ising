#!/usr/bin/env python3
"""Plot dynamic hybrid policies against fixed, static, and searched bounds."""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--dynamic-summary", type=Path,
    default=Path("results/paper_benchmarks/dynamic_hybrid/summary.csv"),
)
parser.add_argument(
    "--oracle-summary", type=Path,
    default=Path("results/paper_benchmarks/hybrid_oracle/summary.csv"),
)
parser.add_argument(
    "--output-dir", type=Path,
    default=Path("plots/paper/dynamic_hybrid"),
)
args = parser.parse_args()
dynamic = list(csv.DictReader(args.dynamic_summary.open()))
oracle_rows = list(csv.DictReader(args.oracle_summary.open()))
oracle = {(r["dataset"], r["mapping"]): r for r in oracle_rows}
grouped = {}
for row in dynamic:
    grouped.setdefault((row["dataset"], row["mapping"]), []).append(row)
keys = sorted(grouped)
if not keys:
    raise SystemExit("dynamic summary is empty")
best = {key: min(grouped[key], key=lambda row: int(row["iteration_cycles"]))
        for key in keys}
labels = [f"{a}\n{b.replace('_', ' ')}" for a, b in keys]
x = list(range(len(keys)))
args.output_dir.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(max(10, len(keys)*.55), 4.6))
width = .27
series = (
    ("Static hybrid", [float(oracle[k]["hybrid_speedup_over_fixed_oracle"])
                       for k in keys], "#2E8B57"),
    ("Best dynamic", [float(best[k]["speedup_over_fixed_oracle"])
                      for k in keys], "#E89C31"),
    ("Best searched", [float(oracle[k]["searched_speedup_over_fixed_oracle"])
                       for k in keys], "#775DA6"),
)
for index, (name, values, color) in enumerate(series):
    ax.bar([value+(index-1)*width for value in x], values, width,
           label=name, color=color)
ax.axhline(1, color="black", linewidth=1)
ax.set_ylabel("Speedup over best fixed policy")
ax.set_xticks(x, labels, rotation=55, ha="right", fontsize=8)
ax.legend(frameon=False, ncol=3); ax.grid(axis="y", alpha=.25)
fig.tight_layout()
for extension in ("png", "pdf"):
    fig.savefig(args.output_dir/f"dynamic_oracle_speedup.{extension}", dpi=220)
plt.close(fig)

policies = sorted({row["policy"] for row in dynamic})
fig, ax = plt.subplots(figsize=(max(8, len(policies)*1.2), 4.2))
data = [[float(row["speedup_over_fixed_oracle"])
         for row in dynamic if row["policy"] == policy] for policy in policies]
ax.boxplot(data, tick_labels=[p.replace("traffic-aware-", "traffic-")
                             for p in policies], showmeans=True)
ax.axhline(1, color="black", linewidth=1)
ax.set_ylabel("Speedup over best fixed policy")
ax.tick_params(axis="x", rotation=25); ax.grid(axis="y", alpha=.25)
fig.tight_layout()
for extension in ("png", "pdf"):
    fig.savefig(args.output_dir/f"dynamic_policy_distribution.{extension}", dpi=220)
plt.close(fig)

with (args.output_dir/"best_dynamic.csv").open("w", newline="") as output:
    writer = csv.DictWriter(output, fieldnames=list(next(iter(best.values()))))
    writer.writeheader(); writer.writerows(best[key] for key in keys)
