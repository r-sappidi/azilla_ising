#!/usr/bin/env bash
# Component integration diagnostic only. Does not release paper validation gates.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export VCS_HOME=${VCS_HOME:-/home/hwpeng/vcs}
export VCS_ARCH_OVERRIDE=linux VCS_TARGET_ARCH=linux64
export LD_LIBRARY_PATH="$repo/third_party/ramulator2:${LD_LIBRARY_PATH:-}"
output=${OUTPUT_ROOT:-results/paper_validation_20260906/core_memory_integration_v1}
mesh=${USE_MESH:-0}
engines=${ENGINES:-1}
jobs=${JOBS_PER_SOURCE:-2}
memory_config=${RAMULATOR_CONFIG:-tb/ramulator_128x32.yaml}
build="$repo/build/core_memory_integration_vcs_mesh${mesh}_e${engines}_j${jobs}"
mkdir -p "$output" "$build"
compiler=/home/rsappidi/miniforge3/envs/azilla/bin
if [[ ${SKIP_BUILD:-0} != 1 ]]; then
/usr/bin/time -v -o "$output/build.time" \
  "$VCS_HOME/bin/vcs" -full64 -sverilog -j 2 \
  -cpp "$compiler/x86_64-conda-linux-gnu-g++" \
  -cc "$compiler/x86_64-conda-linux-gnu-gcc" \
  -timescale=1ns/1ps -top core_memory_integration_vcs_tb \
  -assert svaext -assert disable_cover \
  -pvalue+core_memory_integration_vcs_tb.USE_MESH="$mesh" \
  -pvalue+core_memory_integration_vcs_tb.ENGINES="$engines" \
  -pvalue+core_memory_integration_vcs_tb.JOBS_PER_SOURCE="$jobs" \
  -Mdir="$build/csrc" -o "$build/simv" \
  -CFLAGS "-std=c++20 -I$repo/third_party/ramulator2/src" \
  -LDFLAGS "-no-pie -L$repo/third_party/ramulator2 -lramulator -Wl,-rpath,$repo/third_party/ramulator2" \
  -f rtl/floo_router_files.f \
  rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv rtl/azilla_floo_router.sv \
  rtl/core_local_mvm_engine.sv rtl/dram_weight_streamer.sv \
  tb/ramulator_dpi_bridge.sv tb/ramulator_node_frontend.sv \
  tb/core_memory_integration_vcs_tb.sv tb/ramulator_dpi.cpp \
  >"$output/build.log" 2>&1
fi
test -x "$build/simv"
for stall in 0 7 13; do
  /usr/bin/time -v -o "$output/stall${stall}.time" \
    "$build/simv" +STALL_PERIOD="$stall" +RAMULATOR_CONFIG="$memory_config" >"$output/stall${stall}.log" 2>&1
  rg -q 'PASS CORE_MEMORY_COMPONENT_INTEGRATION' "$output/stall${stall}.log"
  check_args=()
  if [[ $mesh == 1 ]]; then check_args+=(--mesh); fi
  python3 scripts/check_core_memory_integration.py --root "$output" --stall "$stall" \
    --engines "$engines" --jobs "$jobs" "${check_args[@]}" \
    --config "$memory_config" \
    >"$output/comparison_stall${stall}.log" 2>&1
done
printf 'Component integration completed: %s (not a differential certificate)\n' "$output"
