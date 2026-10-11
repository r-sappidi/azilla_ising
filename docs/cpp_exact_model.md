# C++ exact-event model

`model/cpp_exact/` is a standalone C++20 reimplementation of the Python
timing-only exact-event model
(`azilla_cycle_model.exact_events.RamulatorEventPerformanceModel`). It covers
execution mode `cir` (stage 1, below) and the cores-only shared-fetch v3
endpoint policies, streaming and buffered (stage 3, later in this document).
It exists to run the same cycle-exact simulation much faster; the Python
model remains the oracle and must not be changed to match the port.

Validation summary (October 2026, port equivalence only; no new RTL coverage):

| Mode | Python oracle | Stored VCS traces (three-way RTL = Python = C++) | Largest RTL-verified point |
|---|---|---|---|
| `cir` | 18/18 byte-identical | 20/20 valid traces, plus dense 128K two-H1 (40,499,009 cycles) | 131,072 spins, 2x1 mesh, fully dense |
| `cores-only` streaming | 63/63 byte-identical | 22/22 | 131,072 spins, 2x1 mesh, hierarchy stress |

Evidence: `results/cpp_exact_port_20261006/REPORT.md`,
`results/cpp_exact_port_20261006/rtl_differential/REPORT.md` and
`results/cpp_exact_port_20261006/streaming/REPORT.md`. Wall-clock speedups
over Python range from 2x on sub-second cases to 46x on dense 128K; stack
sampling shows 85-100% of the C++ run time inside the shared Ramulator
library, so the port is within 1.2-1.8x of VCS on dense workloads.

## Stage 1: execution mode `cir`

### Scope

Ported (one C++ module per Python module, same names, same decision order):

| Python | C++ |
|---|---|
| `workload.py` (Geometry, BlockOccupancyDataset, schedules) | `workload.{hpp,cpp}` |
| `scheduler.py` (allocate_h1_pairs, compile_schedule, ConcurrentDispatcher) | `scheduler.{hpp,cpp}` |
| `noc.py` (Flit, InterconnectConfig, DirectedLink, FlooRouter, Mesh) | `noc.{hpp,cpp}` |
| `events.py` EventCompressedMesh.replay (publication phase) | `events.{hpp,cpp}` |
| `hierarchy.py` + `compute.SymmetricMVM` (timing-only) | `hierarchy.{hpp,cpp}` |
| `memory.py` DramWeightStreamer | `memory.{hpp,cpp}` |
| `ramulator.py` ctypes binding | `ramulator.{hpp,cpp}` (dlopen, same C ABI) |
| `cross.py` CrossH1Node, LocalInjectionArbiter | `cross.{hpp,cpp}` |
| `adapters.py` H0Adapter, H1ChildAdapter | `adapters.{hpp,cpp}` |
| `exact_events.py` run() for `cir` | `exact_events.{hpp,cpp}` |
| `metrics.py` write_exact_event_metrics / write_transfer_trace | `metrics.{hpp,cpp}` |
| `cli.py simulate-exact-events` | `main.cpp` |

Not ported: `cores-only` (buffered v3 shared-fetch endpoint), `hybrid`,
streaming endpoint, `simulate-mapped-exact-events`, arithmetic (the port is
timing-only by construction, like the Python `timing_only=True` path).

DRAM timing is shared, not reimplemented: the binary loads
`build/cycle_model_ramulator/libazilla_ramulator.so` (built from
`tb/ramulator_dpi.cpp` by `make -C tb cycle-model-ramulator-library`) and calls
`az_dram_init_timing`, `az_dram_send`, `az_dram_tick`, `az_dram_pop`,
`az_dram_get_stats`, `az_dram_finalize` exactly as `ramulator.py` does
(40 ticks per cycle, 16 lanes, optional idle refresh modulus 7600).

### Build

```bash
cmake -S model/cpp_exact -B build/cpp_exact -DCMAKE_BUILD_TYPE=Release
cmake --build build/cpp_exact -j4
```

Requires g++ with C++20 `<charconv>` floating-point support (conda-forge
g++ 15 was used) and cmake >= 3.16. No Python or pybind11 dependency.

### Run

Same flags as the Python CLI:

```bash
build/cpp_exact/azilla_exact_cir simulate-exact-events --execution-mode cir \
  --dataset tb/datasets/g256_smoke.txt --mesh-x 2 --mesh-y 1 \
  --h0-per-h1 2 --cores-per-h0 2 --h0-mvms 1 --h1-mvms 1 --cross-mvms 1 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml --idle-refresh-period-ticks 7600 \
  --metrics-prefix /tmp/smoke/metrics --transfer-trace /tmp/smoke/transfers.csv
```

Outputs `<prefix>_summary.json`, `<prefix>_noc.csv`, `<prefix>_nodes.csv`,
`<prefix>_dram.csv` and the transfer trace in the Python schema. The writer
reproduces Python `json.dump(indent=2, sort_keys=True)`, `csv` CRLF rows and
float `repr`, so existing collectors can consume the files and the harness can
check byte identity.

