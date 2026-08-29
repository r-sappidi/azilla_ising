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

The remaining integration work is tracked in `MODEL_STATUS.md`.  A component
is not called cycle-exact there until an RTL differential trace passes.

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

This passes for the audited timing-only 4x4/2-H0/16-core configuration and
compares phase timing, all NoC stall totals, and every accepted transfer.  See
`MODEL_STATUS.md` for the exact scope and the distinction between 16K timing
verification and end-to-end arithmetic-payload verification.

Run the arithmetic-enabled three-way 16K state audit with:

```bash
python3 scripts/check_16k_arithmetic.py
```

This compares every next-state bit among the Python Ramulator model, the
non-timing-only RTL simulation, and an independent graph-equation reference in
`scripts/generate_matlab_golden.m`.  The local automated run uses GNU Octave to
execute the MATLAB-compatible `.m` file; it does not claim execution by a
licensed MathWorks MATLAB binary.  See `MODEL_STATUS.md` for exact parameters,
results, and remaining coverage limits.

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

The event model removes spin/MVM arithmetic and schedules endpoint completion
events. Empty NoC intervals are jumped over but remain in the reported cycle
count; every active or contended NoC cycle uses the same RTL-differentially
checked Python router. The default endpoint profile is calibrated to the
128-pin/32-Gb/s Ramulator configuration. Override its block-cycle parameters
only with measurements from the exact model or RTL.

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
  --idle-refresh-period-ticks 7600
```

`scripts/check_exact_event_model.py` verifies both the 256-spin and 16K cases
against stored RTL traces. It compares phase timing, all NoC stall totals, and
every accepted injection, hop, and ejection by cycle and packet metadata.

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
