#!/usr/bin/env python3
"""Generate paper-oriented hybrid policy/oracle comparison figures."""

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--summary", type=Path,
    default=Path("results/paper_benchmarks/hybrid_oracle/summary.csv"),
)
parser.add_argument(
    "--output-dir", type=Path,
    default=Path("plots/paper/hybrid_oracle"),
)
args = parser.parse_args()
rows = list(csv.DictReader(args.summary.open()))
if not rows:
    raise SystemExit("oracle summary is empty")
args.output_dir.mkdir(parents=True, exist_ok=True)
labels = [f"{r['dataset']}\n{r['mapping'].replace('_', ' ')}" for r in rows]
x = list(range(len(rows)))

fig, ax = plt.subplots(figsize=(max(10, len(rows) * .55), 4.6))
width = .38
hybrid = [float(r["hybrid_speedup_over_fixed_oracle"]) for r in rows]
searched = [float(r["searched_speedup_over_fixed_oracle"]) for r in rows]
ax.bar([i-width/2 for i in x], hybrid, width, label="Static hybrid",
       color="#2E8B57")
ax.bar([i+width/2 for i in x], searched, width,
       label="Best searched placement", color="#775DA6")
ax.axhline(1, color="black", linewidth=1)
ax.set_ylabel("Speedup over best fixed policy")
ax.set_xticks(x, labels, rotation=55, ha="right", fontsize=8)
ax.legend(frameon=False, ncol=2)
ax.grid(axis="y", alpha=.25)
fig.tight_layout()
for extension in ("png", "pdf"):
    fig.savefig(args.output_dir / f"hybrid_oracle_speedup.{extension}", dpi=220)
plt.close(fig)

fig, ax = plt.subplots(figsize=(max(10, len(rows) * .55), 4.2))
regret = [100 * float(r["hybrid_regret_to_best_searched"]) for r in rows]
colors = ["#D95319" if value > 5 else "#2E8B57" for value in regret]
ax.bar(x, regret, color=colors)
ax.axhline(5, color="black", linestyle="--", linewidth=1,
           label="5% target")
ax.set_ylabel("Static-hybrid regret to best searched placement (%)")
ax.set_xticks(x, labels, rotation=55, ha="right", fontsize=8)
ax.legend(frameon=False)
ax.grid(axis="y", alpha=.25)
fig.tight_layout()
for extension in ("png", "pdf"):
    fig.savefig(args.output_dir / f"hybrid_oracle_regret.{extension}", dpi=220)
plt.close(fig)

fig, ax = plt.subplots(figsize=(max(10, len(rows) * .55), 4.2))
interaction = [int(r["interaction_blocks"]) for r in rows]
static_fraction = [int(r["hybrid_core_pairs"])/n if n else 0
                   for r, n in zip(rows, interaction)]
searched_fraction = [int(r["searched_core_pairs"])/n if n else 0
                     for r, n in zip(rows, interaction)]
ax.plot(x, static_fraction, "o-", label="Static hybrid", color="#2E8B57")
ax.plot(x, searched_fraction, "s-", label="Best searched placement",
        color="#775DA6")
ax.set_ylabel("Fraction of interaction blocks placed at cores")
ax.set_ylim(-.03, 1.03)
ax.set_xticks(x, labels, rotation=55, ha="right", fontsize=8)
ax.legend(frameon=False, ncol=2)
ax.grid(axis="y", alpha=.25)
fig.tight_layout()
for extension in ("png", "pdf"):
    fig.savefig(args.output_dir / f"hybrid_oracle_core_fraction.{extension}", dpi=220)
