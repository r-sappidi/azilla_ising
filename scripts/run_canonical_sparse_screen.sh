#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
export PYTHONPATH="$repo_root/model"

manifest=${MANIFEST:-results/paper_canonical/datasets/manifest.json}
output=${OUTPUT_DIR:-results/paper_canonical/calibrated_identity}
workers=${WORKERS:-1}

python3 scripts/run_ising_benchmark_sweep.py \
  --manifest "$manifest" \
  --output-dir "$output" \
  --workers "$workers" \
  --h0-mvms 4 \
  --h1-mvms 2 \
  --cross-mvms 4 \
  --fifo-depth 4

MPLBACKEND=Agg python3 scripts/plot_canonical_sparse_campaign.py \
  --results "$output/results.csv" \
  --output-dir plots/paper_canonical
