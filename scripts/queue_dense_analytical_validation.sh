#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
export PYTHONPATH="$repo_root/model"
export LD_LIBRARY_PATH="$repo_root/third_party/ramulator2:${LD_LIBRARY_PATH:-}"
mkdir -p results/dense_analytical_validation logs

# Do not compete with the strict validation campaign, the already-running
# large exact differential, or the long 512K/1M VCS sequence.
while pgrep -f 'scripts/run_simulator_validation_campaign.sh|scripts/check_large_exact_event_model.py|simv.*dense_(524288|1048576)_header' >/dev/null; do
    date -u '+WAIT %FT%TZ existing validation/RTL campaign still active'
    sleep 60
done

python3 scripts/check_dense_analytical_model.py
python3 scripts/check_large_exact_event_model.py --sizes 65536 131072 262144
python3 scripts/run_dense_partial_exact_validation.py --sizes 16384,32768
python3 scripts/check_dense_analytical_model.py
date -u '+COMPLETE %FT%TZ dense analytical validation'
