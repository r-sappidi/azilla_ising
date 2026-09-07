# Azilla cycle model

For the end-to-end mapping experiment workflow, see
[`MAPPING_EXPERIMENTS.md`](../MAPPING_EXPERIMENTS.md).

`azilla_cycle_model` is a cycle-stepped executable specification of the RTL.
It is separate from the hybrid NoC replay: arithmetic values and registered
component state are represented explicitly.

Current implemented and unit-tested primitives:

- fixed-width signed packing, addition, multiplication, and spin/weight dot
  products;
- synchronous J-block SRAM read timing;
- local row-serial MVM;
- fused symmetric MVM, including both directional results;
- spin-core initialization, local MVM, partial accumulation, fixed-width
  finalization, LFSR advance, and commit lifecycle;
- hierarchy-node command/J/result double buffering and serialization;
- tagged DRAM streamer with out-of-order response reassembly and ordered
  hierarchy-node delivery;
- H0, H1-child, and H1/NoC protocol adapters;
- integrated H0 tile lifecycle and datapath;
- integrated H1/H0 hierarchy lifecycle and datapath;
- cross-H1 compute endpoint and top-node local injection arbitration;
- ctypes binding to the RTL testbench's exact Ramulator2 backend.
- structural system composition across H1 tiles, cross endpoints, adapters,
  and the mesh.
- configured one-VC FlooNoC router and rectangular mesh, including registered
  input FIFOs, XY routing, wormhole locks, backpressure, and fair arbitration.

A component or configuration is only described as RTL-differential when the
corresponding trace comparison passes. Larger untested geometries retain the
accuracy label emitted by the CLI.

Run the Python unit tests from the repository root:

```bash
PYTHONPATH=model python3 -m unittest discover -s model/tests -v
```

Run the RTL differential oracle with:

```bash
python3 scripts/check_cycle_model.py --rebuild
```

After building the direct and Ramulator full-system smoke configurations, run:

```bash
python3 scripts/check_full_cycle_model.py
```

Audit the 16,384-spin Ramulator configuration with strict per-transfer traces:

```bash
python3 scripts/check_16k_cycle_model.py
```

This compares phase timing, all NoC stall totals, and every accepted transfer.
Coverage is specific to the dataset, engine counts, memory configuration, and
source revision in the passing report; geometry alone is not a certificate.
Timing coverage is separate from end-to-end arithmetic-payload verification.

The full mesh testbench releases reset after the final reset falling edge.
Accelerator cycle zero therefore precedes the first active DRAM falling edge:
cycle one has elapsed 40 DRAM ticks in the default configuration. Both the
cycle-stepped model and CIR exact-event idle prefix follow this edge contract.
An extra initial tick shifts refresh timing even when early transfers match.
`+MEM_TRACE_CLOCK` exposes this phase in VCS; `+MEM_TRACE_PREFIX=<path>` emits
separate per-memory-system request/response and command traces.

For a fresh VCS run, `scripts/check_cir_exact_vcs_trace.py` compares an exact-event
replay against its log, accepted-transfer CSV, and NoC counter CSV. It fails on
any phase, transfer-cycle, or stall-total difference and saves scoped evidence;
it does not issue a general simulator or arithmetic-validation certificate.

Run the arithmetic-enabled three-way 16K state audit with:

```bash
python3 scripts/check_16k_arithmetic.py
```

This compares every next-state bit among the Python Ramulator model, the
non-timing-only RTL simulation, and an independent graph-equation reference in
`scripts/generate_matlab_golden.m`.  The local automated run uses GNU Octave to
execute the MATLAB-compatible `.m` file; it does not claim execution by a
licensed MathWorks MATLAB binary.

Validate that a mapping contains every required sparse block exactly once:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli validate-schedule \
  --dataset tb/datasets/g16384_kings.txt --schedule mappings/example.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 1 --cores-per-h0 32
```

Run the fully cycle-stepped direct RTL workflow:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-direct \
  --dataset tb/datasets/g256_smoke.txt \
  --mesh-x 2 --mesh-y 1 --h0-per-h1 2 --cores-per-h0 2
```

