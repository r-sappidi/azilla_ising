#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
export PYTHONPATH="$repo_root/model"

output=results/cir_evaluation/solution_quality/aligned_rademacher_v2_20260904
mkdir -p "$output"
/usr/bin/time -v -o "$output/campaign.time" nice -n 15 python3 \
  scripts/run_reference_solution_quality.py \
  --manifest results/cir_evaluation/solution_quality/d4_to_1m/manifest.json \
  --output-dir "$output" \
  --schedule results/cir_evaluation/campaign/candidate_schedules/noise_decay_a3_b1_n8_to_0.csv \
  --seeds 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29
