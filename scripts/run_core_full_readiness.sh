#!/usr/bin/env bash
# Fail-closed regression and capacity smoke; not a large-geometry RTL certificate.
set -euo pipefail
cd "$(dirname "$0")/.."
output=${OUTPUT_ROOT:?Set a new, unique OUTPUT_ROOT}
test ! -e "$output"
mkdir -p "$output"
sources() {
  rg --files model/azilla_cycle_model model/tests rtl scripts tb -g '*.py' -g '*.sv' -g '*.sh' -g '*.yaml' -g '*.cpp' | sort | xargs sha256sum
  sha256sum build/cycle_model_ramulator/libazilla_ramulator.so
}
sources > "$output/source_before.sha256"
PYTHONPATH=model python3 -m unittest discover -s model/tests -v > "$output/unit.log" 2>&1
fixture=results/paper_validation_20260907/core_iteration_pub_h424_j2_v1
for stall in 0 7 13; do
  for mode in arithmetic timing; do
    options=(--engines 4)
    if [[ "$mode" == timing ]]; then options+=(--timing-only); fi
    python3 scripts/check_core_full_events.py --root "$fixture" --stall "$stall" \
      --h0-engines 4 --h1-engines 2 --cross-engines 4 "${options[@]}" \
      > "$output/${mode}_stall${stall}.log" 2>&1
  done
done
printf 'Representative VCS differential PASS; starting separately labeled scale checks.\n'
for dataset in g16384_kings holdout_toroidal_d4_n16384; do
  /usr/bin/time -v -o "$output/${dataset}.time" env PYTHONPATH=model \
    python3 -m azilla_cycle_model.cli simulate-exact-events \
    --dataset "tb/datasets/${dataset}.txt" --mesh-x 4 --mesh-y 4 \
    --h0-per-h1 1 --cores-per-h0 32 --h0-mvms 4 --h1-mvms 2 --cross-mvms 4 \
    --execution-mode cores-only \
    --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
    --ramulator-config tb/ramulator_128x32.yaml \
    --max-cycles 1000000 --metrics-prefix "$output/${dataset}/metrics" \
    > "$output/${dataset}.log" 2>&1
done
sources > "$output/source_after.sha256"
diff -u "$output/source_before.sha256" "$output/source_after.sha256" > "$output/source_changes.diff"
printf 'PASS representative full-core differential and 16K scale smoke; 16K is not RTL-certified.\n'