`simulate-direct` deliberately matches `USE_RAMULATOR=0`, including its
serial testbench DMA task order. It is the end-to-end functional/cycle oracle,
not the projected-memory throughput workflow. The concurrent Ramulator runner
remains under differential integration; do not interpret direct-mode cycles as
the architecture's projected GDDR performance.

Run the concurrent projected-memory workflow with the exact Ramulator2 backend:

```bash
make -C tb cycle-model-ramulator-library
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-ramulator \
  --dataset tb/datasets/g256_smoke.txt \
  --mesh-x 2 --mesh-y 1 --h0-per-h1 2 --cores-per-h0 2 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml
```

Run the calibrated event-compressed timing model:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-events \
  --dataset tb/datasets/g256_smoke.txt \
  --mesh-x 2 --mesh-y 1 --h0-per-h1 2 --cores-per-h0 2
```

## CIR and cores-only execution modes

### Current snapshot and validation boundary

The September 7 snapshot includes the shared-fetch, two-slot endpoint model,
its historical regression oracles, and opt-in timing acceleration. The complete
Python suite currently passes 99 tests. This is not a universal RTL timing
certificate. A separate 65,536-spin, one-H1 toroidal timing differential matches
67,587 initialization, 16,849 iteration and 84,436 total cycles, including
recorded core/memory/state/router events. It is timing-only and does not certify
arithmetic at that size or arbitrary multi-H1 graphs.

Run the portable Python checks with:

```bash
PYTHONPATH=model python3 -m unittest discover -s model/tests -v
PYTHONPATH=model python3 -m azilla_cycle_model.shared_fetch_double_cli --help
```

The detailed model additionally requires the project Ramulator bridge and a
compatible Ramulator checkout. Locally modified third-party sources, compiled
libraries, archived validation traces, and campaign outputs are not bundled
with this simulator source snapshot. Regenerate validation evidence for a
different memory build; do not infer identical timing from Python tests alone.
Campaign launchers referring to ignored local inventories are researcher
automation, not a self-contained public reproduction package.

For the **primary CIR-matched comparison**, cores-only must use
`python3 -m azilla_cycle_model.shared_fetch_double_cli simulate-exact-events
--execution-mode cores-only ...` (or `simulate-mapped-exact-events`). Require
the exported fetch policy `single_fetch_two_slot_cir_routes_v3`. This provides
single canonical fetch, two downstream forwarding slots and CIR-matched
destination routing; see [the baseline contract](../docs/shared_fetch_baseline.md).
The plain `cli`/`fast_cli` cores-only and `shared_fetch_cli` paths described in
older examples remain historical variants and differential oracles. They must
not populate primary comparison figures. The fast-model equivalence gate for
the historical model does not authorize using `fast_cli` for the new baseline.

`scripts/run_cir_matched_campaign.py` prepares and executes the version-checked
replacement queue. It preserves old outputs, validates each new engine/memory
configuration on a small VCS fixture, and records large runs as projections.
Validation/arithmetic regression tests for historical variants remain useful
but are not primary performance results.

The event-compressed commands accept `--execution-mode cir`,
`--execution-mode cores-only`, or `--execution-mode hybrid`. The default is
`cir`.

- `cir` assigns every occupied unordered 32-by-32 interaction block to its
  native H0, H1-local, or cross-H1 symmetric MVM. One job produces partials
  for both endpoint blocks.
- `cores-only` assigns the two directions of every interaction to the two
  destination spin cores. Weight placement does not change: H0-local,
  H1-local, and cross-H1 blocks are read through the same canonical memory
  interfaces used by CIR. The complete 1-KiB block is transported to each
  destination core (32 256-bit flits per cross-H1 copy), rather than computing
  beside the owner memory and returning compact partials. It performs two
  directed jobs and does not return partial packets from the hierarchy.
- `hybrid` constructs a static, exclusive partition: some interactions run at
  their destination cores and the remainder use their native CIR pool.

For a controlled comparison, keep the dataset, geometry, hierarchy provisioning
parameters, canonical owners, memory configuration, interconnect parameters,
and iteration count identical and change only the execution mode. CIR fetches
each off-diagonal block once for symmetric computation; the implemented
cores-only baseline fetches it twice for two directed jobs. Active compute
counts and operand-cache capacities also differ. This compares combined
placement/fetch mechanisms, not placement alone or equal-area designs. See the
[comparison contract](../docs/comparison_contract.md) for controls and claim limits.

Example paired invocation:

```bash
for mode in cir cores-only; do
  PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-events \
    --dataset tb/datasets/g16384_kings.txt \
    --mesh-x 4 --mesh-y 4 --h0-per-h1 2 --cores-per-h0 16 \
    --h0-mvms 4 --h1-mvms 2 --cross-mvms 4 \
    --execution-mode "$mode" \
    --metrics-prefix "results/execution_modes/$mode"
