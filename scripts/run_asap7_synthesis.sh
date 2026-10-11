#!/usr/bin/env bash
set -euo pipefail

TOP=${TOP:-mvm}
MVM_COUNT=${MVM_COUNT:-4}
STATE_ENTRY_COUNT=${STATE_ENTRY_COUNT:-256}
SYNTH_PREFIX=${SYNTH_PREFIX:-/home/rsappidi/miniforge3/envs/azilla-synth}
AZILLA_TOOL_ROOT=${AZILLA_TOOL_ROOT:-/home/rsappidi/.local}
ASAP7_ROOT=${ASAP7_ROOT:-$AZILLA_TOOL_ROOT/share/azilla/asap7sc7p5t_28}
SV_ELAB_ROOT=${SV_ELAB_ROOT:-$AZILLA_TOOL_ROOT/src/yosys-slang}
OUTPUT_ROOT=${OUTPUT_ROOT:-build/synthesis/asap7}

yosys="$SYNTH_PREFIX/bin/yosys"
plugin="$SV_ELAB_ROOT/build/slang.so"
liberty="$ASAP7_ROOT/LIB/NLDM/extracted_rvt_tt/asap7sc7p5t_MERGED_RVT_TT.lib"

case "$TOP" in
  mvm)
    sources="rtl/ising_pkg.sv rtl/mvm.sv"
    parameters=""
    tag=mvm
    ;;
  spin_core)
    sources="rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv rtl/core.sv"
    parameters=""
    tag=spin_core
    ;;
  hierarchy_node)
    sources="rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/banked_state_sram.sv rtl/symmetric_mvm.sv rtl/hierarchy_node.sv"
    parameters="-G MVM_COUNT=$MVM_COUNT -G STATE_ENTRY_COUNT=$STATE_ENTRY_COUNT"
    tag="hierarchy_node_mvm${MVM_COUNT}_states${STATE_ENTRY_COUNT}"
    ;;
  *)
    echo "Unsupported TOP=$TOP (use mvm, spin_core, or hierarchy_node)" >&2
    exit 2
    ;;
esac

out="$OUTPUT_ROOT/$tag"
mkdir -p "$out"
constraint_file="$OUTPUT_ROOT/abc.constr"
printf '%s\n' \
  'set_driving_cell BUFx2_ASAP7_75t_R' \
  'set_load 1.0' > "$constraint_file"

# Registers and multiported arrays are mapped to cells.  Only the explicitly
# ram_style-tagged J-block weight memories remain as SRAM macro placeholders.
commands="read_slang $sources --top $TOP $parameters;
hierarchy -check -top $TOP;
proc; opt; memory_dff; memory_collect; flatten;
memory_map -attr !ram_style; opt; techmap; opt;
dfflibmap -liberty $liberty;
abc -liberty $liberty -constr $constraint_file -D 1000; clean;
tee -o $out/stat.txt stat -liberty $liberty;
tee -o $out/stat.json stat -liberty $liberty -json;
write_verilog -noattr -noexpr $out/netlist.v"

/usr/bin/time -v -o "$out/wall_time.txt" \
  "$yosys" -m "$plugin" -ql "$out/yosys.log" -p "$commands"

cat "$out/stat.txt"