### Verification

`scripts/check_cpp_exact_model.py` runs the Python oracle and the C++ binary
on identical inputs under `/usr/bin/time -v` and compares, with zero
tolerance, every field of `metrics_summary.json`, every cell of the three CSV
tables and every accepted NoC transfer record. It records wall time, peak RSS
and SHA-256 hashes of inputs, the Python sources ported from, the C++ sources
and the binary.

```bash
python3 scripts/check_cpp_exact_model.py --list
python3 scripts/check_cpp_exact_model.py --only smoke256_cir kings16k_cir
python3 scripts/check_cpp_exact_model.py --only torus64k_cir --skip-python  # reuse python/ dir
```

Status as of 2026-10-06 (details in
`results/cpp_exact_port_20261006/REPORT.md`): 18 cases from 256 spins to
131,072 spins pass with every compared artifact byte-identical, including a
4x4 mesh with link stalls, nondefault link/chiplet timing, an explicit
`--schedule` with owner overrides, a single-H1 64K toroidal case (13x faster
than Python) and the two-H1 128K community case compared against the saved
15 h campaign metrics (20 min 38 s in C++, 43x; metrics files only, no
transfer trace exists for that reference).

This is port/optimization equivalence. It adds no RTL coverage: the
`accuracy` label is reproduced from the Python logic and means what it means
there.

#### Direct comparison against stored VCS traces (stage 1b)

`scripts/check_cpp_exact_vcs_trace.py` is the C++ counterpart of
`scripts/check_cir_exact_vcs_trace.py`: it runs the binary with the same
driving conventions (`cir`, recorded geometry/engines, shared Ramulator
library/YAML, no idle modulus, default FIFO and link settings) and compares,
with zero tolerance, VCS phase timing, every accepted NoC transfer tuple and
the aggregate NoC flit/stall totals against a stored RTL trace, reusing the
Python checker's RTL parsers. With `--python-differential` it embeds the
stored Python-vs-RTL result and reports `three_way_agreement` only when both
runs passed on the same hashed inputs with equal observed values.

```bash
python3 scripts/check_cpp_exact_vcs_trace.py --dataset DS --schedule SCHED \
  --rtl-log rtl.log --rtl-events events.csv --rtl-noc noc.csv \
  --mesh-x 2 --mesh-y 1 --h0-per-h1 2 --cores-per-h0 4 \
  --python-differential differential.json --workdir OUT/cpp --output OUT/comparison.json
```

Status as of 2026-10-06 (details, hashes and per-trace artifacts in
`results/cpp_exact_port_20261006/rtl_differential/REPORT.md`): every stored
CIR VCS trace whose dataset, schedule and geometry could be recovered and
hashed was compared; see that report for the pass/fail table and the
diagnosed causes of the two historical Python failures that were re-run for
completeness. This only covers the configurations those traces exercise
(512 to 16,384-spin datasets on 2x1/2x2/4x2/4x4 meshes and three
65,536-spin single-H1 datasets, engines 4/2/4, one timing-only iteration
each; 20 valid runs pass, one deliberately re-run historical no-schedule
invocation fails identically to Python); it is not nominal
multi-H1 certification, not arithmetic/quality validation and runs no new
RTL simulation.

#### Dense occupancy and streaming traces (October 7)

Two opt-in flags, off by default (`results/dense_cir_vcs_20261007/`):
`--dense-all-blocks 1` occupies every unordered off-diagonal block pair from
a header-only dataset, the C++ mirror of the Python dense campaigns'
implicit `BlockOccupancyDataset(n, 0, pairs)`; it refuses files that carry
edge records. `--transfer-trace-stream PATH` writes accepted transfers as
they occur (same rows as `--transfer-trace`, generation order, cycle
non-decreasing) so dense multi-H1 runs with hundreds of millions of
transfers run in bounded memory. They are built into a second target,
`azilla_exact_cir_dense`, so the stage-1 `azilla_exact_cir` file keeps its
recorded hash (`cmake --build build/cpp_exact --target azilla_exact_cir_dense`).
`scripts/check_cpp_exact_vcs_trace.py` gained matching opt-in options
`--binary-arg`, `--reuse-cpp-run` and `--streaming-transfers` (gz-aware,
cycle-group comparison equivalent to the sorted-list comparison).

## Stage 3: cores-only streaming endpoint (and buffered v3)

Execution mode `cores-only` is ported for the two shared-fetch v3 endpoint
policies, selected with `--endpoint-policy`:

| policy | Python oracle | fetch_policy | core |
|---|---|---|---|
| `streaming` (default) | `python3 -m azilla_cycle_model.streaming_cli` | `single_fetch_two_slot_cir_routes_v3_streaming_endpoint_v1` | `streaming_core.StreamingCoreIteration` (accepted-row streaming, one shared MVM) |
| `buffered-v3` | `python3 -m azilla_cycle_model.shared_fetch_double_cli` | `single_fetch_two_slot_cir_routes_v3` | `core_iteration.CoreIteration` (32-beat load, then MVM) |

