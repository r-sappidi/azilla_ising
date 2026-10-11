#!/usr/bin/env bash
set -uo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
export PYTHONPATH="$repo_root/model"
export LD_LIBRARY_PATH="$repo_root/third_party/ramulator2:${LD_LIBRARY_PATH:-}"

output_root=${OUTPUT_ROOT:-results/simulator_validation}
mkdir -p "$output_root/logs"
summary="$output_root/status.tsv"
printf 'check\tstatus\telapsed_seconds\tlog\n' > "$summary"

run_check() {
    local name=$1
    shift
    local log="$output_root/logs/$name.log"
    local started ended rc
    started=$(date +%s)
    "$@" >"$log" 2>&1
    rc=$?
    ended=$(date +%s)
    if (( rc == 0 )); then
        printf '%s\tPASS\t%s\t%s\n' "$name" "$((ended-started))" "$log" | tee -a "$summary"
    else
        printf '%s\tFAIL\t%s\t%s\n' "$name" "$((ended-started))" "$log" | tee -a "$summary"
    fi
    return "$rc"
}

failures=0
run_check python_units python3 -m unittest discover -s model/tests -v || failures=$((failures+1))
run_check component_rtl python3 scripts/check_cycle_model.py || failures=$((failures+1))
run_check full_256 python3 scripts/check_full_cycle_model.py || failures=$((failures+1))
run_check cir_16k_timing python3 scripts/check_16k_cycle_model.py --reuse-rtl-trace || failures=$((failures+1))
run_check cir_16k_arithmetic python3 scripts/check_16k_arithmetic.py --reuse-golden --reuse-rtl || failures=$((failures+1))
run_check cir_exact_event python3 scripts/check_exact_event_model.py || failures=$((failures+1))
run_check cores_only_256_rtl make -C tb core-local-baseline-256-test || failures=$((failures+1))
run_check hybrid_256_rtl make -C tb static-hybrid-256-test || failures=$((failures+1))

for spins in 16384 65536 131072 262144; do
    if [[ -f "results/core_only_rtl_scale_check/$spins/PASS" ]]; then
        printf 'cores_only_capacity_%s\tPASS\t0\tresults/core_only_rtl_scale_check/%s\n' \
            "$spins" "$spins" | tee -a "$summary"
    else
        printf 'cores_only_capacity_%s\tMISSING\t0\tresults/core_only_rtl_scale_check/%s\n' \
            "$spins" "$spins" | tee -a "$summary"
        failures=$((failures+1))
    fi
done

# This is deliberately a hard, visible gate. Capacity replication is not a
# substitute for an integrated cores-only RTL/exact-event differential.
if compgen -G "$output_root/cores_only_full_system/*/PASS" >/dev/null; then
    printf 'cores_only_full_system_differential\tPASS\t0\t%s\n' \
        "$output_root/cores_only_full_system" | tee -a "$summary"
else
    printf 'cores_only_full_system_differential\tBLOCKED\t0\tintegrated RTL mode not yet implemented\n' \
        | tee -a "$summary"
    failures=$((failures+1))
fi

printf 'failures\t%s\n' "$failures" | tee "$output_root/result.txt"
exit "$((failures != 0))"
