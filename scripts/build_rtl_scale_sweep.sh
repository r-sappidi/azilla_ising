#!/usr/bin/env bash
# Build full Azilla RTL configurations from 16K through 128K spins without
# starting simulation. Intended for a long-running tmux session.

set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/.." && pwd)

usage() {
  cat <<'EOF'
Usage: scripts/build_rtl_scale_sweep.sh [SPIN_COUNT ...]

Build, but do not run, full Verilator RTL simulators. With no arguments the
script builds 16384, 32768, 65536, and 131072-spin configurations in order.

Recommended tmux invocation:
  JOBS=2 scripts/build_rtl_scale_sweep.sh 2>&1 | tee logs/rtl_scale_driver.log

Important environment overrides:
  JOBS=1                 Parallel C++ build jobs; start conservatively.
  TIMING_ONLY=1          1 for performance RTL, 0 for arithmetic RTL.
  USE_RAMULATOR=1        Compile the Ramulator-backed testbench.
  H0_MVMS=1              H0 engines per H0 tile.
  H1_MVMS=1              H1-local engines per H1 tile.
  CROSS_MVMS=16          Cross-H1 engines per H1 tile.
  OUTPUT_SPLIT=20000     Verilator output-splitting threshold; 0 disables it.
  CONTINUE_ON_ERROR=0    Set to 1 to attempt larger builds after a failure.
  DRY_RUN=0              Set to 1 to print configurations without compiling.
  LOG_DIR=logs/...       Build logs and manifest directory.

Examples:
  scripts/build_rtl_scale_sweep.sh 16384 32768
  JOBS=4 CONTINUE_ON_ERROR=1 scripts/build_rtl_scale_sweep.sh
  TIMING_ONLY=0 scripts/build_rtl_scale_sweep.sh 16384
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
fi

# Defaults are performance-oriented, timing-only RTL builds. Set
# TIMING_ONLY=0 for arithmetic-enabled binaries. Build parallelism defaults to
# one because large Verilated designs can consume substantial RAM per compiler
# process; increase JOBS only after checking server memory.
JOBS=${JOBS:-1}
TIMING_ONLY=${TIMING_ONLY:-1}
USE_RAMULATOR=${USE_RAMULATOR:-1}
SKIP_ZERO_BLOCKS=${SKIP_ZERO_BLOCKS:-1}
FIFO_DEPTH=${FIFO_DEPTH:-4}
ITERATION_COUNT=${ITERATION_COUNT:-1}
OUTPUT_SPLIT=${OUTPUT_SPLIT:-20000}
CONTINUE_ON_ERROR=${CONTINUE_ON_ERROR:-0}
DRY_RUN=${DRY_RUN:-0}
LOG_DIR=${LOG_DIR:-"${repo_root}/logs/verilator_scale"}

# Sparse-reference profile. It preserves the RTL-differential 16K geometry,
# then keeps 32 cores per H0 while adding H0s as capacity grows. A 4x4 H1 mesh
# and 16 cross-H1 engines are held fixed so the builds form a controlled scale
# series. These are reference configurations, not workload-independent global
# optima; all engine counts can be overridden below.
MESH_X=${MESH_X:-4}
MESH_Y=${MESH_Y:-4}
H0_MVMS=${H0_MVMS:-1}
H1_MVMS=${H1_MVMS:-1}
CROSS_MVMS=${CROSS_MVMS:-16}

if (($#)); then
  spin_counts=("$@")
else
  spin_counts=(16384 32768 65536 131072)
fi

case "$JOBS" in
  ''|*[!0-9]*) echo "JOBS must be a positive integer" >&2; exit 2 ;;
esac
if ((JOBS < 1)); then
  echo "JOBS must be at least 1" >&2
  exit 2
fi

mkdir -p "$LOG_DIR"
manifest="$LOG_DIR/manifest.tsv"
if [[ ! -e "$manifest" ]]; then
  printf 'spins\tmesh\th0_per_h1\tcores_per_h0\tmvms\ttiming_only\tramulator\tbinary\tlog\n' > "$manifest"
fi

if [[ "$USE_RAMULATOR" == 1 ]]; then
  if [[ ! -f "$repo_root/third_party/ramulator2/libramulator.so" ]]; then
    echo "missing third_party/ramulator2/libramulator.so" >&2
    echo "build Ramulator first using the repository README instructions" >&2
    exit 2
  fi
fi

configuration_for_spins() {
  case "$1" in
    16384)  printf '%s %s\n' 2 16 ;;
    32768)  printf '%s %s\n' 2 32 ;;
    65536)  printf '%s %s\n' 4 32 ;;
    131072) printf '%s %s\n' 8 32 ;;
    *)
      echo "unsupported default spin count $1" >&2
      echo "supported values: 16384 32768 65536 131072" >&2
      return 2
      ;;
  esac
}

