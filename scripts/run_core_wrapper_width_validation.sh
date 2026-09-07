#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
output=${OUTPUT_ROOT:?Set a unique OUTPUT_ROOT}
test ! -e "$output"
mkdir -p "$output"
for width in 32 64; do
  for stall in 0 7 13; do
    for mode in arithmetic timing; do
      options=(--engines 4)
      if [[ "$mode" == timing ]]; then options+=(--timing-only); fi
      /usr/bin/time -v -o "$output/x${width}_${mode}_stall${stall}.time" \
        python3 scripts/check_core_full_events.py \
        --root "results/paper_validation_20260907/core_publication_width_e4_j2_v3/x${width}" \
        --config "results/memory_width_prepare_20260907_v1/ramulator_x${width}_32gbps.yaml" \
        --stall "$stall" "${options[@]}" \
        > "$output/x${width}_${mode}_stall${stall}.log" 2>&1
    done
  done
done
printf 'PASS all 12 full production-wrapper x32/x64 comparisons\n'
