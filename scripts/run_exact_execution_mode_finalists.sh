#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-$PWD/model}"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-$PWD/third_party/ramulator2}"
out_root="${1:-results/paper_benchmarks/exact_execution_modes}"
for spec in \
  'gset_g14 4 4 1 2' \
  'gset_g70 4 4 2 16' \
  'gset_g81 4 4 2 32'
do
  read -r name mx my h0 cores <<<"$spec"
  for mode in cir cores-only hybrid; do
    out="$out_root/$name/$mode"
    mkdir -p "$out"
    if [[ -f "$out/metrics_summary.json" ]]; then continue; fi
    /usr/bin/time -v -o "$out/wall.time" python3 -m azilla_cycle_model.cli \
      simulate-exact-events \
      --dataset "results/paper_benchmarks/datasets/prepared/$name.txt" \
      --mesh-x "$mx" --mesh-y "$my" --h0-per-h1 "$h0" \
      --cores-per-h0 "$cores" --h0-mvms 4 --h1-mvms 2 --cross-mvms 4 \
      --execution-mode "$mode" \
      --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
      --ramulator-config tb/ramulator_128x32.yaml \
      --idle-refresh-period-ticks 7600 --metrics-prefix "$out/metrics" \
      --transfer-trace "$out/transfers.csv" >"$out/run.log" 2>&1
  done
done
