# Current architecture (October 2026)

The design evaluated since October 15 is CIR with **H0-anchored symmetric
ownership and accumulate-at-owner uplinks** (`--execution-mode cir-h0-anchored
--anchored-accumulate 1`, RTL `rtl/experimental/anchored/`). It supersedes the
hierarchy-owner CIR of `docs/CIR_BASELINE.md` as the primary design; the
comparison baseline is the flat weight-stationary state-exchange organization
(`--execution-mode flat-stationary`). This page is the implementation-level
summary; `docs/anchored_rtl.md`, `docs/cpp_exact_model.md` and
`docs/comparison_contract.md` hold the interfaces, model and evaluation rules.

## Fixed granularity and widths

- 32 spins per core, 1-bit spin states, int8 couplings; a coupling block is
  32x32 = 1,024 bytes.
- Every datapath bus is 256 bits (`DATA_W`): one J row = one 32-byte beat,
  one block = 32 beats; a partial result is 32 x int32 = 128 bytes = 4 beats.
- DRAM transactions are 32 bytes, one J row per request.
- Nominal geometry: H0 = 256 cores = 8,192 spins; H1 = 8 H0 = 65,536 spins;
  H1 mesh 1x1 / 2x1 / 2x2 / 2x4 / 4x4 for 64K / 128K / 256K / 512K / 1M spins.

## Core (endpoint)

`rtl/core.sv`: 32 frozen states, the diagonal block J_ii in local SRAM, a
one-sided local MVM for J_ii x_i, and three independent int32 accumulators
(local, H0 stream, external stream) that are combined only after the
controller marks the iteration's partial traffic complete. The update is the
simplified synchronous rule with coefficients a/b/c and optional noise (not
simulated bifurcation); commit replaces the frozen state at the iteration
boundary. No partials leave a core.

## H0 chiplet: where all off-diagonal compute lives

- **Weight memory.** Each H0 owns one DRAM system. Modeled configurations:
  the board GDDR6 projection (`tb/ramulator_128x32.yaml`, 128 pins x 32 Gb/s)
  and co-packaged HBM4 (`tb/ramulator_hbm4_512x8*.yaml`, 8 x 64-bit channels
  at 8 Gb/s = the slice of two stacks per H1); both are 512 GB/s peak per H0.
  `dram_weight_streamer.sv` turns each scheduled block into 32 tagged 32-byte
  reads over 16 request/response lanes (`MEM_LANES`, model `--mem-lanes`),
  up to `MAX_OUTSTANDING` in flight (RTL default 64, model
  `--max-outstanding`; 256 in the 1 GHz campaigns), responses in any order,
  into one of two 1 KB J slots per engine (`j_block_sram.sv`, fill overlaps
  compute).
- **Engines.** `hierarchy_node.sv` + `symmetric_mvm.sv`: one J row per
  cycle, add/subtract trees (states are +/-1), so in 32 row-serial cycles one
  block read yields both J_ab x_b and J_ab^T x_a (two 128-byte partials). Two
  result slots per engine overlap output backpressure with the next block.
  Operand states come from a global-indexed H0 state table
  (`banked_state_sram.sv`: 8 interleaved synchronous banks, tagged one-cycle
  reads, fixed lane-order arbitration; same-bank reads serialize), filled
  before the node starts with every distinct source block its jobs need.
  Engines per H0: 4 in the RTL-verified configuration, 16 in the 1 GHz
  campaigns.
- **Ownership.** A block whose two cores share an H0 stays there. Every other
  block is owned by exactly one of its two endpoint H0s
  (`compile_anchored_schedule`: greedy by H0 pair plus local search on
  max(engine load, owner-H1 injection-port load); software, not hardware).
  Each block is read once and stored once: N^2/2 weight bytes per dense
  iteration. The H1-local and cross-H1 engine pools and DRAM systems of the
  earlier CIR carry no jobs.
- **Local partial.** Delivered through `h0_adapter.sv`, a 256-bit crossbar to
  the 256 cores; each core is an independent output bank with fixed-priority,
  packet-locked arbitration.
