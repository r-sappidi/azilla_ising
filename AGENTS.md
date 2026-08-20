# Azilla Ising Accelerator — Agent Handoff and Working Instructions

## Scope and repository

This file is the authoritative handoff for work in this repository:

```text
/home/rsappidi/research/azilla_ising
```

The user's shell may initially open in the older GVSOC repository at
`/home/rsappidi/research/ising_sim/gvsoc_ising`. Do not accidentally implement
new RTL there. Unless the user explicitly says otherwise, current RTL,
FlooNoC, testbench, and Ramulator work belongs in `azilla_ising`.

Read these files before making architectural changes:

- `AGENTS.md` (this file)
- `RTL_MODULE_REFERENCE.md` for the detailed module and signal reference
- `tb/README.md` for build and test commands
- The relevant RTL and testbench source; documentation is not a substitute for
  checking the implementation.

The worktree is intentionally dirty and contains substantial uncommitted and
untracked project work. Preserve it. Do not reset, clean, delete, or overwrite
unrelated files. Do not commit or push unless the user explicitly requests it.

## Collaboration rule

The user wants to make key architectural decisions. If implementation requires
a choice not already resolved below and the alternatives materially affect the
architecture, timing model, interfaces, correctness contract, or paper claims,
stop and ask before choosing. Ordinary local implementation details that
preserve these contracts do not require clarification.

In particular, ask before changing:

- the 32-spin block size;
- binary-state semantics or update equation;
- interaction ownership or symmetric block evaluation;
- dynamic scheduling to static scheduling;
- hierarchy dimensions or topology assumptions in the baseline architecture;
- partial-packet format or hierarchy-boundary protocol;
- the number or placement of physical memory interfaces;
- DRAM timing assumptions used for reported performance;
- accumulator/weight precision;
- whether a component is synthesizable RTL or testbench-only infrastructure.

## Architectural objective

Azilla is a hierarchical accelerator for synchronous Ising updates. State is
binary:

```text
stored bit 1 = spin +1
stored bit 0 = spin -1
```

The conceptual update is:

```text
field[i]  = a*x[i] + b*sum_j(J[i,j]*x[j]) + noise[i]
x_next[i] = +1 if field[i] >= 0, otherwise -1
```

Current baseline properties:

- `J` is signed int8.
- A spin core owns 32 spins.
- `ACC_W` is 32 bits per spin.
- The streamed data width is 256 bits.
- A 32-by-32 int8 J block is 1,024 bytes or 32 256-bit beats.
- A 32-element partial with 32-bit elements is 1,024 bits or four 256-bit
  flits.
- All hierarchy levels operate on the same frozen `state_current` during an
  iteration.
- No state changes until all required partials have arrived and the system
  commits `state_next` to `state_current`.
- Addition/reduction is commutative, but epoch boundaries and packet framing
  must remain correct.

## Hierarchical interaction ownership

Every interaction is computed exactly once at the lowest level that contains
both endpoints:

| Endpoint relationship | Compute owner |
|---|---|
| Same 32-spin block | `spin_core` |
| Different cores in the same H0 | H0 `hierarchy_node` |
| Different H0s in the same H1 | H1 `hierarchy_node` |
| Different H1s | `cross_h1_node` assigned to a top-level mesh location |

For a symmetric off-diagonal block between state blocks A and B, fetch the
block once and update both endpoints:

```text
partial_A = J_AB * x_B
partial_B = transpose(J_AB) * x_A
```

The MVM datapath exploits binary spins: multiplication by a spin is signed
add/subtract. Do not replace this with general multipliers without a specific
reason.

Dense scheduling is deterministic. Sparse support is intended to skip wholly
zero 32-by-32 blocks while retaining the same dense-block datapath; nonzero
blocks may contain zero padding. `SKIP_ZERO_BLOCKS` is currently testbench-side
schedule behavior, not autonomous hardware discovery.

## Physical organization

