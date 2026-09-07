#!/usr/bin/env bash
# Full arithmetic/state/memory/router test. Python timing differential is separate.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export VCS_HOME=${VCS_HOME:-/home/hwpeng/vcs}
export VCS_ARCH_OVERRIDE=linux VCS_TARGET_ARCH=linux64
export LD_LIBRARY_PATH="$repo/third_party/ramulator2:${LD_LIBRARY_PATH:-}"
output=${OUTPUT_ROOT:-results/paper_validation_20260907/core_iteration_publication_v1}
mesh=${USE_MESH:-1}
engines=${ENGINES:-1}
h0_engines=${H0_ENGINES:-$engines}
h1_engines=${H1_ENGINES:-$engines}
cross_engines=${CROSS_ENGINES:-$engines}
jobs=${JOBS_PER_SOURCE:-2}
memory_config=${RAMULATOR_CONFIG:-tb/ramulator_128x32.yaml}
build="$repo/build/core_iteration_publication_vcs_mesh${mesh}_e${engines}_h${h0_engines}_${h1_engines}_${cross_engines}_j${jobs}"
mkdir -p "$output" "$build"
compiler=/home/rsappidi/miniforge3/envs/azilla/bin
if [[ ${SKIP_BUILD:-0} != 1 ]]; then
/usr/bin/time -v -o "$output/build.time" \
  "$VCS_HOME/bin/vcs" -full64 -sverilog -j 2 \
  -cpp "$compiler/x86_64-conda-linux-gnu-g++" \
  -cc "$compiler/x86_64-conda-linux-gnu-gcc" \
  -timescale=1ns/1ps -top core_iteration_publication_vcs_tb \
  -assert svaext -assert disable_cover \
  -pvalue+core_iteration_publication_vcs_tb.USE_MESH="$mesh" \
  -pvalue+core_iteration_publication_vcs_tb.ENGINES="$engines" \
  -pvalue+core_iteration_publication_vcs_tb.H0_ENGINES="$h0_engines" \
  -pvalue+core_iteration_publication_vcs_tb.H1_ENGINES="$h1_engines" \
  -pvalue+core_iteration_publication_vcs_tb.CROSS_ENGINES="$cross_engines" \
  -pvalue+core_iteration_publication_vcs_tb.JOBS_PER_SOURCE="$jobs" \
  -Mdir="$build/csrc" -o "$build/simv" \
  -CFLAGS "-std=c++20 -I$repo/third_party/ramulator2/src" \
  -LDFLAGS "-no-pie -L$repo/third_party/ramulator2 -lramulator -Wl,-rpath,$repo/third_party/ramulator2" \
  -f rtl/floo_router_files.f \
  rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv rtl/azilla_floo_router.sv \
  rtl/core.sv rtl/banked_state_sram.sv rtl/dram_weight_streamer.sv \
  tb/ramulator_dpi_bridge.sv tb/ramulator_node_frontend.sv \
  tb/core_iteration_publication_vcs_tb.sv tb/ramulator_dpi.cpp \
  >"$output/build.log" 2>&1
fi
test -x "$build/simv"
python3 scripts/generate_core_iteration_dataset.py "$output/dataset.txt"
for stall in 0 7 13; do
  /usr/bin/time -v -o "$output/stall${stall}.time" \
    "$build/simv" +STALL_PERIOD="$stall" +DATASET="$output/dataset.txt" +RAMULATOR_CONFIG="$memory_config" >"$output/stall${stall}.log" 2>&1
  rg -q 'PASS CORE_ITERATION_PUBLICATION' "$output/stall${stall}.log"
  python3 scripts/check_core_iteration_arithmetic.py "$output/stall${stall}.log" \
    "$output/dataset.txt" >"$output/arithmetic_stall${stall}.log" 2>&1

done
printf 'Full RTL arithmetic iterations completed: %s (Python timing differential separate)\n' "$output"
