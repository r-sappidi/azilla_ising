#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
output=${OUTPUT_ROOT:?unique OUTPUT_ROOT required}
h0=${H0_PER_H1:-2};cores=${CORES_PER_H0:-2}
mesh_x=${MESH_X:-2};mesh_y=${MESH_Y:-1}
build="$output/build"
mkdir -p "$build"
export VCS_HOME=${VCS_HOME:-/home/hwpeng/vcs} VCS_ARCH_OVERRIDE=linux VCS_TARGET_ARCH=linux64
compiler=/home/rsappidi/miniforge3/envs/azilla/bin
/usr/bin/time -v -o "$output/build.time" "$VCS_HOME/bin/vcs" -full64 -sverilog -j 1 \
  -cpp "$compiler/x86_64-conda-linux-gnu-g++" -cc "$compiler/x86_64-conda-linux-gnu-gcc" \
  +define+AZILLA_TIMING_ONLY -timescale=1ns/1ps -assert svaext -assert disable_cover \
  -top shared_fetch_two_h1_tb -pvalue+shared_fetch_two_h1_tb.H0_PER_H1="$h0" \
  -pvalue+shared_fetch_two_h1_tb.CORES_PER_H0="$cores" \
  -pvalue+shared_fetch_two_h1_tb.MESH_X="$mesh_x" -pvalue+shared_fetch_two_h1_tb.MESH_Y="$mesh_y" \
  -Mdir="$build/csrc" -o "$build/simv" \
  -CFLAGS "-std=c++20 -I$repo/third_party/ramulator2/src" \
  -LDFLAGS "-no-pie -L$repo/third_party/ramulator2 -lramulator -Wl,-rpath,$repo/third_party/ramulator2" \
  -f rtl/floo_router_files.f rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv rtl/core.sv \
  rtl/banked_state_sram.sv rtl/dram_weight_streamer.sv rtl/shared_fetch_replay.sv rtl/shared_fetch_replay_double.sv \
  rtl/azilla_floo_router.sv tb/ramulator_dpi_bridge.sv tb/ramulator_node_frontend.sv \
  tb/shared_fetch_two_h1_tb.sv tb/ramulator_dpi.cpp > "$output/build.log" 2>&1
rtl_schedule_args=()
if [[ -n "${SCHEDULE:-}" ]]; then rtl_schedule_args=("+SCHEDULE=$SCHEDULE"); fi
/usr/bin/time -v -o "$output/sim.time" "$build/simv" "${rtl_schedule_args[@]}" +DATASET="${DATASET:?DATASET required}" +STALL_PERIOD="${STALL_PERIOD:-0}" > "$output/rtl.log" 2>&1
schedule_args=()
if [[ -n "${SCHEDULE:-}" ]]; then schedule_args=(--schedule "$SCHEDULE"); fi
"$compiler/python3" scripts/check_shared_fetch_two_h1.py "${schedule_args[@]}" --dataset "$DATASET" --log "$output/rtl.log" \
  --output "$output/differential.json" --h0 "$h0" --cores "$cores" --mesh-x "$mesh_x" --mesh-y "$mesh_y" --stall "${STALL_PERIOD:-0}" > "$output/differential.log" 2>&1
