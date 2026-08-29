# Cycle-Accurate Python Performance Model Status

The accuracy contract is strict: every implemented component advances on one
rising edge per `tick()`, preserves ready/valid backpressure, and applies RTL
width truncation at the corresponding register boundary.  Aggregate timing
agreement alone is not sufficient; differential traces must match per cycle.

## Implemented

- SystemVerilog-style signed/unsigned truncation and packed-lane helpers.
- `mvm.sv` row timing and arithmetic.
- `symmetric_mvm.sv` row timing and bidirectional arithmetic.
- `j_block_sram.sv` registered read behavior.
- `spin_core` lifecycle, partial accumulation, LFSR, finalization, and commit.
- `hierarchy_node` command/J/result double buffering, symmetric computation,
  and four-beat directional serialization.
- `dram_weight_streamer` allocation, request issue, tag encoding,
  out-of-order response reassembly, and ordered node delivery.
- H0, H1-child, and H1/NoC protocol adapter routing and packet locks.
- Integrated H0 tile state loading, local hierarchy, partial delivery, spin
  cores, completion, and commit control.
- Integrated H1 tile, child H0s, state snapshotting, local/parent partial
  arbitration, and hierarchical completion.
- Cross-H1 hierarchy endpoint, round-robin packet output, route derivation,
  and top-node local injection lock.
- Python binding to the same Ramulator2 C++/configuration used by RTL; DRAM
  command timing is not approximated by a separate Python implementation.
- Dataset/J-row loader, RTL-identical initial-state/noise seeds, and strict
  schedule coverage/duplicate/owner validation.
- Structural mesh composition joining H1 tiles, cross endpoints, H1 protocol
  adapters, top-node injection/ejection decisions, and real-router timing.
- The configured one-VC, input-buffered XY FlooNoC router and mesh model.
- RTL-testbench-identical hierarchy classification, distance-balanced cross-H1
  owner allocation, publication discovery, serial direct dispatch, and
  concurrent ready-aware Ramulator dispatch.
- End-to-end direct-mode phase driver: reset, diagonal initialization, state
  publication/drain, cross start, schedule issue, schedule-done, completion,
  functional state comparison point, and architectural commit.
- End-to-end concurrent Ramulator phase driver using the same C++ Ramulator2
  implementation and configuration as the RTL testbench.
- Event-compressed timing-only model with explicit endpoint calibration,
  hierarchy engine resource queues, and exact active-cycle NoC replay. Empty
  mesh intervals advance the timestamp without executing idle router ticks.
- Accelerated exact-event Ramulator path that removes timing-inert spin payload
  state while retaining the real Ramulator backend, tagged streamers,
  hierarchy-node control/double buffers, result arbitration, and active-cycle
  FlooNoC execution.
- Sparse graph-mapping integration with a portable permutation/block/owner
  artifact, architecture-constrained cross-H1 scheduling, exact-dataset
  permutation export, and direct event-model consumption. Timing studies do
  not materialize a dense adjacency matrix or 32x32 weight tiles.

## Differential evidence

- `mvm`/`symmetric_mvm`: per-cycle control and arithmetic traces pass.
- `spin_core`: per-cycle lifecycle, ready/valid, state, accumulator, and
  arithmetic traces pass.
- `hierarchy_node`: per-cycle engine/buffer/serializer and arithmetic traces
  pass.
- FlooNoC router: FIFO-full, backpressure, arbitration, and multibeat lock
  traces pass.
- Full 256-spin sparse direct workflow: RTL and Python both report 267
  initialization cycles, 339 iteration cycles, and 606 total cycles; the RTL
  functional golden check passes and both report 22 injected/ejected flits and
  10 physical-link flits.
- Full 256-spin sparse Ramulator workflow: RTL and Python both report 267
  initialization cycles, 179 iteration cycles, and 446 total cycles; both use
  the same Ramulator2 implementation, pass the same functional state check,
  and report 22 injected/ejected flits, 10 physical-link flits, one injection
  stall, and three ejection stalls.

## 16K differential audit

The 16,384-spin Ramulator timing-only configuration now passes its strict
RTL differential.  The audited configuration is a 4x4 mesh with 2 H0s per H1,
16 cores per H0, 1/1/16 H0/H1/cross engines, FIFO depth 4, sparse scheduling,
16 memory lanes, and 40 Ramulator ticks per accelerator cycle.

RTL and Python both report 16,899 initialization cycles, 1,821 iteration
cycles, 18,720 total cycles, 1,336 accepted injections/ejections, 2,200
accepted physical-link transfers, and 122/131/215 injection/ejection/link
stall cycles.  All 4,872 accepted injection, hop, and ejection records match
by cycle and packet metadata.  Two independent RTL runs produced
byte-identical event and statistics CSVs.

The audit exposed and fixed two scale-dependent timing errors:

- The Python concurrent dispatcher could refill a port on the same dispatch
  step that retired its prior command.  RTL retains the prior-edge accepted
  mask through that step and inserts a one-edge refill bubble.
- The Python router updated fair arbitration from live FIFO heads.  FlooNoC's
  wormhole arbiter snapshots the request vector at a packet boundary and holds
  it through the packet, so a late request cannot affect the priority update
  on that packet's last beat.

The accelerated exact-event path passes the same audit in about 12--20 seconds
on the development machine. It reports the same 16,899/1,821/18,720 cycle
breakdown, all six NoC accepted/stall totals, and all 4,872 accepted transfers
by cycle and packet metadata. The 256-spin differential likewise matches all
54 transfers. Reproduce both checks with:

```bash
python3 scripts/check_exact_event_model.py
```

This path bulk-advances request-free initialization time while retaining it in
the reported cycle count. For `ramulator_128x32.yaml`, reducing that idle
prefix modulo the 7,600-tick all-bank refresh period was differentially checked
at both 256 and 16K spins. A different memory configuration must supply and
revalidate its own refresh period or disable this optimization.

The 1,048,576-spin 256-core/H0, 8-H0/H1, 4x4 configuration completed with the
same structural timing path in 7,329 iteration cycles. It scheduled
115,968/10,528/1,410 H0/H1/cross blocks and reported 12,256 injected/ejected
flits, 20,400 physical-link transfers, and 3,800/2,265/3,994
injection/ejection/link stall cycles. The run used roughly 461 MB and 10.7
minutes on one CPU core. Its accuracy label is `cycle-structured-unverified`:
the implementation is composed from the 16K-differential components, but an
RTL elaboration of the million-spin configuration was not run.

Run the reproducible audit with:

```bash
python3 scripts/check_16k_cycle_model.py
```

The checker compares every accepted injection, physical hop, and ejection by
cycle and packet metadata, plus aggregate accepted/stall counts and phase
timing.  Its passive observer was independently checked on the 256-spin
Ramulator regression, where all 54 accepted NoC transfers match exactly.  The
16K audit uses `TIMING_ONLY=1`: it validates memory, streamer, hierarchy,
packet, router, backpressure, and completion timing, but does not compare 16K
arithmetic payload values end to end.

## 16K arithmetic audit

The same 16,384-spin configuration also passes a non-timing-only, three-way
arithmetic comparison.  The RTL testbench dumps every architectural
`state_next` bit after one complete iteration; the Python Ramulator model dumps
the corresponding state, and `scripts/generate_matlab_golden.m` independently
evaluates the graph update equation directly from the edge list.  The `.m`
reference contains no hierarchy, packetization, scheduling, or Python-model
logic and is executable by MATLAB or GNU Octave.

All 16,384 bits match for Python versus RTL, RTL versus the MATLAB-compatible
reference, and Python versus the reference.  Each result contains 10,160 one
bits.  The arithmetic-enabled Python and RTL runs also retain the exact timing
agreement: 16,899 initialization cycles, 1,821 iteration cycles, and 18,720
total cycles.  A separate three-iteration 256-spin stress calculation with
`A=3`, `B=-2`, and noise amplitude 5 matched Python versus the `.m` reference
for all 768 produced state bits.  The component RTL differentials independently
cover MVM, spin-core coefficient/noise/fixed-width arithmetic, and hierarchy
payload traces.

Run the 16K three-way audit with:

```bash
python3 scripts/check_16k_arithmetic.py
```

This final-state audit proves arithmetic results for the stated dataset,
parameters, iteration count, mapping, and geometry.  It complements, rather
than replaces, the timing-only audit's exhaustive per-cycle NoC transfer trace.

## Remaining validation limits

- Differential RTL traces for the H0/H1 adapters remain required; tile-level
  traces for integrated H0/H1 tiles remain required independently of the
  passing full-system direct regression.
- Differential RTL trace for `dram_weight_streamer` remains required.
- Add end-to-end per-cycle transfer logs for multiple sparse/dense mappings and
  multi-iteration/noise configurations, rather than relying on matching phase
  totals and final state alone.

The direct workflow is a cycle-accurate functional oracle but serializes DMA in
the testbench. The concurrent Ramulator workflow is the arithmetic/performance
oracle. The accelerated exact-event Ramulator path is the million-spin timing
path. It does not evaluate arithmetic payload values, because those values do
not affect the fixed-latency MVM control or routing schedule; arithmetic
equivalence remains covered separately by the RTL/Python/MATLAB audits.

The older fixed-profile event model remains useful for sub-second exploratory
sweeps, but its endpoint service times must be recalibrated whenever the RTL or
memory configuration changes.

The event model reports an accuracy label. `rtl-differential` means its entire
configuration was checked against RTL; `calibrated-extrapolation` means NoC
cycles are exact for the generated releases but endpoint release timing comes
from a calibrated fixed-service profile. Do not describe extrapolated endpoint
timing as fully RTL-cycle-exact.
