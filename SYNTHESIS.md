# ASAP7 logic-synthesis workflow

The synthesis flow is an early, pre-layout feasibility check. It maps the
SystemVerilog RTL to ASAP7 RVT standard cells with Yosys/ABC at the TT corner.
It does not include floorplanning, routing, clock-tree synthesis, extracted
parasitics, SRAM characterization, package links, power delivery, or thermal
analysis.

ABC is asked to meet a 1 ns target using an ASAP7 `BUFx2` input driver and a
1 fF output load. The resulting ABC-reported delay is a pre-layout critical
path estimate, not a signoff clock frequency.

Install the tools and public ASAP7 library outside the repository:

```bash
BUILD_JOBS=2 scripts/setup_yosys_asap7.sh
```

Run representative blocks:

```bash
TOP=mvm scripts/run_asap7_synthesis.sh
TOP=spin_core scripts/run_asap7_synthesis.sh
TOP=hierarchy_node MVM_COUNT=4 STATE_ENTRY_COUNT=256 \
  scripts/run_asap7_synthesis.sh
```

Results are written below `build/synthesis/asap7/`. The flow maps ordinary
register arrays and multiported state/result tables to standard cells, while
leaving `ram_style`-tagged J-block memories as `$mem_v2` SRAM placeholders.
Consequently, the reported chip area excludes weight SRAM macro area. Always
report the inferred memory dimensions and account for characterized SRAM
macros separately before making an area or frequency claim.

Hierarchy state storage is implemented as eight interleaved synchronous 1R1W
SRAM banks. Operand fetches to different banks proceed concurrently. A fixed
lane-order arbiter serializes same-bank conflicts, and the RTL/Python engine
models include the fetch and conflict cycles. These banks also remain SRAM
macro placeholders in the standard-cell area report.

## Arithmetic and SRAM-boundary requirements

Both endpoint and symmetric MVMs use a balanced combinational reduction for
each 32-weight row. Signed int8 weights are extended to nine bits before
conditional negation (so negating -128 produces +128); the 32 terms are
reduced in 14 signed bits and extended to the 32-bit accumulator. This adds
no pipeline registers or iteration cycles and preserves transpose support.

When replacing SRAM arrays for logic synthesis, retain the bank arbiter,
request decoding, response selection, and valid/tag control. Blackboxing the
entire `banked_state_sram` wrapper omits that logic and cannot establish full
node logic area or timing. Abstract-array synthesis still does not establish
characterized SRAM access timing or physical timing closure. Report setup
and hold separately, include hold-repair cells in area, and state boundary
loading, clock uncertainty, corner, and timing-coverage limitations.