done
```

Use the live-Ramulator exact-event path for finalist timing by replacing
`simulate-events` with `simulate-exact-events` and supplying the Ramulator
library and configuration:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-exact-events \
  --dataset tb/datasets/g16384_kings.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 2 --cores-per-h0 16 \
  --h0-mvms 4 --h1-mvms 2 --cross-mvms 4 \
  --execution-mode cores-only \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --idle-refresh-period-ticks 7600 \
  --metrics-prefix results/execution_modes/cores_only_exact
```

The underscore spelling `core_only` is not accepted; use `cores-only` exactly.
Mapped commands support the same option. Always pair a mapped artifact with
its permuted dataset for live-Ramulator or functional execution. These modes
change where interaction arithmetic is assigned; they do not change the Ising
update equation.

Calibrated results are screening estimates. Exact-event mode retains live
Ramulator timing but is timing-only and does not by itself verify arithmetic.
The strict CIR trace differential covers the documented small and 16K
configurations. The VCS-only cores-only validation combines an integrated
two-endpoint real-router/directed-core differential, live Ramulator endpoint
checks, and capacity elaboration through 256K. It is a representative,
compositional validation envelope rather than a direct large-geometry RTL
differential. Preserve the CLI accuracy label and the validation-manifest
limitations in reported results.

The exact-event cores-only path uses canonical H0/H1/cross Ramulator ownership.
Memory, transport, and core retirement now advance concurrently. Each source
has two 1-KiB reassembly buffers per configured hierarchy streamer lane and at
most 64 outstanding requests; a buffer is released only after its last row is
accepted by local delivery or the bounded cross-H1 router input. Each core has
one active job and one receiving weight SRAM. Local sources share a 256-bit
delivery lane per destination H0. Cross-H1
ejection has priority, then local sources arbitrate by ascending system ID.
The summary exports this provisional contract and occupancy counters under
`cores_only_ablation.pipeline_contract`.

The production path now shares `CoreMemoryPipeline` with the small integrated
VCS differential below. That coverage is not a full-iteration or arbitrary
multi-engine certificate. The earlier compositional envelope did not validate
memory/transport feedback or local arbitration. Older outputs used either
destination-H0 replication or separate DRAM/network/core replay phases;
neither may be mixed into current comparisons.
The contract retains two reads per unordered block pending a separate decision
on read-once distribution. Own-block SRAM computation and frozen-state access
timing require explicit integrated coverage before performance certification.

`USE_MESH=1 bash scripts/run_core_memory_integration_vcs.sh` exercises three canonical
Ramulator frontends, a shared local delivery arbiter, and eight directed core
engines under VCS. `check_core_memory_integration.py` compares every accepted
schedule, command, weight, and retirement event with the Python streamer and
core FSM, including forced result backpressure and every offered weight/ejection
stall. With `USE_MESH=1`, cross-source weight traffic traverses real FlooNoC
routers and physical-link acceptance/stalls are also compared. These checks use
the same kernel as the production exact-event path. `ENGINES` and
`JOBS_PER_SOURCE` control source lanes and repeated directed work; tested lane
counts include 1, 2, 4, and 16 with forced result stalls. The final stress checks
also compare every accepted memory request/response tag and cycle. Repeated
blocks stress queues rather than represent a solution-quality workload. Full
iteration control, frozen-state timing, and diagonal work require further coverage.