The instantiated hierarchy is:

```text
ising_mesh
└── mesh_h1_tile [rectangular mesh]
    ├── h1_tile
    │   ├── h1 hierarchy_node
    │   └── h0_tile [H0_COUNT]
    │       ├── h0 hierarchy_node
    │       ├── h0_adapter
    │       └── spin_core [CORES_PER_H0]
    ├── h1_noc_adapter
    └── top_node
        ├── FlooNoC-based mesh router wrapper
        └── cross_h1_node
```

H0-to-core connectivity is a local fanout/collection network implemented by
`h0_adapter`. H0s beneath one H1 connect through `h1_child_adapter`. Top-level
cross-H1 traffic uses a 2D mesh and FlooNoC router infrastructure. Compute is
decoupled from the network adapter so the hierarchy compute nodes do not embed
topology-specific routing behavior.

The router uses ready/valid links, XY routing, per-input buffering,
round-robin output arbitration, and wormhole packet locking through the final
flit. Locking means an output remains assigned to one multi-flit packet until
its accepted `last` flit, preventing packet interleaving.

## Scheduling boundary

The current baseline uses dynamic ready/valid dispatch in the RTL, but the
global work schedule is generated by the testbench. There is no autonomous RTL
scheduler or full DMA command generator yet.

The testbench determines:

- which block pair is computed at H0, H1, or cross-H1 level;
- which MVM engine receives a command;
- the two state-table indices;
- the two global destination block IDs;
- when each level's schedule is complete;
- state-publication destinations and iteration lifecycle.

This is deliberate: it permits functional and NoC/memory timing evaluation
before scheduler/DMA RTL is implemented. Do not describe testbench scheduling
as existing hardware.

## Important synthesizable modules

- `rtl/ising_pkg.sv`: shared architectural widths, packet types, constants.
- `rtl/core.sv`: 32-spin endpoint; stores current/next state, local J block,
  local accumulator, H0 and external accumulators, coefficients and LFSR.
- `rtl/j_block_sram.sv`: local J-block storage wrapper.
- `rtl/mvm.sv`: one-direction MVM.
- `rtl/symmetric_mvm.sv`: symmetric block evaluation producing both endpoint
  partials.
- `rtl/hierarchy_node.sv`: generic H0/H1 block-compute node with local state
  table, paired MVM behavior, double-buffered engine slots, result buffering,
  and ready/valid interfaces.
- `rtl/h0_adapter.sv`: routes H0-local partial packets to 32-spin cores and
  collects/publishes local state.
- `rtl/h0_tile.sv`: H0 hierarchy node plus its cores and adapter.
- `rtl/h1_child_adapter.sv`: connects one H1 node to its child H0 tiles.
- `rtl/h1_tile.sv`: H1 compute node plus H0 children.
- `rtl/h1_noc_adapter.sv`: translates H1 hierarchy traffic to/from top-level
  NoC packets.
- `rtl/cross_h1_node.sv`: top-level cross-H1 block compute and state table.
- `rtl/azilla_floo_router.sv`: Azilla packet wrapper around FlooNoC routing.
- `rtl/top_node.sv`: mesh router plus cross-H1 endpoint and local arbitration.
- `rtl/mesh_h1_tile.sv`: complete physical mesh location.
- `rtl/ising_mesh.sv`: full-system RTL wrapper.
- `rtl/dram_weight_streamer.sv`: synthesizable tagged read generator,
  out-of-order response reassembler, two buffers per MVM engine, and ordered
  block delivery to existing hierarchy-node ports.

FlooNoC sources are under `rtl/FlooNoC/`; the compilation file list is
`rtl/floo_router_files.f`. Treat vendored FlooNoC as third-party code and avoid
editing it unless absolutely necessary. Prefer changes in the Azilla wrapper.

`h0_node.sv`, older topology-named adapters, and earlier standalone hierarchy
experiments may be obsolete. Verify instantiation with `rg` before deleting
anything; do not infer obsolescence from the filename alone.

