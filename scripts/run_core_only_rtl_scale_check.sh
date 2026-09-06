#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

if [[ -n ${VCS_HOME:-} && -x $VCS_HOME/bin/vcs ]]; then
    vcs=$VCS_HOME/bin/vcs
else
    vcs=$(command -v vcs || true)
fi
if [[ -z $vcs ]]; then
    echo "VCS not found; set VCS_HOME or add vcs to PATH" >&2
    exit 2
fi
output_root=${OUTPUT_ROOT:-results/core_only_rtl_scale_check}
sizes=${SIZES:-16384,65536,131072,262144}
mkdir -p "$output_root"

IFS=, read -ra requested <<< "$sizes"
for spins in "${requested[@]}"; do
    if (( spins <= 0 || spins % 32 != 0 )); then
        echo "invalid spin count: $spins" >&2
        exit 2
    fi
    cores=$((spins / 32))
    run_dir="$output_root/$spins"
    mkdir -p "$run_dir"
    if [[ -f "$run_dir/PASS" ]]; then
        echo "SKIP spins=$spins"
        continue
    fi
    echo "BUILD spins=$spins cores=$cores"
    (
        cd "$run_dir"
        /usr/bin/time -v -o build.time \
            "$vcs" -full64 -sverilog -timescale=1ns/1ps \
            -LDFLAGS '-no-pie' -top core_local_scale_tb \
            -pvalue+core_local_scale_tb.TOTAL_CORES="$cores" \
            "$repo_root/rtl/ising_pkg.sv" \
            "$repo_root/rtl/j_block_sram.sv" \
            "$repo_root/rtl/mvm.sv" \
            "$repo_root/rtl/core_local_mvm_engine.sv" \
            "$repo_root/tb/core_local_scale_tb.sv" -o simv \
            > build.log 2>&1
        /usr/bin/time -v -o sim.time ./simv > sim.log 2>&1
        grep -q "PASS: cores-only replicated RTL capacity=$spins spins" sim.log
        touch PASS
    )
    echo "PASS spins=$spins"
done