The event model removes spin/MVM arithmetic and schedules endpoint completion
events. Empty NoC intervals are jumped over but remain in the reported cycle
count; every active or contended NoC cycle uses the same RTL-differentially
checked Python router. The queued-v2 endpoint profile separates job latency
from multi-engine initiation interval and applies fixed low-load/saturated
request rates to the one shared memory endpoint per H0. This avoids granting
each destination core an independent memory interface. Its constants are
calibrated to the 128-pin/32-Gb/s Ramulator configuration and are recorded in
every summary. Override them only using a documented training/held-out
comparison against exact-event or RTL results.

Calibration accuracy must be evaluated on points not used to select the
profile constants. For example:

```bash
python3 scripts/check_calibrated_model_accuracy.py \
  --calibrated results/calibration/cores/summary.csv \
  --calibrated results/calibration/cir/summary.csv \
  --exact-root results/exact_execution_modes \
  --output results/calibration/accuracy.json
```

The checker defaults to a 10% held-out mean-error limit and a 20% held-out
maximum-error limit. Passing this check supports use as a calibrated screening
model; it does not change the result label to `rtl-differential`.

The CLI labels results `rtl-differential` only for a configuration actually
covered by an end-to-end RTL comparison. Other geometries are labeled
`calibrated-extrapolation`: their router behavior and elapsed event timestamps
remain exact, but a fixed endpoint service profile cannot reproduce arbitrary
Ramulator contention exactly. This distinction must be preserved in reported
paper results.

## Accelerated exact-event Ramulator model

For absolute timing, use the accelerated path instead of the fixed 67-cycle
endpoint profile. It retains Ramulator request acceptance, bank/row/refresh
timing, streamer double buffering, hierarchy pipeline state, partial-result
arbitration, and every active FlooNoC cycle. It omits arithmetic payload
evaluation and per-spin objects that cannot affect timing.

```bash
make -C tb cycle-model-ramulator-library
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-exact-events \
  --dataset tb/datasets/g16384_kings.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 2 --cores-per-h0 16 \
  --h0-mvms 1 --h1-mvms 1 --cross-mvms 16 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --idle-refresh-period-ticks 7600 \
  --metrics-prefix results/g16384/exact \
  --transfer-trace results/g16384/exact_transfers.csv
```

`--metrics-prefix` writes summary JSON and per-resource NoC, per-node work, and
per-memory-system DRAM CSVs. `--transfer-trace` optionally writes every
accepted injection, hop, and ejection. Counter collection is always enabled in
the result object; these options only control file output.

`scripts/check_exact_event_model.py` verifies both the 256-spin and 16K cases
against stored RTL traces. It compares phase timing, all NoC stall totals, and
every accepted injection, hop, and ejection by cycle and packet metadata.

### Opt-in optimized exact execution

For timing-only exact commands, the alternative entry point is
`python -m azilla_cycle_model.fast_cli`, with the same arguments as `.cli`.
It supports `simulate-exact-events` and `simulate-mapped-exact-events` in
both `cir` (CIR-only) and `cores-only` modes. The original entry point remains the
unchanged reference implementation.

The optimized implementation caches combinational outputs between state
changes, skips identity updates in idle streamers and empty networks, avoids
repeated empty DRAM response polls, and replaces timing-only endpoint MVM
row operations with an equivalent counter. It does **not** replace Ramulator
with fixed memory latency or bypass active NoC arbitration, backpressure,
state-bank conflicts, or pipeline completion delays.

Validate a frozen candidate against a separately frozen reference with
`scripts/check_fast_exact_model.py` (complete runs, byte-identical exported
metrics and ordered transfers) and `scripts/check_fast_exact_windows.py`
(large-geometry active windows, internal events and final state). Component
tests are in `model/tests/test_fast_*.py`. Large-geometry windows establish
sampled optimization equivalence, not full-run or additional RTL verification.
The gated continuation tools refuse to launch outside the recorded validated
configuration envelope and preserve existing in-flight runs and artifacts.

