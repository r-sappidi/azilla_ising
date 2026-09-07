#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
output=${OUTPUT_ROOT:?unique OUTPUT_ROOT required}
h0=${H0_COUNT:-8};cores=${CORES_PER_H0:-256}
build="$output/build"
mkdir -p "$build"
export VCS_HOME=${VCS_HOME:-/home/hwpeng/vcs} VCS_ARCH_OVERRIDE=linux VCS_TARGET_ARCH=linux64
compiler=/home/rsappidi/miniforge3/envs/azilla/bin
/usr/bin/time -v -o "$output/build.time" "$VCS_HOME/bin/vcs" -full64 -sverilog -j 1 \
  -cpp "$compiler/x86_64-conda-linux-gnu-g++" -cc "$compiler/x86_64-conda-linux-gnu-gcc" \
  +define+AZILLA_TIMING_ONLY -timescale=1ns/1ps -assert svaext -assert disable_cover \
  -top shared_fetch_single_h1_tb -pvalue+shared_fetch_single_h1_tb.H0_COUNT="$h0" \
  -pvalue+shared_fetch_single_h1_tb.CORES_PER_H0="$cores" \
  -Mdir="$build/csrc" -o "$build/simv" \
  -CFLAGS "-std=c++20 -I$repo/third_party/ramulator2/src" \
  -LDFLAGS "-no-pie -L$repo/third_party/ramulator2 -lramulator -Wl,-rpath,$repo/third_party/ramulator2" \
  -f rtl/floo_router_files.f rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv rtl/core.sv \
  rtl/banked_state_sram.sv rtl/dram_weight_streamer.sv rtl/shared_fetch_replay.sv rtl/shared_fetch_replay_double.sv \
  rtl/azilla_floo_router.sv tb/ramulator_dpi_bridge.sv tb/ramulator_node_frontend.sv \
  tb/shared_fetch_single_h1_tb.sv tb/ramulator_dpi.cpp > "$output/build.log" 2>&1
/usr/bin/time -v -o "$output/sim.time" "$build/simv" +DATASET="${DATASET:?DATASET required}" > "$output/rtl.log" 2>&1
"$compiler/python3" scripts/check_shared_fetch_single_h1.py --dataset "$DATASET" --log "$output/rtl.log" \
  --output "$output/differential.json" --h0 "$h0" --cores "$cores" > "$output/differential.log" 2>&1
