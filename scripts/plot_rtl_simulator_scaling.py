#!/usr/bin/env python3
"""Plot paired VCS and Verilator build/simulation wall-time scaling."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]


def load_rows(paths: list[Path]) -> dict[str, dict[int, dict[str, str]]]:
    data: dict[str, dict[int, dict[str, str]]] = {}
    for path in paths:
        with path.open(newline="") as source:
            for row in csv.DictReader(source):
                data.setdefault(row["simulator"], {})[int(row["spins"])] = row
    return data


def plot_metric(
    data: dict[str, dict[int, dict[str, str]]], metric: str, ylabel: str,
    output: Path, title: str,
) -> None:
    common = sorted(set(data["vcs"]) & set(data["verilator"]))
    if not common:
        raise RuntimeError("no spin sizes completed by both simulators")
    fig, axis = plt.subplots(figsize=(7.4, 4.8), constrained_layout=True)
    styles = {
        "vcs": dict(label="VCS", marker="o", color="#0072B2"),
        "verilator": dict(label="Verilator", marker="s", color="#D55E00"),
    }
    for simulator in ("vcs", "verilator"):
        seconds = [float(data[simulator][spins][metric]) for spins in common]
        axis.plot(common, seconds, linewidth=2.2, markersize=6.5, **styles[simulator])
    axis.set_xscale("log", base=2)
    axis.set_yscale("log")
    axis.set_xticks(common, [f"{spins // 1024}K" for spins in common])
    axis.set_xlabel("Dense problem size (spins)")
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    axis.grid(True, which="both", alpha=0.28)
    axis.legend(frameon=False)
    fig.savefig(output, dpi=220)
    fig.savefig(output.with_suffix(".pdf"))
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("plots/rtl_simulator_scaling"))
    args = parser.parse_args()
    paths = [
        ROOT / "results/dense_config_search/rtl_vcs/summary.csv",
        ROOT / "results/dense_config_search/rtl_vcs_64k_128k/summary.csv",
        ROOT / "results/dense_config_search/rtl_verilator/summary.csv",
        ROOT / "results/dense_config_search/rtl_verilator_64k_128k/summary.csv",
    ]
    data = load_rows(paths)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_metric(
        data, "build_wall_seconds", "Build wall time (seconds)",
        args.output_dir / "build_wall_time.png",
        "RTL simulator build-time scaling",
    )
    plot_metric(
        data, "simulation_wall_seconds", "Simulation wall time (seconds)",
        args.output_dir / "simulation_wall_time.png",
        "Dense RTL simulation-time scaling",
    )
    common = sorted(set(data["vcs"]) & set(data["verilator"]))
    print("paired sizes:", ", ".join(str(value) for value in common))
    print("output:", args.output_dir)


if __name__ == "__main__":
    main()