current_spins=none
on_exit() {
  rc=$?
  if ((rc != 0)); then
    echo "sweep stopped while processing spins=${current_spins} (exit ${rc})" >&2
  fi
}
trap on_exit EXIT

failures=0
for spins in "${spin_counts[@]}"; do
  current_spins=$spins
  read -r h0_count cores_per_h0 < <(configuration_for_spins "$spins")
  implemented=$((MESH_X * MESH_Y * h0_count * cores_per_h0 * 32))
  if ((implemented != spins)); then
    echo "internal configuration error: requested ${spins}, implements ${implemented}" >&2
    exit 2
  fi

  config="${MESH_X}x${MESH_Y}_h0${h0_count}_c${cores_per_h0}_e${H0_MVMS}-${H1_MVMS}-${CROSS_MVMS}_f${FIFO_DEPTH}_s${SKIP_ZERO_BLOCKS}_r${USE_RAMULATOR}_i${ITERATION_COUNT}_t${TIMING_ONLY}"
  binary="${repo_root}/build/ising_mesh_floo_${config}/Vising_mesh_tb"
  log="${LOG_DIR}/build_${spins}_${config}.log"

  command=(
    make -C "$repo_root/tb" floo-mesh-build
    "MESH_X_COUNT=${MESH_X}"
    "MESH_Y_COUNT=${MESH_Y}"
    "H0_COUNT=${h0_count}"
    "CORES_PER_H0=${cores_per_h0}"
    "H0_MVM_COUNT=${H0_MVMS}"
    "H1_MVM_COUNT=${H1_MVMS}"
    "CROSS_MVM_COUNT=${CROSS_MVMS}"
    "FIFO_DEPTH=${FIFO_DEPTH}"
    "SKIP_ZERO_BLOCKS=${SKIP_ZERO_BLOCKS}"
    "USE_RAMULATOR=${USE_RAMULATOR}"
    "ITERATION_COUNT=${ITERATION_COUNT}"
    "TIMING_ONLY=${TIMING_ONLY}"
    "VERILATOR_JOBS=${JOBS}"
    "VERILATOR_OUTPUT_SPLIT=${OUTPUT_SPLIT}"
  )

  echo
  echo "[$(date --iso-8601=seconds)] build spins=${spins} config=${config}"
  echo "binary=${binary}"
  printf 'command='
  printf '%q ' "${command[@]}"
  echo

  printf '%s\t%dx%d\t%d\t%d\t%d/%d/%d\t%d\t%d\t%s\t%s\n' \
    "$spins" "$MESH_X" "$MESH_Y" "$h0_count" "$cores_per_h0" \
    "$H0_MVMS" "$H1_MVMS" "$CROSS_MVMS" "$TIMING_ONLY" \
    "$USE_RAMULATOR" "$binary" "$log" >> "$manifest"

  if [[ "$DRY_RUN" == 1 ]]; then
    continue
  fi

  if [[ -x "$binary" ]]; then
    echo "existing binary found; make will verify whether it is current"
  fi

  set +e
  {
    echo "started=$(date --iso-8601=seconds)"
    printf 'command='
    printf '%q ' "${command[@]}"
    echo
    time "${command[@]}"
  } 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e

  if ((rc != 0)); then
    echo "FAILED spins=${spins}; see ${log}" >&2
    failures=$((failures + 1))
    if [[ "$CONTINUE_ON_ERROR" != 1 ]]; then
      exit "$rc"
    fi
  elif [[ ! -x "$binary" ]]; then
    echo "build returned success but binary is missing: ${binary}" >&2
    exit 1
  else
    echo "PASS spins=${spins} binary=${binary}"
  fi
done

current_spins=none
if ((failures)); then
  echo "completed with ${failures} failed configuration(s)" >&2
  exit 1
fi
echo "all requested RTL builds completed; manifest=${manifest}"