Equivalent results retain the original fidelity labels and RTL coverage;
optimization equivalence does not certify the underlying model against RTL.
Existing performance/traffic data need not be regenerated solely for this
optimization. Simulator wall-time measurements must still identify which
implementation produced them, and arithmetic/solution-quality experiments
continue to use their separate functional reference.

### Parameterized inter-H1 physical links

All simulation modes accept the following per-direction link parameters:

- `--link-latency-cycles`: additional one-way latency per physical H1 hop
  beyond the directly connected RTL mesh;
- `--link-flit-interval-cycles`: minimum cycles between accepted logical
  256-bit flits, representing serialization/effective bandwidth;
- `--link-max-inflight-flits`: finite credit window including flits in flight,
  queued at the receiver, and awaiting credit return;
- `--link-credit-return-cycles`: delay after receiver acceptance before the
  corresponding credit is reusable.

The defaults (`0`, `1`, `4`, `0`) preserve the RTL-connected mesh and its
differential traces. A non-default physical-link experiment is labeled
`parameterized-interconnect-projection`. For example:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-exact-events \
  --dataset tb/datasets/g16384_kings.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 2 --cores-per-h0 16 \
  --h0-mvms 1 --h1-mvms 1 --cross-mvms 16 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --link-latency-cycles 10 \
  --link-flit-interval-cycles 2 \
  --link-max-inflight-flits 32 \
  --link-credit-return-cycles 10
```

The model remains flit-level and full-duplex. Each directed link separately
enforces its launch interval and credit window; delayed flits participate in
downstream router arbitration and backpressure after arrival. Exact-event
summary JSON records the complete interconnect configuration.

### Package-internal chiplet latency

The event and exact-event commands also accept one package-local parameter:

- `--chiplet-link-latency-cycles`: additional one-way latency for a
  fully-pipelined crossing between chiplets inside one H1 package.

It is charged at the modeled package crossings: H0 state publication to the
H1/top endpoint, H0 state snapshotting into the H1-local hierarchy node, and
the return of H1-local or cross-H1 partials to the destination H0 tile. A
latency-only internal fabric is assumed, so this value shifts pipeline startup
and tail events but does not reduce throughput or add credits/backpressure.
Ramulator continues to model each attached GDDR device; this parameter does
not add a second delay to the GDDR command/data interface.

For an H1-package projection with eight additional cycles per internal
chiplet crossing:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-exact-events \
  ... \
  --chiplet-link-latency-cycles 8
```

The zero default preserves the RTL timing contract. Any nonzero value is
reported as `parameterized-interconnect-projection`, because the current RTL
contains direct wires at these boundaries rather than a physical chiplet-link
implementation. The selected value is printed by the CLI and written to the
summary JSON under `package.chiplet_link_latency_cycles`.

The million-spin 256-8-16 command is:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-exact-events \
  --dataset tb/datasets/g1048576_kings.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 8 --cores-per-h0 256 \
  --h0-mvms 16 --h1-mvms 16 --cross-mvms 16 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --idle-refresh-period-ticks 7600
```

This measured 7,329 iteration cycles on the current identity-ordered kings
dataset. The CLI labels it `cycle-structured-unverified`, because the same
model structure is RTL-differential at 16K but million-spin RTL itself was not
elaborated. Do not confuse this with functional arithmetic validation at one
million spins.

## Sparse graph-mapper integration

The code from `~/Downloads/graph_mapping` is vendored at
`model/graph_mapping/graph_compression`. The hardware-specific bridge is
`model/azilla_cycle_model/mapping_adapter.py`.

The integration does not construct a dense adjacency matrix or materialize
32x32 weight tiles for timing studies. It consumes the dataset as sparse edge
arrays, runs the hierarchical vertex permutation, and extracts unique occupied
block coordinates on the selected PyTorch device. Coupling magnitudes, not
their signs, drive placement because positive and negative couplings have the
same communication cost.

Run the mapper and create a self-contained artifact:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli map-graph \
  --dataset tb/datasets/g1048576_kings.txt \
  --output-dir mappings/g1048576_kings \
  --write-permuted-dataset mappings/g1048576_kings/dataset.txt \
  --mesh-x 8 --mesh-y 8 --h0-per-h1 16 --cores-per-h0 32 \
  --device cuda --require-cuda --seed 1
```

