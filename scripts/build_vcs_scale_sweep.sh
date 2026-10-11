#!/usr/bin/env bash
# Build full Azilla RTL configurations with Synopsys VCS without simulating.

set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/.." && pwd)

# The local license setting is intentionally kept in the user's private shell
# configuration, not copied into this repository or its logs.
source /home/rsappidi/.bashrc

VCS_HOME=${VCS_HOME:-/home/hwpeng/vcs}
VCS_ARCH_OVERRIDE=${VCS_ARCH_OVERRIDE:-linux}
VCS_TARGET_ARCH=${VCS_TARGET_ARCH:-linux64}
export VCS_HOME VCS_ARCH_OVERRIDE VCS_TARGET_ARCH
export PATH="$VCS_HOME/bin:/usr/bin:/bin"

VCS_JOBS=${VCS_JOBS:-2}
TIMING_ONLY=${TIMING_ONLY:-1}
USE_RAMULATOR=${USE_RAMULATOR:-1}
SKIP_ZERO_BLOCKS=${SKIP_ZERO_BLOCKS:-1}
FIFO_DEPTH=${FIFO_DEPTH:-4}
ITERATION_COUNT=${ITERATION_COUNT:-1}
CONTINUE_ON_ERROR=${CONTINUE_ON_ERROR:-0}
DRY_RUN=${DRY_RUN:-0}
LOG_DIR=${LOG_DIR:-"${repo_root}/logs/vcs_scale"}
MESH_X=${MESH_X:-4}
MESH_Y=${MESH_Y:-4}
H0_MVMS=${H0_MVMS:-1}
H1_MVMS=${H1_MVMS:-1}
CROSS_MVMS=${CROSS_MVMS:-4}

if (($#)); then
    spin_counts=("$@")
else
    spin_counts=(16384 32768 65536 131072)
fi

if [[ -z ${SNPSLMD_LICENSE_FILE:-} && -z ${LM_LICENSE_FILE:-} ]]; then
    echo "SNPSLMD_LICENSE_FILE or LM_LICENSE_FILE must be configured" >&2
    exit 2
fi
if [[ ! -x $VCS_HOME/bin/vcs ]]; then
    echo "VCS executable not found at $VCS_HOME/bin/vcs" >&2
    exit 2
fi
if [[ $USE_RAMULATOR == 1 && ! -f $repo_root/third_party/ramulator2/libramulator.so ]]; then
    echo "missing third_party/ramulator2/libramulator.so" >&2
    exit 2
fi

configuration_for_spins() {
    case "$1" in
        16384)  printf '%s %s\n' 2 16 ;;
        32768)  printf '%s %s\n' 2 32 ;;
        65536)  printf '%s %s\n' 4 32 ;;
        131072) printf '%s %s\n' 8 32 ;;
        *) echo "unsupported spin count $1" >&2; return 2 ;;
    esac
}

mkdir -p "$LOG_DIR"
manifest="$LOG_DIR/manifest.tsv"
if [[ ! -e $manifest ]]; then
    printf 'spins\tmesh\th0_per_h1\tcores_per_h0\tmvms\ttiming_only\tramulator\tbinary\tlog\n' > "$manifest"
fi

cxx=/home/rsappidi/miniforge3/envs/azilla/bin/x86_64-conda-linux-gnu-g++
cc=/home/rsappidi/miniforge3/envs/azilla/bin/x86_64-conda-linux-gnu-gcc
timing_define=()
if [[ $TIMING_ONLY == 1 ]]; then
    timing_define=(+define+AZILLA_TIMING_ONLY)
fi

