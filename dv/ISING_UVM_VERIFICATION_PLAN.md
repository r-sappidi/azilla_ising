# Azilla Ising Accelerator UVM Verification Plan

## 1. Purpose

This document specifies verification of the parameterized Azilla Ising RTL,
from the arithmetic primitives through the complete FlooNoC-connected mesh.
It is both a test plan and an implementation specification for the UVM
environment.

UVM is used for constrained-random simulation, checking, and coverage closure.
It is not mathematical formal verification. Section 12 defines a complementary
SystemVerilog Assertions (SVA) and formal property verification (FPV) plan.

The plan follows the structure used by OpenTitan: every design feature maps to
directed or constrained-random tests, functional coverage, assertions, and
explicit sign-off criteria.

## 2. Verification goals

The environment shall demonstrate that:

1. Every accepted 32x32 signed-int8 interaction block produces the correct two
   signed 32-lane partial vectors.
2. Each interaction is evaluated at exactly one hierarchy level and reaches
   exactly the two intended 32-spin cores.
3. Arbitrary legal backpressure changes timing but never changes, duplicates,
   drops, corrupts, or reorders packet contents illegally.
4. All levels use the frozen `state_current` for an epoch; `state_current`
   changes only on commit.
5. Completion cannot occur until local arithmetic, scheduled hierarchy work,
   result serialization, FIFO contents, and required remote partials are done.
6. The full-system `state_next` equals an independent golden implementation of
   the configured Ising update for every spin and iteration.
7. Parameterized legal configurations elaborate and operate correctly, while
   illegal configurations fail explicitly.
8. Reset, epoch transitions, simultaneous traffic, FIFO-full conditions, and
   arithmetic corner cases are safe.

## 3. Design contract and assumptions

One state bit represents +1 when one and -1 when zero. The intended update is:

```text
sum[i]    = sum over j of J[i,j] * x[j]
field[i]  = a*x[i] + b*sum[i] + noise[i]
x_next[i] = 1 when field[i] >= 0, otherwise 0
```

The global state and J matrix are divided into 32-spin and 32x32 blocks.
Signed J entries are `WEIGHT_W` bits, normally int8. Each off-diagonal block is
evaluated symmetrically:

```text
partial_A = J_AB * x_B
partial_B = transpose(J_AB) * x_A
```

Interaction ownership is:

| Endpoints | Owner |
|---|---|
| Same 32-spin block | `spin_core` |
| Different cores in one H0 | H0 `hierarchy_node` |
| Different H0s in one H1 | H1 `hierarchy_node` |
| Different H1s | `cross_h1_node` selected by the schedule |

The current RTL assumes an external scheduler and memory streamer. UVM drives
the exposed command and weight interfaces. Scheduling correctness is checked
both at the interface boundary and end-to-end, but autonomous schedule
generation and a physical DRAM controller are outside the current DUT.

The verification model shall reproduce the implemented arithmetic exactly,
including signed widths, truncation, overflow behavior, tie rule, LFSR update,
and coefficient placement. Any difference between implemented and intended
behavior is reported as a design issue rather than hidden in the model.

## 4. Verification levels

Verification is divided into reusable benches so failures can be localized.

| Level | DUT | Primary purpose |
|---|---|---|
| L0 | `mvm`, `symmetric_mvm`, `j_block_sram` | Arithmetic and storage |
| L1 | `spin_core` | Local block, partial accumulation, update, commit |
| L2 | `hierarchy_node` | Engine scheduling, buffering, symmetric results |
| L3 | adapters and router | Routing, arbitration, packetization, backpressure |
| L4 | `h0_tile`, `h1_tile`, `top_node` | Subsystem integration |
| L5 | `ising_mesh` | Complete iteration and global correctness |

L0 through L3 run large constrained-random regressions. L5 uses smaller random
systems for exhaustive arithmetic checking and larger systems for protocol,
traffic, and performance-oriented stress.

## 5. UVM environment architecture

```text
ising_virtual_sequence
   |
   +-- control sequencer/driver
   +-- core-init agents [node][h0][core]
   +-- H0 command/weight agents [node][h0][engine]
   +-- H1 command/weight agents [node][engine]
   +-- cross command/weight agents [node][engine]
   +-- state-publication agents [node]
   +-- optional NoC link fault/backpressure agents
   |
   v
ising_mesh DUT
   |
   +-- state and completion monitors
   +-- command/weight monitors
   +-- NoC packet monitors
   +-- assertion bind modules
   |
   v
predictor/reference model --> scoreboard --> coverage subscribers
```

### 5.1 Environment classes

Recommended hierarchy:

```text
ising_base_test
ising_env
ising_env_cfg
ising_virtual_sequencer
ising_scoreboard
ising_reference_model
ising_coverage
```

The environment configuration contains all RTL parameters, active/passive
agent selections, backpressure policy, timeout, dataset or random-graph
configuration, seed, iteration count, and checking/coverage enables.

### 5.2 Transaction types

Define the following sequence items:

- `ising_init_item`: target core, state, seed, coefficients, and diagonal J.
- `ising_block_cmd_item`: hierarchy level, target engine, A/B state indices,
  global A/B block IDs, weight address or complete block.
- `ising_weight_beat_item`: engine, beat number, data, first/last metadata used
  by the verification environment.
- `ising_state_publish_item`: source block, destination coordinate, epoch.
- `ising_noc_packet_item`: packet type, source, destination, epoch, block ID,
  ordered flit payload, and tail position.
- `ising_control_item`: init, iteration start, schedule done, completion,
  commit, and terminal done events.
- `ising_state_result_item`: epoch, global block ID, current and next state.

### 5.3 Agents

All ready/valid agents must support active and passive modes. Drivers hold
payload and metadata stable while valid is asserted without ready. Monitors
sample only on rising edges where valid and ready are both high.

Required agents:

| Agent | Responsibilities |
|---|---|
| Core initialization | Drive state/seed/configuration and exactly one complete diagonal block per core |
| Hierarchy command | Drive and monitor H0, H1, or cross-H1 descriptors |
| Weight stream | Drive complete blocks with configurable gaps and backpressure |
| State publication | Publish selected frozen states to selected mesh owners |
| Control | Coordinate reset, epochs, start, schedule-done, completion, and commit |
| NoC packet monitor | Reconstruct state, partial, and epoch-done packets from flits |
| State monitor | Observe current/next states and completion events |

The environment shall not instantiate one heavyweight agent object per engine
for large meshes. Use an array-aware agent per interface group with logical
lane IDs in transactions.

## 6. Independent reference model

The predictor must not reuse RTL MVM or routing code. It maintains:

- Signed J entries indexed by global spin pair.
- Current and expected next state per global spin.
- Per-core LFSR state.
- Expected signed local, H0, H1, and cross-H1 partials.
- Expected packet destinations and per-epoch completion counts.

For every accepted block command, it predicts both symmetric partials using
the command's state indices and a snapshot of the epoch's state. It sends
expected partial packets to the protocol scoreboard and adds their values to
the appropriate expected core accumulators.

At the full-system level, a second direct calculation evaluates Jx from the
dataset independently of block scheduling. The following must all agree:

```text
sum of predicted block-level partials
direct dense/sparse software Jx
DUT state_next
```

This three-way comparison catches both datapath errors and missing, duplicate,
or incorrectly assigned block commands.

The model uses an explicit arbitrary-width or widened signed intermediate, then
applies RTL-width truncation at the same architectural boundaries as the DUT.

## 7. Scoreboards and checking

### 7.1 Arithmetic scoreboard

Checks every completed MVM result by `{epoch, level, block_id, packet_index}`.
Results from different engines may arrive in any legal order. Beats inside a
multi-flit partial packet must remain ordered and contiguous where required.

### 7.2 NoC scoreboard

For every injected packet, check exactly one delivery at the correct endpoint
with identical type, source ID, epoch, block ID, payload, and `last` placement.
Flag loss, duplication, corruption, misrouting, unexpected packets, and packets
remaining after the end-of-test drain barrier.

### 7.3 State scoreboard

At `iter_done`, compare all `state_next` bits against the reference model.
Before commit, check `state_current` is unchanged. After commit, check every
current block equals the preceding next block.

### 7.4 Completion scoreboard

Track accepted commands, completed engines, emitted partial packets, accepted
destination beats, state publications, and epoch-done packets. Assert that
`iter_done` and `cross_iter_done` do not occur early and eventually occur after
all prerequisites under fair ready assumptions.

## 8. Test inventory

### 8.1 Smoke and reset

| Test | Intent |
|---|---|
| `ising_smoke_test` | Small legal mesh, one iteration, deterministic weights, no stalls |
| `ising_reset_test` | Reset values, no X/Z outputs after reset, clean first transaction |
| `ising_reset_during_init_test` | Abort partial weight initialization and restart cleanly |
| `ising_reset_during_iter_test` | Reset with engines, FIFOs, and NoC packets active |
| `ising_repeated_reset_test` | Closely spaced resets and subsequent recovery |

### 8.2 Arithmetic primitives