## Ready/valid and packet contracts

A transfer occurs only on a rising edge with both ready and valid asserted.
While valid is asserted and ready is low, the producer must hold payload and
metadata stable.

Current NoC packet types include:

- `NOC_STATE`: one-flit frozen-state publication;
- `NOC_PARTIAL`: four-flit 32-element accumulator partial;
- `NOC_EPOCH_DONE`: one-flit completion marker.

Multi-flit partials cannot be interleaved on one locked stream. Destination
block ID identifies the 32-spin endpoint. Epoch metadata prevents stale
iteration traffic from being accepted at hierarchy boundaries. Inside the
spin core, partials are accumulated only when their stream handshake succeeds.

Do not add metadata merely for convenience. Previous design discussion
explicitly minimized core-facing signals: global control owns iteration end;
the core accepts framed local/external partial streams and should not need to
know their source hierarchy when the routing/control layer can guarantee the
contract.

## DRAM/Ramulator integration

Ramulator 2.1 is vendored at `third_party/ramulator2`. The current memory model
is testbench integration around a synthesizable streamer:

```text
testbench schedule command
    -> dram_weight_streamer
    -> tagged 32-byte read requests
    -> ramulator_dpi_bridge
    -> Ramulator timing model
    -> sparse dataset-backed payload lookup
    -> tagged out-of-order responses
    -> streamer reassembly
    -> hierarchy_node command + 32 weight beats
```

Each physical H0, H1, and cross-H1 compute node receives an independent local
Ramulator memory-system instance. There is no contention between different
node-local interfaces. Engines within one node share that node's interface.

Memory details:

- Default projected interface: 128 pins at 32 Gb/s per pin.
- Native transaction: 32 bytes / 256 bits.
- One 32-by-32 J block: 32 read transactions.
- Default RTL request and response lanes: 16.
- Maximum outstanding reads per physical interface: 64.
- Tags encode `{engine_id, buffer_id, beat_index}`.
- Engine ID comes from the scheduler-selected engine port.
- Buffer ID is allocated by the streamer's two-buffer engine state.
- Beat index is the request's row/beat within the 32-beat J block.
- Responses may return out of order; the tag selects exact reassembly storage.
- Complete blocks are delivered to each engine in schedule order.
- Address layout is a rectangular global block matrix. The streamer canonicalizes
  a symmetric pair to `(min(block_a, block_b), max(...))`, so one symmetric J
  block is fetched once.

Relevant files:

- `rtl/dram_weight_streamer.sv`
- `tb/ramulator_node_frontend.sv`
- `tb/ramulator_dpi_bridge.sv`
- `tb/ramulator_dpi.cpp`
- `tb/ramulator_config.py`
- `tb/ramulator_128x32.yaml`
- `tb/dram_weight_streamer_tb.sv`

The 32-Gb/s-per-pin configuration is a projected architectural model derived
from Ramulator's GDDR6 timing model, not a vendor-qualified GDDR specification.
Keep that caveat in reports and paper-facing claims.

Ramulator source/core IDs are local to each independent memory system. In the
DPI implementation, `system_id` selects the physical memory instance, while
the request source ID passed inside that instance must remain zero. Passing the
global system ID as the internal source ID caused memory corruption and a
cleanup abort; this has been fixed in `tb/ramulator_dpi.cpp`.

## Testbench lifecycle

`tb/ising_mesh_tb.sv` performs the complete functional sequence:

1. Parse and validate the dataset and geometry.
2. Reset the DUT.
3. Initialize every core's state, seed, coefficients, and core-local J block.
4. Start an iteration with a frozen global state.
5. Publish state blocks to the required cross-H1 compute locations.
6. Issue H0, H1, and cross-H1 block schedules.
7. In direct mode, stream J beats from the testbench.
8. In Ramulator mode, let each tagged streamer fetch/reassemble its J blocks.
9. Wait for all streamers to become idle before asserting schedule-done. This
   prevents a hierarchy node from declaring completion while fetched commands
   are still buffered upstream.