failures=0
for spins in "${spin_counts[@]}"; do
    read -r h0_count cores_per_h0 < <(configuration_for_spins "$spins")
    config="${MESH_X}x${MESH_Y}_h0${h0_count}_c${cores_per_h0}_e${H0_MVMS}-${H1_MVMS}-${CROSS_MVMS}_f${FIFO_DEPTH}_s${SKIP_ZERO_BLOCKS}_r${USE_RAMULATOR}_i${ITERATION_COUNT}_t${TIMING_ONLY}"
    build_dir="$repo_root/build/ising_mesh_vcs_${config}"
    binary="$build_dir/simv"
    log="$LOG_DIR/build_${spins}_${config}.log"
    mkdir -p "$build_dir"

    command=(
        vcs -full64 -sverilog -j "$VCS_JOBS"
        -cpp "$cxx" -cc "$cc"
        -assert svaext -assert disable_cover -timescale=1ns/1ps
        "${timing_define[@]}"
        -top ising_mesh_tb -Mdir="$build_dir/csrc" -o "$binary"
        -pvalue+ising_mesh_tb.MESH_X_COUNT="$MESH_X"
        -pvalue+ising_mesh_tb.MESH_Y_COUNT="$MESH_Y"
        -pvalue+ising_mesh_tb.H0_COUNT="$h0_count"
        -pvalue+ising_mesh_tb.CORES_PER_H0="$cores_per_h0"
        -pvalue+ising_mesh_tb.H0_MVM_COUNT="$H0_MVMS"
        -pvalue+ising_mesh_tb.H1_MVM_COUNT="$H1_MVMS"
        -pvalue+ising_mesh_tb.CROSS_MVM_COUNT="$CROSS_MVMS"
        -pvalue+ising_mesh_tb.FIFO_DEPTH="$FIFO_DEPTH"
        -pvalue+ising_mesh_tb.SKIP_ZERO_BLOCKS="$SKIP_ZERO_BLOCKS"
        -pvalue+ising_mesh_tb.USE_RAMULATOR="$USE_RAMULATOR"
        -pvalue+ising_mesh_tb.MEM_PIN_COUNT=128
        -pvalue+ising_mesh_tb.MEM_PIN_GBPS=32
        -pvalue+ising_mesh_tb.MEM_LANES=16
        -pvalue+ising_mesh_tb.ITERATION_COUNT="$ITERATION_COUNT"
        -CFLAGS "-std=c++20 -I$repo_root/third_party/ramulator2/src"
        -LDFLAGS "-no-pie -L$repo_root/third_party/ramulator2 -lramulator -Wl,-rpath,$repo_root/third_party/ramulator2"
        -f rtl/floo_router_files.f
        rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/banked_state_sram.sv rtl/mvm.sv
        rtl/symmetric_mvm.sv rtl/core.sv rtl/hierarchy_node.sv
        rtl/h0_adapter.sv rtl/h0_tile.sv rtl/h1_child_adapter.sv
        rtl/h1_tile.sv rtl/h1_noc_adapter.sv rtl/cross_h1_node.sv
        rtl/azilla_floo_router.sv rtl/top_node.sv rtl/mesh_h1_tile.sv
        rtl/ising_mesh.sv rtl/dram_weight_streamer.sv
        tb/ramulator_dpi_bridge.sv tb/ramulator_node_frontend.sv
        tb/ising_mesh_tb.sv tb/ramulator_dpi.cpp
    )

    echo
    echo "[$(date --iso-8601=seconds)] build spins=$spins config=$config"
    echo "binary=$binary"
    printf 'command='; printf '%q ' "${command[@]}"; echo
    printf '%s\t%dx%d\t%d\t%d\t%d/%d/%d\t%d\t%d\t%s\t%s\n' \
        "$spins" "$MESH_X" "$MESH_Y" "$h0_count" "$cores_per_h0" \
        "$H0_MVMS" "$H1_MVMS" "$CROSS_MVMS" "$TIMING_ONLY" \
        "$USE_RAMULATOR" "$binary" "$log" >> "$manifest"

    if [[ $DRY_RUN == 1 ]]; then
        continue
    fi

    set +e
    { time "${command[@]}"; } 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -e
    if ((rc != 0)); then
        echo "FAILED spins=$spins; see $log" >&2
        failures=$((failures + 1))
        if [[ $CONTINUE_ON_ERROR != 1 ]]; then
            exit "$rc"
        fi
    elif [[ ! -x $binary ]]; then
        echo "build returned success but binary is missing: $binary" >&2
        exit 1
    else
        echo "PASS spins=$spins binary=$binary"
    fi
done

if ((failures)); then
    echo "completed with $failures failed configuration(s)" >&2
    exit 1
fi
echo "all requested VCS builds completed; manifest=$manifest"