| Test | Intent |
|---|---|
| `mvm_zero_test` | All-zero J |
| `mvm_identity_test` | Identity and zero-diagonal matrices |
| `mvm_all_ones_test` | Maximum positive accumulation |
| `mvm_all_negative_test` | Maximum negative accumulation |
| `mvm_checkerboard_test` | Alternating state and weight signs |
| `mvm_random_test` | Random int8 J and binary states |
| `symmetric_mvm_transpose_test` | Verify both J*x and transpose(J)*x outputs |
| `mvm_signed_corner_test` | Weights -128, -1, 0, 1, 127 |
| `mvm_overflow_test` | Defined accumulator boundary and overflow behavior |

### 8.3 Spin core

| Test | Intent |
|---|---|
| `core_init_test` | Exact weight-beat count, state/seed/coefficient latching |
| `core_local_only_test` | Diagonal block with no external partials |
| `core_h0_only_test` | H0 accumulation and packet beat assembly |
| `core_external_only_test` | H1/cross partial accumulation |
| `core_dual_port_test` | Simultaneous H0 and external transfers |
| `core_partial_gap_test` | Arbitrary valid gaps within and between partial packets |
| `core_backpressure_test` | FIFO-full propagation without loss |
| `core_done_order_test` | Completion before, with, and after local MVM completion |
| `core_coeff_test` | Positive/negative/zero a, b, c and tie-to-positive rule |
| `core_noise_test` | LFSR sequence, sign selection, amplitude and decay contract |
| `core_commit_test` | Frozen current state and atomic commit semantics |

### 8.4 Hierarchy node and adapters

| Test | Intent |
|---|---|
| `node_single_engine_test` | One descriptor, block load, two correct results |
| `node_multi_engine_test` | Concurrent engines and independent buffering |
| `node_double_buffer_test` | Load next block while current block computes/serializes |
| `node_schedule_done_test` | Done before/after final accepted command |
| `node_result_contention_test` | Multiple engines targeting same and different children |
| `adapter_boundary_id_test` | First/last block in every child range |
| `adapter_invalid_id_test` | Defined handling of out-of-range block IDs |
| `adapter_parent_child_test` | Parent partials and locally generated partials contend |
| `adapter_fairness_test` | Continuously active sources do not starve legal peers |

### 8.5 NoC and top node

| Test | Intent |
|---|---|
| `noc_single_flit_test` | State and epoch-done delivery for all source/destination pairs |
| `noc_multiflit_test` | Four-flit partial packet integrity |
| `noc_xy_route_test` | Every legal route and mesh boundary |
| `noc_output_lock_test` | Wormhole lock retained until accepted tail flit |
| `noc_backpressure_test` | Random and prolonged destination stalls |
| `noc_fifo_full_test` | Fill each input FIFO and verify upstream propagation |
| `noc_hotspot_test` | Many sources target one destination |
| `noc_bisection_test` | Opposite halves exchange maximum traffic |
| `noc_local_contention_test` | H1 injection competes with cross-node result injection |
| `noc_epoch_test` | Current, stale, and future epoch packets |
| `noc_reset_inflight_test` | Reset with head/body/tail flits in different routers |

### 8.6 Full hierarchy

| Test | Intent |
|---|---|
| `mesh_256_ring_test` | Existing eight-block ring, dense schedule, golden comparison |
| `mesh_256_skip_zero_test` | Same graph with empty block skipping; identical result |
| `mesh_random_dense_test` | Random symmetric dense J and random initial state |
| `mesh_random_sparse_test` | Random block sparsity and zero-block skipping |
| `mesh_asymmetric_negative_test` | Reject or explicitly characterize asymmetric J behavior |
| `mesh_level_ownership_test` | Force interactions at core, H0, H1, and cross-H1 levels |
| `mesh_random_schedule_order_test` | Permute command order without changing result |
| `mesh_random_engine_test` | Random legal engine assignment for each block |
| `mesh_random_backpressure_test` | Independent random stalls on every exposed stream |
| `mesh_multi_iteration_test` | At least 100 commits with golden comparison per iteration |
| `mesh_parameter_sweep_test` | Required parameter configurations listed below |
| `mesh_dataset_regression_test` | Known graph datasets and known update-state hashes |

## 9. Constrained-random stimulus

Randomization constraints shall guarantee legal block ownership unless a
negative test explicitly violates it. Important distributions include:

- 20% zero, 20% boundary, and 60% uniform-random weights.
- State patterns: all zero, all one, one-hot, alternating, and random.
- Empty, one-edge, partially filled, and fully dense J blocks.
- Backpressure burst lengths of 0, 1, FIFO depth, and several times FIFO depth.
- Commands distributed across all engines, including simultaneous completion.
- Destinations biased toward local, one-hop, diameter, and hotspot traffic.
- Schedule order randomized independently from mathematical block order.
- Schedule-done asserted immediately after the final command or after a random
  delay; never before the final command except in designated negative tests.

Reproduce every failure using the logged random seed, configuration, dataset,
and serialized transaction trace.

## 10. Functional coverage

### 10.1 Configuration coverage

Cover and cross:

- Mesh X and Y sizes.
- H0 count and cores per H0.
- H0, H1, and cross engine counts.
- FIFO depths.
- Dense versus zero-block-skipping schedule.
- One and multiple iterations.

### 10.2 Arithmetic coverage

Cover:

- Weight sign and corner value.
- State sign.
- Positive, zero, and negative dot products.
- Final-field positive, zero, and negative.
- Accumulator near minimum/maximum and any defined overflow event.
- Each coefficient zero, positive, and negative.
- Noise sign, zero/nonzero amplitude, and decay boundary.

Cross weight sign with state sign, and final-field sign with output state.

### 10.3 Protocol and buffering coverage

Cover:

- Ready/valid transfer with no stall and stalls of several length buckets.
- FIFO occupancy from empty through full.
- Simultaneous H0 and external core partial acceptance.
- Both engine slots/buffers and all engines.
- Schedule-done relative to engine/load/serialization state.
- Packet types, lengths, source/destination coordinates, and route lengths.
- Every router input-to-output turn permitted by XY routing.
- Arbitration with 1, 2, 3, 4, and 5 contenders.
- Lock duration and tail-flit acceptance under backpressure.

### 10.4 Hierarchical ownership coverage

Cover and cross:

- Core-local, H0, H1, and cross-H1 ownership.
- First, interior, and last global block IDs in each child range.
- Result A and result B destination levels.
- Owner local to endpoint A, local to endpoint B, and remote from both.
- Same destination versus different destinations for symmetric results.

### 10.5 Coverage exclusions

Every exclusion must state whether it is structurally unreachable, forbidden by
the interface contract, or deferred functionality. Exclusions require review;
do not waive uncovered bins solely to reach a numeric target.

## 11. Simulation assertions

Bind reusable assertions to every ready/valid channel:

1. Valid, payload, and metadata remain stable while `valid && !ready`.
2. Counters advance only on accepted transfers.
3. FIFO writes never occur when full and reads never occur when empty.
4. FIFO occupancy remains between zero and depth.
5. Weight streams contain exactly `WEIGHT_BEATS` accepted beats per command.
6. Partial packets contain exactly `PARTIAL_BEATS` accepted beats and assert
   `last` only on the final beat.
7. Router output ownership cannot change before an accepted tail flit.
8. A routed output direction is consistent with deterministic XY routing.
9. No valid packet is routed beyond a physical mesh boundary.
10. State writes and incoming partials use in-range block/state indices.
11. `state_current` is stable between reset/init and commit events.
12. `iter_done` implies all required local completion conditions.
13. `cross_iter_done` implies schedule done and all cross engines/buffers idle.
14. After reset, valid outputs and completion outputs are deasserted.
15. No output consumed by protocol logic is X or Z after reset release.

Where unbounded liveness is unsuitable for simulation, add bounded watchdog
properties under an explicit assumption that downstream ready eventually rises.

## 12. Formal property verification plan

Formal proofs should target bounded control blocks rather than the complete
million-spin mesh.

### 12.1 Proof targets

- Ready/valid skid/FIFO logic: safety, ordering, no loss, no duplication.
- H0 and H1 adapters: global block ID maps to exactly one correct child.
- Router: XY direction, boundary safety, arbitration one-hotness, packet lock.
- MVM controller: accepted command consumes one block and emits exactly two
  complete partial packets.
- Spin-core FSM: legal transitions, frozen state, completion preconditions.
- Top-node arbitration: no flit interleaving and correct source retention.
- LFSR: nonzero initialized state never enters zero for the selected polynomial.

### 12.2 Formal assumptions

Constrain inputs only to documented protocol requirements: stable stalled
payloads, legal indices for positive tests, complete packets, and eventual
ready for liveness proofs. Do not assume the result being proved.

### 12.3 Formal covers

Add covers for full FIFO, simultaneous contenders, maximum packet lock,
backpressure release, both symmetric result destinations, schedule completion,
and iteration commit.

## 13. Parameter regression matrix

At minimum, run:

| Class | Mesh | H0/H1 | Cores/H0 | Engines H0/H1/cross | FIFO |
|---|---|---:|---:|---|---:|
| Tiny debug | 1x1 | 1 | 1 | 1/1/1 | 2 |
| H0 integration | 1x1 | 1 | 2, 4 | 1-2/1/1 | 2, 4 |
| H1 integration | 1x1 | 2, 4 | 2, 4 | 1-4/1-4/1 | 2, 4, 8 |
| NoC debug | 2x1, 2x2 | 2 | 2 | 1-2/1-2/1-2 | 2, 4 |
| Mesh stress | 4x4 | 2, 4 | 2, 4 | representative legal values | 4, 8 |
| Architecture | selected paper configurations | selected | 32 | selected | selected |

Compile-time parameter jobs are separate builds. Runtime regressions sweep
datasets, seeds, backpressure, schedule order, coefficients, and iterations.

## 14. Regression tiers

| Tier | Frequency | Content |
|---|---|---|
| Presubmit | Every change | Lint, compile matrix, assertions, directed smoke, short random tests |
| Nightly | Daily | All module tests, hundreds of random seeds, parameter subset, coverage merge |
| Weekly | Weekly | Full parameter matrix, long congestion tests, known datasets, formal proofs |
| Milestone | Before release/paper result | Coverage closure, long multi-iteration tests, gate-level subset, reviewed waivers |

All regressions must archive simulator version, RTL revision, seed, parameters,
test name, runtime, pass/fail reason, assertion failures, and coverage database.

## 15. Sign-off criteria

Verification is complete only when:

- Every planned test is implemented and passing.
- All known datasets used for claims reproduce expected state hashes or cuts.
- No scoreboard mismatch, unexpected packet, timeout, assertion failure, or
  unexplained X/Z remains in required regressions.
- Functional coverage is at least 95% overall and 100% for critical ownership,
  packet type, route, arithmetic-sign, FIFO-full, and completion bins.
- Code coverage reaches agreed targets (recommended: 95% statement/branch,
  90% toggle, 100% FSM state/transition), with reviewed exclusions.
- Required formal safety properties prove without inconclusive results; bounded
  liveness depths cover the maximum supported FIFO and packet lengths.
- All waivers are documented and reviewed against architectural requirements.
- At least one independent reviewer approves the test plan, reference model,
  assertion set, coverage exclusions, and final regression report.

## 16. Known RTL items requiring explicit disposition

Before sign-off, resolve or formally document:

1. Whether `coeff_c` and `noise_decay` are fully implemented in `spin_core` or
   intentionally handled by an external controller.
2. Required behavior for asymmetric J, since symmetric block evaluation assumes
   the transpose contribution comes from the same loaded block.
3. Required behavior for invalid block IDs and stale/future epochs: reject,
   backpressure, drop with error, or declare illegal input.
4. Accumulator overflow policy: impossible by sizing, wrap, saturate, or flag.
5. Whether reset may occur with packets in flight and what recovery guarantee is
   required.
6. Fairness requirement for engine-result, adapter, and router arbitration.
7. Final scheduler/DMA and memory interface contract when those blocks replace
   direct UVM stimulus.

These are verification requirements, not details the testbench should guess.

## 17. Recommended repository structure

```text
dv/
  ISING_UVM_VERIFICATION_PLAN.md
  tb/ising_mesh_tb_top.sv
  bind/ising_assert_bind.sv
  env/ising_env_pkg.sv
  env/ising_env_cfg.sv
  env/ising_virtual_sequencer.sv
  env/ising_scoreboard.sv
  env/ising_reference_model.sv
  env/ising_coverage.sv
  agents/init/
  agents/block_stream/
  agents/state_publish/
  agents/noc/
  sequences/
  tests/
  formal/
  sim/
```

The existing procedural SystemVerilog testbench remains a fast smoke test and
source of known-good directed stimulus while the UVM environment is brought up.

## 18. Reference methodology

This plan draws on the public OpenTitan DV methodology and chip DV structure,
which pair UVM constrained-random simulation with separate FPV, machine-readable
test plans, independent scoreboards, assertions, coverage plans, and staged
regressions. PULP's open AXI verification infrastructure is also relevant when
the future memory streamer adopts AXI.

- OpenTitan DV methodology: https://github.com/lowRISC/opentitan/blob/master/doc/contributing/dv/methodology/README.md
- OpenTitan chip DV example: https://github.com/lowRISC/opentitan/blob/master/hw/top_earlgrey/dv/README.md
- OpenTitan DV coding style: https://github.com/lowRISC/style-guides/blob/master/DVCodingStyle.md
- PULP AXI verification infrastructure: https://github.com/pulp-platform/axi
