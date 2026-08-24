#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "usage: $0 TRACE_PREFIX [OUTPUT_DIRECTORY]" >&2
    echo "example: $0 logs/run_noc logs/run_noc_plots" >&2
    exit 2
fi

trace_prefix=$1
output_directory=${2:-"${trace_prefix}_plots"}
timeline_file="${trace_prefix}_timeline.csv"
event_file="${trace_prefix}_events.csv"

python3 scripts/analyze_noc_trace.py \
    --timeline "$timeline_file" \
    --events "$event_file" \
    --output-prefix "${trace_prefix}_analysis"

python3 scripts/plot_noc_trace.py \
    --timeline "$timeline_file" \
    --events "$event_file" \
    --output-dir "$output_directory" \
    --format both
