#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
output=${OUTPUT_ROOT:-results/paper_validation_20260907/core_publication_width_e4_j2_v1}
test ! -e "$output"
mkdir -p "$output"
sources() {
  sha256sum model/azilla_cycle_model/{__init__,core_iteration_pipeline,core_iteration_driver,core_full_controller,core_state_publication,core_iteration,core_state_bank,compute,config,fixed,hierarchy,memory,noc,ramulator,workload}.py \
    scripts/{check_core_iteration_pipeline,check_core_iteration_arithmetic,generate_core_iteration_dataset}.py \
    tb/{core_iteration_publication_vcs_tb.sv,ramulator_dpi.cpp,ramulator_dpi_bridge.sv,ramulator_node_frontend.sv} \
    build/cycle_model_ramulator/libazilla_ramulator.so
  rg --files rtl | sort | xargs sha256sum
}
sources > "$output/source_before.sha256"
for width in 32 64; do
  config="results/memory_width_prepare_20260907_v1/ramulator_x${width}_32gbps.yaml"
  run="$output/x${width}"
  skip=0
  if [[ "$width" == 64 ]]; then skip=1; fi
  OUTPUT_ROOT="$run" ENGINES=4 JOBS_PER_SOURCE=2 SKIP_BUILD="$skip" RAMULATOR_CONFIG="$config" \
    bash scripts/run_core_iteration_publication_vcs.sh
  sha256sum "$config" build/core_iteration_publication_vcs_mesh1_e4_j2/simv > "$run/config_binary.sha256"
  for stall in 0 7 13; do
    for compressed in 0 1; do
      options=(--independent)
      label=independent
      if [[ "$compressed" == 1 ]]; then options+=(--compress-idle); label=independent_compressed; fi
      /usr/bin/time -v -o "$run/${label}_stall${stall}.time" \
        python3 scripts/check_core_iteration_pipeline.py --root "$run" --stall "$stall" \
          --engines 4 --jobs 2 --full-publication "${options[@]}" \
          --config "$config" > "$run/${label}_stall${stall}.log" 2>&1
    done
  done
done
sources > "$output/source_after.sha256"
diff -u "$output/source_before.sha256" "$output/source_after.sha256" > "$output/source_changes.diff"
printf 'PASS full publication width anchors, stable sources: %s\n' "$output"