The mapping stage requires PyTorch; a CUDA-enabled build is strongly
recommended for million-spin inputs. PyTorch is intentionally not required to
load an existing mapping artifact or run the event model.

The artifact directory contains:

- `manifest.json`: schema, geometry, conventions, and mapping parameters;
- `permutation.npy`: `permutation[new_vertex] = old_vertex`;
- `inverse_permutation.npy`: the old-to-new vertex lookup;
- `block_pairs.npy`: unique occupied off-diagonal 32x32 blocks;
- `owners.npy`: cross-H1 compute owner per block, with -1 for fixed local
  work;
- `schedule.txt`: the same schedule in the existing text interface.

Consume it directly with the event-compressed model:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-mapped-events \
  --artifact mappings/g1048576_kings \
  --h0-mvms 4 --h1-mvms 4 --cross-mvms 16
```

Use `simulate-mapped-exact-events` with the emitted permuted dataset when a
mapped result needs Ramulator-backed absolute timing:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-mapped-exact-events \
  --artifact mappings/g1048576_kings \
  --dataset mappings/g1048576_kings/dataset.txt \
  --h0-mvms 16 --h1-mvms 16 --cross-mvms 16 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --idle-refresh-period-ticks 7600
```

For the arithmetic or Ramulator model, use the emitted permuted dataset and
text schedule. The permutation must be applied to the weights; using the
original dataset with mapped block coordinates is functionally incorrect.

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-mapped-ramulator \
  --artifact mappings/g1048576_kings \
  --dataset mappings/g1048576_kings/dataset.txt \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml
```

`simulate-mapped-ramulator` derives geometry and schedule directly from the
artifact and validates them against the permuted dataset before simulation.
Unlike `simulate-mapped-events`, it has no fixed block-service latency:
Ramulator2 is advanced by `--ticks-per-cycle` on every accelerator cycle,
and request rejection, bank/row timing, tagged out-of-order responses,
streamer double buffering, compute overlap, and NoC backpressure all affect the
reported completion time. Add `--timing-only` to skip arithmetic values while
retaining the same control and memory timing.

`simulate-mapped-ramulator` is fully cycle-stepped. Ramulator2 does not
currently expose its next internal DRAM event through the DPI bridge, so both
Ramulator-backed paths continue stepping every cycle while requests are
outstanding. `simulate-mapped-exact-events` gains scalability by eliminating
timing-inert arithmetic state and bulk-advancing only the request-free prefix;
it retains the same active-cycle timing path. The older fixed-profile
`simulate-mapped-events` command remains the large-scale extrapolation model.

Alternative mapping algorithms can use the stable Python boundary
`artifact_from_mapping(geometry, permutation, inverse_permutation,
occupied_coords, owner_assigner=...)`. A vertex mapper only needs to emit a
bijection and occupied block coordinates. A scheduling experiment can replace
`owner_assigner`; the adapter then enforces 32-spin blocks, immutable H0/H1
local placement, complete cross-H1 ownership, and artifact consistency before
the performance model runs.

## Cores-only full-controller validation

`simulate-exact-events --execution-mode cores-only` now uses the full
single-MVM core controller, canonical hierarchy-owned weight interfaces,
banked operand-state SRAM, and one persistent mesh across iteration phases.
The calibrated model is not a substitute for this validation path.

Representative VCS coverage is currently 256 spins, two iterations, including
4/2/4 hierarchy engines and injected memory/consumer backpressure. Arithmetic
and timing-only executions are checked separately. Larger runs retain
`cycle-structured-unverified`; representative coverage does not certify every
geometry. Request-free initialization is bulk advanced and nonzero package-local
link latency is currently rejected by this path pending integrated coverage.

`scripts/run_core_full_readiness.sh` reruns the representative differential
before executing separately labeled 16K scaling smokes. Set `OUTPUT_ROOT` to
a new ignored results directory. It stops on failures or source changes;
successful scale smokes are not large-geometry RTL certificates.
