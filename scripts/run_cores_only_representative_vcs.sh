#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
if [[ -n ${VCS_HOME:-} && -x $VCS_HOME/bin/vcs ]]; then
  vcs=$VCS_HOME/bin/vcs
else
  vcs=$(command -v vcs || true)
fi
if [[ -z $vcs ]]; then
  echo "VCS not found; set VCS_HOME or add vcs to PATH" >&2
  exit 2
fi
export VCS_HOME=${VCS_HOME:-$(cd "$(dirname "$vcs")/.." && pwd)}
export VCS_ARCH_OVERRIDE=linux VCS_TARGET_ARCH=linux64
export LD_LIBRARY_PATH="$repo/third_party/ramulator2:${LD_LIBRARY_PATH:-}"

out=${OUTPUT_ROOT:-results/cores_only_representative_validation}
capacity_root=${CAPACITY_ROOT:-results/core_only_rtl_scale_check}
router_out=$out/vcs_router_core
ram_out=$out/vcs_ramulator
mkdir -p "$router_out" "$ram_out" build/core_only_representative_vcs \
    build/core_only_ramulator_vcs

# Directed engine arithmetic/accounting under VCS.
make -C tb core-local-baseline-256-vcs-test

# Real FlooNoC plus two directed endpoint engines.
"$vcs" -full64 -sverilog -assert svaext -assert disable_cover \
  -timescale=1ns/1ps -LDFLAGS '-no-pie' \
  -top core_only_representative_vcs_tb -f rtl/floo_router_files.f \
  rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv \
  rtl/core_local_mvm_engine.sv rtl/azilla_floo_router.sv \
  tb/core_only_representative_vcs_tb.sv \
  -o build/core_only_representative_vcs/simv
for seed in 1 7 19; do
  for stall in 0 4 12; do
    log=$router_out/seed${seed}_stall${stall}.log
    build/core_only_representative_vcs/simv \
      +SEED="$seed" +STALL_CYCLES="$stall" >"$log" 2>&1
    grep -q 'PASS: cores-only representative VCS RTL' "$log"
  done
done
python3 scripts/check_core_only_representative_vcs.py \
  --log-dir "$router_out" --output "$router_out/results.csv"
touch "$router_out/PASS"

# Live tagged streamer/Ramulator endpoint under VCS. The identical replay is
# intentional: determinism and exact request/response totals are gate checks.
cxx=${CXX:-$(command -v g++)}
cc=${CC:-$(command -v gcc)}
"$vcs" -full64 -sverilog -j 2 \
  -cpp "$cxx" -cc "$cc" \
  -assert svaext -assert disable_cover -timescale=1ns/1ps \
  -top ramulator_node_perf_tb -Mdir=build/core_only_ramulator_vcs/csrc \
  -o build/core_only_ramulator_vcs/simv \
  -pvalue+ramulator_node_perf_tb.MVM_COUNT=4 \
  -pvalue+ramulator_node_perf_tb.OUTPUT_LANES=1 \
  -pvalue+ramulator_node_perf_tb.WORK_BLOCK_COUNT=64 \
  -pvalue+ramulator_node_perf_tb.TOTAL_BLOCK_COUNT=2048 \
  -pvalue+ramulator_node_perf_tb.STATE_ENTRY_COUNT=64 \
  -pvalue+ramulator_node_perf_tb.MEM_LANES=4 \
  -CFLAGS "-std=c++20 -I$repo/third_party/ramulator2/src" \
  -LDFLAGS "-no-pie -L$repo/third_party/ramulator2 -lramulator -Wl,-rpath,$repo/third_party/ramulator2" \
  rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/banked_state_sram.sv rtl/mvm.sv \
  rtl/symmetric_mvm.sv rtl/hierarchy_node.sv rtl/dram_weight_streamer.sv \
  tb/ramulator_dpi_bridge.sv tb/ramulator_node_frontend.sv \
  tb/ramulator_node_perf_tb.sv tb/ramulator_dpi.cpp
for suffix in contention contention_repeat; do
  build/core_only_ramulator_vcs/simv +DATASET=g65536_kings.txt \
    +RAMULATOR_CONFIG=tb/ramulator_128x32.yaml >"$ram_out/$suffix.log" 2>&1
done

for spins in 16384 65536 131072 262144; do
  test -f "$capacity_root/$spins/PASS"
done
printf 'EXIT_CODE=0\n' >"$out/prechecks_vcs.status"
python3 scripts/finalize_cores_only_representative_validation.py \
  --output-root "$out" --capacity-root "$capacity_root"
