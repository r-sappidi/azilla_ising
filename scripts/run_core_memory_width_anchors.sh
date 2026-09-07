#!/usr/bin/env bash
# Scoped full-core anchors: local state snapshots, no publication NoC claim.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
output=${OUTPUT_ROOT:-results/paper_validation_20260907/core_memory_width_e4_j2_v1}
test ! -e "$output"
mkdir -p "$output"
sources() {
  rg --files model/azilla_cycle_model rtl tb scripts | rg '\.(py|sv|cpp|sh)$' | sort | xargs sha256sum
  sha256sum build/cycle_model_ramulator/libazilla_ramulator.so
}
sources > "$output/source_before.sha256"
for width in 32 64; do
  config="results/memory_width_prepare_20260907_v1/ramulator_x${width}_32gbps.yaml"
  run="$output/x${width}"
  skip=0
  if [[ "$width" == 64 ]]; then skip=1; fi
  OUTPUT_ROOT="$run" ENGINES=4 JOBS_PER_SOURCE=2 SKIP_BUILD="$skip" RAMULATOR_CONFIG="$config" \
    bash scripts/run_core_iteration_memory_vcs.sh
  sha256sum "$config" build/core_iteration_memory_vcs_mesh1_e4_j2/simv > "$run/config_binary.sha256"
  for stall in 0 7 13; do
    /usr/bin/time -v -o "$run/independent_stall${stall}.time" \
      python3 scripts/check_core_iteration_pipeline.py --root "$run" --stall "$stall" \
        --engines 4 --jobs 2 --independent --config "$config" > "$run/independent_stall${stall}.log" 2>&1
  done
done
sources > "$output/source_after.sha256"
diff -u "$output/source_before.sha256" "$output/source_after.sha256" > "$output/source_changes.diff" || true
printf 'Completed scoped cores-only width anchors; inspect source_changes.diff before certification: %s\n' "$output"
