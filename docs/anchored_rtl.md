# Anchored CIR RTL (H0-anchored symmetric ownership, accumulate-at-owner)

RTL for the C++ exact-event mode `--execution-mode cir-h0-anchored
--anchored-accumulate 1` (`docs/cpp_exact_model.md`, "H0-anchored symmetric
ownership").  The C++ model is the cycle-level specification; the RTL is a
swap-in set under `rtl/experimental/anchored/` in the style of the streaming
endpoint candidate (`docs/streaming_endpoints.md`): changed modules keep
their base module name and are compiled **instead of** the base file.
Nothing under `rtl/*.sv`, `tb/ising_mesh_tb.sv` or the existing `tb/Makefile`
targets is modified.

## File map

| anchored file (`rtl/experimental/anchored/`) | replaces | change |
|---|---|---|
| `h0_uplink.sv` | new | per-H0 uplink: packet-locked round-robin over engines with remote partials; accumulate mode (per-block accumulator, touched table, ascending flush after node done) or FIFO mode; two FIFOs (sibling / mesh) of `FIFO_PACKETS` four-beat packets; output queue lock |
| `uplink_injection_arbiter.sv` | new | per-H1 packet-locked round-robin over the H0 uplinks' mesh streams |
| `h0_tile.sv` | `rtl/h0_tile.sv` | global-indexed state table (`STATE_ENTRY_COUNT` = total blocks) filled through an external port; `h0_uplink` beside the unchanged `h0_adapter`; `node_done_o`, `uplink_flushed_o` |
| `h1_child_adapter.sv` | `rtl/h1_child_adapter.sv` | adds `PARENT_FIRST` (default 0 = base behaviour) |
| `h1_tile.sv` | `rtl/h1_tile.sv` | crossbar sources = H1 engines then `H0_COUNT` uplink sibling streams, `PARENT_FIRST=1`; exposes per-H0 fill ports, mesh streams and completion flags; H1 node and its snapshot sequence unchanged (no jobs) |
| `top_node.sv` | `rtl/top_node.sv` | the uplink injection stream takes the cross node's slot of the local injection arbiter; cross node instantiated, idle, tied off (fatal if it ever produces a partial) |
| `mesh_h1_tile.sv` | `rtl/mesh_h1_tile.sv` | instantiates `uplink_injection_arbiter`; passes the new ports through |
| `ising_mesh.sv` | `rtl/ising_mesh.sv` | global H0 state indices; new ports per node |

Unchanged and shared with the base build: `hierarchy_node.sv` (engine),
`h0_adapter.sv`, `h1_noc_adapter.sv`, `cross_h1_node.sv`,
`azilla_floo_router.sv` and FlooNoC, `core.sv`, `mvm.sv`, `symmetric_mvm.sv`,
`banked_state_sram.sv`, `j_block_sram.sv`, `dram_weight_streamer.sv`,
`tb/ramulator_node_frontend.sv`, `tb/ramulator_dpi_bridge.sv`,
`tb/ramulator_dpi.cpp`.  Compile order:
`scripts/run_anchored_rtl_differentials.py::RTL_SOURCES` (also the
`anchored-mesh-lint` / `anchored-mesh-build` targets of `tb/Makefile`).

Testbench: `tb/ising_mesh_anchored_tb.sv` (derived from
`tb/ising_mesh_tb.sv`; Ramulator mode only).

## Interfaces

Engine side (`hierarchy_node` is unchanged): each engine's partial stream is
offered to both the H0 adapter (local block IDs) and the uplink (remote block
IDs); `partial_ready` is the OR of the two, which never contend.

`h0_uplink` (per H0):

- input: `partial_*` from the engines; the selected engine's `partial_ready`
  is 1 in accumulate mode (one read-modify-write beat per cycle) or "FIFO has
  room before this cycle's pop" in FIFO mode; selection is locked for the
  packet and the round-robin pointer advances past the engine on `last`;
- accumulator: `TOTAL_BLOCK_COUNT x SPIN_COUNT x ACC_W` register file (128 B
  per block), combinational read; a `touched` bit per block; the first packet
  to a block in an iteration overwrites, later packets add;
- flush (accumulate): while `node_done_i`, the lowest touched block at or
  above the cursor is emitted as a four-beat packet into its destination FIFO
  one beat per cycle when that FIFO has room after this cycle's pop;
- output: `sib_*` (valid/ready/data/block/last) toward the H1 crossbar and
  `mesh_*` (full NoC flit: type `NOC_PARTIAL`, dest x/y from
  `block / BLOCKS_PER_H1`, `source_id = NODE_ID`, `epoch_i`, block, last)
  toward the uplink arbiter; the output locks on the queue of the packet in
  flight and, between packets, switches queue when the head is not accepted;
- `flushed_o`: node done and both FIFOs empty and (accumulate) no touched
  block left to flush.

`h0_tile` state fill: `state_fill_valid_i / ready_o / index_i (global) /
data_i / done_i`.  The tile leaves `TILE_IDLE` on `iter_start` into
`TILE_FILL_STATES` (or straight into `TILE_START_NODE` when `state_fill_done_i`
is asserted together with `iter_start`), accepts one write per cycle, and
starts the node on the cycle after `state_fill_done_i`.  This reproduces the
model's rule "node ready at iter_start + (distinct source blocks) + 2" exactly
when the controller writes the distinct blocks back to back, which is what
the testbench does.

`h1_child_adapter` with `PARENT_FIRST=1`: an unlocked child takes the mesh
(parent) stream first, then the lowest-numbered valid source (H1 engines
`0..H1_MVM_COUNT-1`, uplinks `H1_MVM_COUNT..H1_MVM_COUNT+H0_COUNT-1`); locks
hold for the packet.  Child ready is constant 1 during the iteration.

`uplink_injection_arbiter`: packet-locked round robin over
`H0_COUNT` mesh streams; its output is `top_node.uplink_tx_*`, arbitrated
against the H1 adapter's STATE/EPOCH_DONE flits by the unchanged local
injection arbiter (uplink has priority when unlocked, lock through `last`).

Completion: the testbench opens the quiet window before `EPOCH_DONE` only
when `&cross_iter_done_o && &h0_node_done_o && &h0_uplink_flushed_o`
(model: all cross nodes done, all H0 nodes done, all uplinks flushed), then
the existing four-quiet-cycle barrier, completion injection and core
`partials_done` path are used unchanged.

The testbench runs the fill as a background process that overlaps the cross
start and the schedule dispatch, exactly like the autonomous H0/H1 snapshot
loads of the base tiles: the model dispatches from `compute_start` while
each H0 node starts at its own fill time and the weight streamers prefetch
until the node is RUN.  Joining the fill before the dispatch (the first
testbench version) delayed every node's first command by the streamer
prefetch latency whenever a fill outlasted the publication phase; it showed
only on the dense 1x1 geometries (fills of 128-2048 blocks, no publications:
+54 iteration cycles at 4K spins, +66 at 64K) and was fixed on October 15.

## Schedule file (model -> testbench)

`azilla_exact simulate-exact-events --execution-mode cir-h0-anchored
--anchored-accumulate 1 ... --dump-anchored-schedule PATH` writes

```
geometry <mesh_x> <mesh_y> <h0_per_h1> <cores_per_h0> <h0_mvms> <accumulate> <uplink_packets>
job <owner_h0> <block_a> <block_b>     one line per job, per owner H0 in dispatch order
pub <block> <h1>                        explicit (block, consuming H1) publications
end
```

It is the output of `compile_anchored_schedule` on the same records `run()`
compiles.  `+ANCHORED_SCHEDULE=PATH` is mandatory for the testbench, which
validates the geometry line against its parameters, that every job is an
occupied block pair of the dataset with an endpoint owner, and that the job
count equals the dataset's occupied pair count; it never recomputes owners.
Each H0's fill list is the ascending set of distinct `block_a`/`block_b` of
its jobs (data from the frozen global state vector).

## Running the gate

```bash
# syntax/lint only (Verilator), no simulation
make -C tb anchored-mesh-lint
# VCS differentials (sequential, nice -n 5, small geometries), writes
# results/anchored_rtl_20261015/{cases,build,summary.json,REPORT.md}
python3 scripts/run_anchored_rtl_differentials.py [--only CASE ...] [--timing-only]
```

The script runs the C++ model (`build/cpp_exact_flat/azilla_exact`, serial
Ramulator bridge `build/cycle_model_ramulator/libazilla_ramulator.so`,
`tb/ramulator_128x32.yaml`, no idle modulus, FIFO depth 4, baseline links),
builds one VCS binary per configuration (cached by RTL source hashes), runs
it with `+NOC_EVENT_FILE` and compares, with zero tolerance:
initialization/iteration/total cycles; every accepted NoC transfer tuple
(cycle, scope, node, direction, packet_type, source_id, epoch, block_id,
dest_x, dest_y, last); aggregate NoC flit/stall totals; the job count.
Without `--timing-only` the testbench also compares every next-state bit
against its golden update (arithmetic through uplink, accumulator, crossbar
and mesh).  Source, binary, library and YAML hashes are recorded and
re-checked after each case.

## What is and is not verified

Verified (see `results/anchored_rtl_20261015/REPORT.md` for the table):
cycle-exact agreement between the anchored RTL and the C++ model, accumulate
mode, 1-4 H0 engines, uplink FIFOs of 4 and 1 packets, one timing iteration,
on small geometries (2x1 with 2 H0 x 2 cores, 2x1 with 2 H0 x 4 cores dense,
mod3, sparse and uneven, 2x2 with 1 H0 x 4 cores dense, 4x2 with 1 H0 x 4
cores dense, 4x4 with 2 H0 x 2 cores sparse, one case with an H0 that owns
nothing), the dense 1x1 reductions (2 H0 x 64 and 2 H0 x 256 cores) and
the nominal single-H1 dense 64K geometry (8 H0 x 256 cores, timing-only,
2.5M iteration cycles), plus the functional next-state check of the small
runs (every bit of
the next state against the testbench golden update, which exercises the
uplink arithmetic, accumulator, crossbar and mesh delivery).  Up to 2,048
spins and 3,301 accepted NoC transfers per run.

Not verified / known differences:

- Remote state delivery is abstracted on both sides: the model starts an H0
  node at `iter_start + distinct sources + 2` regardless of when the
  `NOC_STATE` publications arrive; the testbench fills the H0 tables from the
  frozen global state vector on that schedule (a controller role, like the
  base testbench's state publication).  The RTL does not implement a timed
  NoC -> H1 -> H0 state forwarding path; the published states still land in
  the idle cross node's table as in CIR.  A real design needs the H1 to
  forward published blocks to its H0s and would have to delay node start
  until they arrive.
- The accumulator is a combinational-read register file sized
  `TOTAL_BLOCK_COUNT x 128 B` per H0 and the flush uses a combinational
  find-first over the touched bitmap.  At nominal sizes (32,768 blocks for
  1M spins) this is an SRAM with a pipelined read and a hierarchical bitmap;
  neither the area nor the extra latency is modelled.
- FIFO mode (`ACCUMULATE=0`) is checked on two cases only (smoke 256 and
  dense 512, 2x1).  The dense case first failed because the model's
  quiet-window condition ignored the uplink FIFOs in FIFO mode (`EPOCH_DONE`
  injected 16 cycles before a queued sibling packet left the uplink); the
  model was fixed on October 15 (`H0Uplink::drained()`, see
  `docs/cpp_exact_model.md`) and the case passes against the fixed binary.
  Accumulate-mode results were unaffected by the fix.
- No multi-iteration, nondefault link, chiplet-latency, idle-modulus,
  non-nominal FIFO depth, nominal 8-H0 geometries, 64K+ sizes, dense/G-set
  quality, DRAM-internal traces, synthesis, area or power.
- The owner assignment and the fill lists are software; the gate checks that
  RTL and model execute the same schedule, not that the schedule is good.

Accuracy label of the model for this mode stays
`cycle-structured-unverified` in general; the differential evidence covers
exactly the configurations in the report.