10. Wait for compute and NoC traffic to drain and deliver completion markers.
11. Compare every `state_next` bit against the direct software golden update.
12. Commit for the next iteration.

The dataset format is:

```text
<vertex_count> <known_best_cut>
<one_based_source> <one_based_destination> <signed_int8_weight>
...
```

Missing entries are weight zero. Symmetric datasets should include both
directed records unless the loader is deliberately changed.

## Build and regression commands

Run commands from the repository root.

Spin-core regression:

```bash
make -C tb run
```

Full direct-weight hierarchy/FlooNoC regression:

```bash
make -C tb floo-mesh-test DATASET=g256_smoke.txt SKIP_ZERO_BLOCKS=0
```

Standalone tagged Ramulator streamer regression:

```bash
make -C tb dram-streamer-test
```

Full Ramulator-backed hierarchy regression:

```bash
make -C tb floo-mesh-dram-test DATASET=g256_smoke.txt SKIP_ZERO_BLOCKS=0
```

Important Make parameters and current defaults:

| Parameter | Default | Meaning |
|---|---:|---|
| `MESH_X_COUNT` | 2 | Top-level mesh width |
| `MESH_Y_COUNT` | 1 | Top-level mesh height |
| `H0_COUNT` | 2 | H0 tiles per H1 |
| `CORES_PER_H0` | 2 | 32-spin cores per H0 in smoke test |
| `H0_MVM_COUNT` | 1 | MVM engines in each H0 node |
| `H1_MVM_COUNT` | 1 | MVM engines in each H1 node |
| `CROSS_MVM_COUNT` | 1 | Cross-H1 engines per mesh node |
| `FIFO_DEPTH` | 4 | Partial/NoC buffering parameter |
| `ITERATION_COUNT` | 1 | Iterations |
| `SKIP_ZERO_BLOCKS` | 0 | Testbench skips all-zero J blocks when 1 |
| `MEM_PIN_COUNT` | 128 | Projected physical memory pins/interface |
| `MEM_PIN_GBPS` | 32 | Projected rate per pin |
| `MEM_LANES` | 16 | Parallel 256-bit RTL transaction lanes |

The dataset spin count must equal:

```text
MESH_X_COUNT * MESH_Y_COUNT * H0_COUNT * CORES_PER_H0 * 32
```

The testbench rejects unsupported geometry. Do not silently relax its
power-of-two and sizing checks without understanding address/index widths.

## Last validated results

As of 2026-08-18, these tests passed:

### Standalone DRAM streamer

```text
DRAM[0] accepted=64 rejected=0 avg_latency_ticks=113.00 max_latency_ticks=136
dram_weight_streamer_tb PASS
```

This issued two 32-beat blocks through two engines/buffers and checked every
returned weight against the dataset.

### Full 256-spin Ramulator-backed hierarchy

Command:

```bash
make -C tb floo-mesh-dram-test DATASET=g256_smoke.txt SKIP_ZERO_BLOCKS=0
```

Result:

```text
initialization complete at cycle 267
state publication drained at cycle 289
schedule issued at cycle 1036
cross compute done at cycle 1119
iteration 0 PASS at cycle 1165
total_cycles=1166
```

The final state matched the software golden model exactly. Request counts were:

```text
H0 systems 0..3:     32 reads each
H1 systems 4..5:    128 reads each
cross system 6:     512 reads
cross system 7:       0 reads (no assigned pair in this tiny geometry)
total:               864 reads
```

Reported latency maxima were 126–128 Ramulator ticks for active full-system
interfaces. `git diff --check` passed after implementation.

The direct-weight full hierarchy regression also passed with an exact golden
match before the Ramulator integration. Its previously observed total was
1,300 cycles for the same 256-spin test; do not interpret the lower Ramulator
number as a general DRAM speedup without checking the different streaming and
overlap paths.

