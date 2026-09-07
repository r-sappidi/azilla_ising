#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export VCS_HOME=${VCS_HOME:-/home/hwpeng/vcs}
export VCS_ARCH_OVERRIDE=linux VCS_TARGET_ARCH=linux64
out=${OUTPUT_ROOT:-results/paper_validation_20260907/core_shared_mvm_v1}
build=build/core_shared_mvm_vcs
mkdir -p "$out" "$build"
/usr/bin/time -v -o "$out/build.time" "$VCS_HOME/bin/vcs" -full64 -sverilog \
  -timescale=1ns/1ps -LDFLAGS '-no-pie' -top core_shared_mvm_vcs_tb \
  -Mdir="$build/csrc" -o "$build/simv" \
  rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv rtl/core.sv \
  tb/core_shared_mvm_vcs_tb.sv >"$out/build.log" 2>&1
/usr/bin/time -v -o "$out/run.time" "$build/simv" >"$out/run.log" 2>&1
rg -q 'PASS CORE_SHARED_MVM' "$out/run.log"
python3 scripts/check_core_shared_mvm.py --log "$out/run.log" --output "$out/comparison.json" \
  >"$out/comparison.log" 2>&1
printf 'PASS shared single-MVM arithmetic/lifecycle: %s\n' "$out"
