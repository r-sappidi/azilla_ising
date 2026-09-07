#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
export VCS_HOME=${VCS_HOME:-/home/hwpeng/vcs}
export VCS_ARCH_OVERRIDE=linux VCS_TARGET_ARCH=linux64
out=${OUTPUT_ROOT:-results/paper_validation_20260907/core_state_publication_$(date -u +%Y%m%dT%H%M%S)}
build=build/core_state_publication_vcs
mkdir -p "$out" "$build"
/usr/bin/time -v -o "$out/build.time" "$VCS_HOME/bin/vcs" -full64 -sverilog \
  -assert svaext -assert disable_cover -timescale=1ns/1ps -LDFLAGS '-no-pie' \
  -top core_state_publication_vcs_tb -Mdir="$build/csrc" -o "$build/simv" \
  -f rtl/floo_router_files.f rtl/ising_pkg.sv rtl/banked_state_sram.sv \
  rtl/azilla_floo_router.sv tb/core_state_publication_vcs_tb.sv >"$out/build.log" 2>&1
/usr/bin/time -v -o "$out/run.time" "$build/simv" >"$out/run.log" 2>&1
python3 scripts/check_core_state_publication.py --log "$out/run.log" --output "$out/comparison.json" \
  >"$out/comparison.log" 2>&1
printf 'PASS core state publication: %s\n' "$out"