Plain `cores-only` of `azilla_cycle_model.cli` (`full_single_mvm_iteration_v1`,
directed per-endpoint fetch) is not ported.

New modules (one per Python module, timing-only, same decision order):

| Python | C++ |
|---|---|
| `streaming_core.py` / `core_iteration.py` (+ `compute.MVM`/`SpinCore` lifecycle) | `core_iteration.{hpp,cpp}` (`EndpointCore`, `EndpointMvm`) |
| `core_state_bank.py` | `core_state_bank.{hpp,cpp}` |
| `shared_fetch.py`, `shared_fetch_double.py` | `shared_fetch.{hpp,cpp}` (`PairReplay`, `DoublePairReplay`, `DoubleFetchStreamer`) |
| `core_iteration_pipeline.py` as specialised by `SharedFetchPipeline`/`DoubleFetchPipeline`/`StreamingPipeline` | `endpoint_pipeline.{hpp,cpp}` |
| `core_iteration_driver.py`, `core_full_controller.py`, `core_state_publication.py` | `cores_only_events.{hpp,cpp}` |
| `shared_fetch_events.run_full_cores` + `streaming_cli`/`shared_fetch_double_cli` wrappers | `RamulatorEventPerformanceModel::run_cores_only` (`exact_events.cpp`) |
| `scheduler.compile_cores_only_schedule` | `scheduler.{hpp,cpp}` |
| `mapping_adapter.MappingArtifact.load` (manifest + text schedule) | `main.cpp` (`simulate-mapped-exact-events --artifact DIR`) |

Shared-file changes: `json.hpp` (the JSON value type moved out of
`metrics.cpp`, plus an insertion-ordered object for Python dicts with int
keys, which `sort_keys` orders numerically), `metrics.cpp` (cores-only
`effective_mvm_engines`, `pipeline_contract`, int `average_hops` when no flit
was injected), `exact_events.{hpp,cpp}` (dispatch, `core_pipeline_contract`,
opt-in `--core-events` sink), `main.cpp`.

Build target `azilla_exact` (unified cir + cores-only). The stage-1
`azilla_exact_cir` / `azilla_exact_cir_dense` files are not rebuilt by it.

```bash
cmake --build <build dir> --target azilla_exact
azilla_exact simulate-exact-events --execution-mode cores-only --endpoint-policy streaming \
  --dataset DS --mesh-x 2 --mesh-y 1 --h0-per-h1 2 --cores-per-h0 4 --h0-mvms 4 --h1-mvms 2 --cross-mvms 4 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so --ramulator-config tb/ramulator_128x32.yaml \
  --metrics-prefix OUT/metrics --transfer-trace OUT/transfers.csv \
  [--schedule S] [--stall-period 7] [--core-events OUT/events.csv]
azilla_exact simulate-mapped-exact-events --artifact DIR --dataset DS --execution-mode cores-only ...
```

Opt-in test knobs (cores-only only): `--stall-period N` is the
`arithmetic_fixture['stall_period']` of `scripts/check_shared_fetch_two_h1.py`
(weights blocked for five cycles out of every N); `--core-events PATH` writes
the Python `core_event_observer` / `core_memory_observer` /
`core_state_observer` streams as `stream,cycle,event,a,b,c` rows.

### Stage 3 verification

- `scripts/check_cpp_streaming_model.py`: Python oracle vs C++ with zero
  tolerance on `metrics_summary.json`, the three CSVs, the transfer trace
  and (where requested) the event streams. Cases with a stall period, with
  event comparison or with the buffered-v3 policy drive the oracle through
  `scripts/run_streaming_oracle.py`, a wrapper that only installs the
  checkers' fixture/observers around the unchanged `streaming_cli`.
  Completed `results/streaming_endpoint_matrix_20261005_v1` cells are
  compared against their stored metrics (Python not rerun).
- `scripts/check_cpp_streaming_vcs_trace.py` (+ `run_streaming_rtl_all.py`):
  C++ vs stored streaming VCS traces, applying the checks of
  `check_shared_fetch_two_h1.py` / `check_shared_fetch_single_h1.py`
  (FULL_INIT, total, jobs, every CORE_INT/CORE_MEM/CORE_STATE/CORE_NOC
  record) with a three-way statement against the stored Python differential.

Status and coverage: `results/cpp_exact_port_20261006/streaming/REPORT.md`
(mirror: `/var/tmp/azilla_cpp_streaming_20261007/results/REPORT.md`).
Port equivalence only; no new RTL coverage.

## Known gaps

- Plain `cores-only` of `azilla_cycle_model.cli` (directed per-endpoint
  fetch), hybrid scheduling and the arithmetic-fixture differential cases
  are not ported; the binary is timing-only.
- Buffered v3 cores-only is ported but verified on one small case only.
- Nondefault `--link-*` and `--chiplet-link-latency-cycles` are verified only
  on small geometries (three harness cases).
- `simulate-mapped-exact-events --artifact` reads the manifest geometry and
  text schedule only; it does not validate the NumPy permutation arrays.