- **Remote partial: the H0 uplink** (`h0_uplink.sv`, one 256-bit port per
  H0). Packet-locked round robin over the engines holding a remote partial;
  one read-modify-write beat per cycle into a per-remote-block int32
  accumulator (128 bytes per block, `touched` bit; first write overwrites,
  later ones add), so the engine never stalls on it. When the H0 node has
  finished its jobs it flushes each touched block as one 4-beat packet in
  ascending block order into one of two 4-packet FIFOs: sibling (destination
  H0 in the same H1) or mesh (another H1). Remote work for a block is thus
  summed at the owner and crosses the hierarchy once per (owner H0, remote
  core) per iteration rather than once per block.

## H1 package

No compute. `h1_child_adapter.sv` (`PARENT_FIRST=1`) is a crossbar whose
sources are the 8 uplink sibling streams plus the mesh/parent port, which has
priority so the mesh always drains; it switches 4-beat partials into the
destination H0's external-partial port. `uplink_injection_arbiter.sv`
round-robins the 8 H0 mesh streams into the node's NoC injection port (the
cross node's slot in `top_node.sv`; the cross node is instantiated idle).
State publication: one single-flit `NOC_STATE` per (source block, consuming
H1) pair at iteration start. Timed delivery of published states into the H0
tables is abstracted on both sides (the model starts a node at
`iter_start + distinct sources + 2`; the testbench fills on that schedule).

## Inter-H1 mesh

FlooNoC five-port routers (`azilla_floo_router.sv`, vendored FlooNoC), one per
H1; 256-bit data flits with type, destination x/y, source id, epoch, block id
and `last`; single VC, 8-deep router FIFOs; baseline links move one flit per
cycle with 4 in flight and zero added latency (`--link-*` options are
projections). Traffic per iteration: `NOC_STATE` flits and 4-flit
`NOC_PARTIAL` packets from uplink flushes, received through the existing
cross-partial path and the child crossbar to the H0 and core. Completion:
all H0 nodes done and all uplinks flushed -> four quiet cycles ->
`EPOCH_DONE` -> cores combine accumulators and commit.

## Baseline: flat weight-stationary state exchange

Same chip and memory; each block stored twice (row form at A's H0, transpose
at B's H0) and evaluated by one-sided directed engines (`Engine::directed`,
half the useful work per streamed byte); only `NOC_STATE` crosses the mesh
and no partial leaves an H0. Matched at equal MAC count: 2x the anchored
engines per H0 (8 vs 4, 32 vs 16). C++ model only.

## Provisioning and clock

The modeled accelerator cycle is `--ticks-per-cycle` Ramulator ticks of
250 ps: 40 (10 ns, 100 MHz, the RTL-verified ratio) or 4 (1 ns, 1 GHz, the
October 19-20 campaigns). At 4 ticks the 64-outstanding front end and the
`RoBaRaCoCh` address map limit the stream, so the 1 GHz campaigns use 256
outstanding, 16 engines per H0 and the `MOP4CLXOR` map (HBM4: ~210-236 B
per cycle per H0 achieved against a ~261 activate-rate plateau; 512 peak).
A 1 GHz cycle is a modeling assumption: GF12 support blocks met a 1 ns
constraint pre-layout, the full H0 synthesis did not complete.

## Verification status

- Anchored+accumulate RTL matches the C++ model cycle for cycle on 24 VCS
  cases up to dense 64K single-H1 at 4 engines, 64 outstanding, 40 ticks,
  GDDR6 (`results/anchored_rtl_20261015`, `docs/anchored_rtl.md`); the
  small cases also pass the functional next-state check.
- The 1 GHz ratio, 16/32 engines, 256 outstanding, HBM4, the flat baseline
  and all multi-H1 sizes above 2K spins are C++ model only
  (`cycle-structured-unverified`).
- Not modeled: the owner accumulator at nominal size (32K blocks x 128 B =
  4 MB SRAM per H0 with pipelined read and hierarchical touched bitmap), timed
  H1 -> H0 state forwarding, area, power, dSB arithmetic, solution quality.