## Current limitations and honest claims

- Global scheduling is testbench-generated, not RTL.
- The memory payload store is a sparse software map populated from the dataset;
  Ramulator models transaction timing, queues, banks, and completion order, but
  does not store the full dense J matrix bytes internally.
- The model exhaustively performs instantiated H0/H1/cross arithmetic for the
  small RTL test. Larger prior GVSOC studies may use timing abstraction; do not
  conflate those results with this RTL regression.
- The current smoke test is 256 spins and deliberately tiny.
- One independent memory interface is modeled per hierarchy compute node.
- No CDC FIFO or production DRAM PHY/controller RTL is implemented yet.
- The pin-rate model and `MEM_LANES` are separate: physical aggregate bandwidth
  configures Ramulator, while `MEM_LANES` describes the parallel simulator/RTL
  transaction interface. A real implementation would serialize/deserialise
  behind a narrower core-clock interface and use CDC FIFOs.
- `noise_decay_i` is exposed in parts of the RTL, but the controller/testbench
  provides the already-scaled signed noise amplitude used by the core.
- Zero-block skipping avoids requests only because the testbench/compiler knows
  block occupancy before dispatch. Sending a block from DRAM and dropping it
  afterward would not save memory bandwidth.
- FlooNoC is integrated at the routing level through an Azilla wrapper; Azilla
  does not use the full AXI/chimney endpoint stack.

## Recommended next work

Unless the user redirects the project, useful next steps are:

1. Add explicit statistics for peak outstanding reads, request backpressure,
   per-interface bandwidth, bank/row behavior, and streamer occupancy.
2. Run parameterized memory-lane and MVM-count regressions while preserving the
   64-request cap; save machine-readable results.
3. Add multi-iteration Ramulator-backed full-hierarchy tests and verify each
   iteration against the golden model.
4. Test a denser dataset at a manageable RTL geometry to separate sparse-map
   lookup behavior from network/memory timing.
5. Design the synthesizable schedule/descriptor streamer and DMA boundary.
6. Define real memory-clock/core-clock CDC FIFOs and a narrower production
   controller-side interface.
7. Add assertions for packet stability, epoch correctness, no duplicate block
   ownership, outstanding-count bounds, and complete iteration drain.

Before scaling to large configurations, estimate elaborated RTL size and host
RAM. Full RTL arithmetic scales poorly with spin count; avoid launching a very
large Verilator build/run on the user's laptop without first reporting the
expected resource cost and providing a resumable/logged invocation.

## Coding and verification practices

- Use `rg`/`rg --files` for source discovery.
- Use `apply_patch` for manual file edits.
- Keep third-party modifications isolated and documented.
- Preserve ready/valid stability and avoid combinational handshake loops.
- Prefer derived localparams over duplicate exposed parameters.
- Use signed casts deliberately; SystemVerilog packed-array signedness is easy
  to lose through slicing and concatenation.
- Check index width at minimum configurations (`MVM_COUNT=1`, one child, etc.);
  use guarded `$clog2` widths where required.
- Test both backpressured and non-backpressured paths when modifying streams.
- Run `git diff --check` after edits.
- Report the exact command, geometry, dataset, cycle count, wall time, golden
  comparison, and modeling shortcuts for performance results.
- Do not call a run correct merely because it completed. Correctness requires
  comparison of output states/partials against the golden arithmetic.

## Fresh-session checklist

At the beginning of a new chat:

1. `cd /home/rsappidi/research/azilla_ising`
2. Read this file and the relevant section of `RTL_MODULE_REFERENCE.md`.
3. Run `git status --short`; assume existing changes belong to the user.
4. Inspect the exact modules in scope before answering from memory.
5. Reproduce the smallest relevant passing regression before broad changes.
6. Ask the user before making any unresolved architectural choice listed above.
