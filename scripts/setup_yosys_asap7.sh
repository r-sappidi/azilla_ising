#!/usr/bin/env bash
set -euo pipefail

# Install the open-source synthesis stack outside the repository.  Override
# these paths when sharing a machine or maintaining a central tool install.
MAMBA=${MAMBA:-/home/rsappidi/miniforge3/bin/mamba}
SYNTH_PREFIX=${SYNTH_PREFIX:-/home/rsappidi/miniforge3/envs/azilla-synth}
AZILLA_TOOL_ROOT=${AZILLA_TOOL_ROOT:-/home/rsappidi/.local}
ASAP7_ROOT=${ASAP7_ROOT:-$AZILLA_TOOL_ROOT/share/azilla/asap7sc7p5t_28}
SV_ELAB_ROOT=${SV_ELAB_ROOT:-$AZILLA_TOOL_ROOT/src/yosys-slang}
BUILD_JOBS=${BUILD_JOBS:-2}

if [[ -d "$SYNTH_PREFIX/conda-meta" ]]; then
  "$MAMBA" install -y -p "$SYNTH_PREFIX" -c conda-forge \
    yosys p7zip cmake make
else
  "$MAMBA" create -y -p "$SYNTH_PREFIX" -c conda-forge \
    yosys p7zip cmake make
fi

if [[ ! -d "$ASAP7_ROOT/.git" ]]; then
  mkdir -p "$(dirname "$ASAP7_ROOT")"
  git clone --depth 1 \
    https://github.com/The-OpenROAD-Project/asap7sc7p5t_28.git \
    "$ASAP7_ROOT"
fi

if [[ ! -d "$SV_ELAB_ROOT/.git" ]]; then
  mkdir -p "$(dirname "$SV_ELAB_ROOT")"
  git clone --depth 1 --recurse-submodules --shallow-submodules \
    https://github.com/povik/yosys-slang.git "$SV_ELAB_ROOT"
fi

cmake -S "$SV_ELAB_ROOT" -B "$SV_ELAB_ROOT/build" \
  -DYOSYS_CONFIG="$SYNTH_PREFIX/bin/yosys-config"
cmake --build "$SV_ELAB_ROOT/build" -j"$BUILD_JOBS"

archive_dir="$ASAP7_ROOT/LIB/NLDM"
extract_dir="$archive_dir/extracted_rvt_tt"
mkdir -p "$extract_dir"
for archive in "$archive_dir"/asap7sc7p5t_{AO,INVBUF,OA,SEQ,SIMPLE}_RVT_TT_*.lib.7z; do
  "$SYNTH_PREFIX/bin/7z" x -y -o"$extract_dir" "$archive" >/dev/null
done

# ABC needs inverters/buffers and combinational cells in one Liberty library;
# dfflibmap additionally needs the sequential cells.  The three source files
# use the same units and lookup-table templates.
simple="$extract_dir/asap7sc7p5t_SIMPLE_RVT_TT_nldm_211120.lib"
invbuf="$extract_dir/asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib"
seq="$extract_dir/asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib"
merged="$extract_dir/asap7sc7p5t_MERGED_RVT_TT.lib"
head -n -1 "$simple" > "$merged"
tail -n +157 "$invbuf" | head -n -1 >> "$merged"
tail -n +158 "$seq" | head -n -1 >> "$merged"
echo '}' >> "$merged"

echo "Yosys:  $SYNTH_PREFIX/bin/yosys"
echo "SV:     $SV_ELAB_ROOT/build/slang.so"
echo "Liberty: $merged"
