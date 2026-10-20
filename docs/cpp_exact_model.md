# C++ exact-event model

`model/cpp_exact/` is a standalone C++20 reimplementation of the Python
timing-only exact-event model
(`azilla_cycle_model.exact_events.RamulatorEventPerformanceModel`). It covers
execution mode `cir` (stage 1, below), the cores-only shared-fetch v3
endpoint policies, streaming and buffered (stage 3, later in this document),
and two C++-only variants without Python or RTL counterparts: a flat
weight-stationary state-exchange baseline and H0-anchored symmetric
ownership (the two sections before the known gaps).
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

## Threaded DRAM ticking (`--dram-threads N`, opt-in)

85-100% of the C++ run time is spent inside the Ramulator bridge, ticking
every independent DRAM system 40 times per accelerator cycle (20 systems at
128K, 40 at 256K, 80 at 512K; ~0.43 us per system tick). `--dram-threads N`
(default 1 = the historical serial `az_dram_tick` call) ticks the systems on
N threads (N-1 persistent workers plus the calling thread). Only the tick
phase is parallel: each system is claimed by one thread per dispatch and
ticked 40 times; `send`, `pop`, statistics and all model logic stay on the
calling thread in the original program order, so results are bit-identical
by construction, not by tolerance. Verified in
`results/cpp_exact_port_20261006/threaded/REPORT.md` (threads 1/2/4/8 against
the stored serial outputs of the CIR, streaming and dense-128K/256K runs, and
against stored VCS traces).

N > 1 requires the multi-thread-capable bridge `libazilla_ramulator_mt.so`
(`model/cpp_exact/ramulator_bridge_mt.cpp`, the same code as
`tb/ramulator_dpi.cpp` plus per-system `az_dram_tick_system` and
`az_dram_advance_clock`, linked against the unchanged
`third_party/ramulator2/libramulator.so`). The RTL testbench and the Python
model keep `build/cycle_model_ramulator/libazilla_ramulator.so`; the binary
refuses N > 1 with a bridge that lacks the per-system entries.

```bash
cmake --build build/cpp_exact --target azilla_exact --target azilla_ramulator_mt -j4
build/cpp_exact/azilla_exact simulate-exact-events ... \
  --ramulator-library build/cpp_exact/libazilla_ramulator_mt.so --dram-threads 8
```

Thread-safety basis (details in the report): the bridge's only shared
mutable state on the tick path is the global issue clock read by the
completion callback for the latency statistic; the MT bridge keeps a
per-system tick counter that observes the same values, and the caller
advances the issue clock after all systems were ticked (asserted). Ramulator2
holds only init-time static registries (factory, DRAM spec creators, logger
names); `IMemorySystem::tick()` touches member state of one memory system.
The submodule is not modified.

Pool: workers spin (`AZILLA_DRAM_SPIN_US`, default 2000 us) before blocking
on a condition variable; dispatch cost is ~1-5 us while spinning. The stdout
line `dram_threads=N parallel_dispatches=... dispatches_with_sleeping_worker=...`
reports how often a dispatch had to wake a sleeping worker. Speedup saturates
on the serial model logic (Amdahl); see the report's performance table.
Each thread adds no memory of note (RSS is unchanged within a few MB).

`scripts/check_cpp_exact_model.py` and `scripts/check_cpp_streaming_model.py`
accept `--cpp-library PATH` and repeatable `--cpp-arg` so the C++ side can be
run with the MT bridge and `--dram-threads N` against the stored oracle
directories (`--skip-python`).

## Flat weight-stationary baseline (`--execution-mode flat-stationary`)

A third execution mode, C++ only, implements the conventional Ising/SB
accelerator organization as a comparison point for CIR: weights stay where
the update happens and only spin states move.

- Every occupied unordered block `(A, B)` is stored twice, as the row
  representation at A's H0 and the transposed representation at B's H0, and
  becomes two directed jobs on those H0s (`compile_flat_schedule`, the same
  expansion as the cores-only schedule). H1-local and cross-H1 owners,
  engines and DRAM systems do no work (their rows carry zero jobs).
- Each H0 engine is the hierarchy engine in a directed configuration
  (`Engine::directed`): it fetches one operand (the source block's 1-bit
  states) from the H0 state table and returns one 128-byte partial to its
  local core through the H0 adapter, on chip. The block pass still takes 32
  row-serial cycles, so a directed engine produces one partial per 32 cycles
  where the symmetric CIR engine produces two; at equal engine counts it has
  half the multipliers and half the useful work per streamed weight byte.
- The H0 state table is indexed by global block (`total_blocks` entries, 4
  bytes each) and is filled with one write per distinct source block the
  H0 consumes before its engines start (the H1-table fill rule of CIR).
- State exchange between H1s uses the CIR publication mechanism unchanged:
  one `NOC_STATE` flit per (source block, consuming H1) pair with differing
  H1s, released at the CIR cadence before compute; no partial flits cross the
  NoC. The epoch-done barrier is the same as CIR.
- Everything else (DRAM addressing, streamer, lanes, Ramulator config, mesh,
  arbitration, initialization cycles, metrics schema) is shared with `cir`.
  `--h0-mvms` sets the directed engines per H0; `--h1-mvms`/`--cross-mvms`
  are accepted for schema compatibility only.

The summary reports `execution_mode = flat-stationary`,
`effective_mvm_engines` with `active_dram_systems` (one per H0) and
`h0_state_entries`, and the `cores_only_ablation` block with the directed job,
read and stored-copy counts (two per unordered block) and the remote state
packet count. No RTL implements this baseline; `accuracy` is always
`cycle-structured-unverified`. The CIR and cores-only paths are unchanged
(`Engine::directed` defaults to false and is never set outside this mode);
the regression harnesses were re-run byte-identical after the change.
Campaign: `results/flat_stationary_baseline_20261015/`.

## H0-anchored symmetric ownership (`--execution-mode cir-h0-anchored`)

A CIR variant, C++ only, that keeps the single symmetric fetch but removes
the hierarchy owners: a block inside one H0 stays there; every other block is
owned by one endpoint H0 and evaluated once by that H0's symmetric engines.
The local partial reaches its core through the H0 adapter as in CIR; the
remote partial leaves through one 256-bit uplink per H0 (`H0Uplink`,
`model/cpp_exact/anchored.cpp`):

- Input side: round-robin, packet-locking selection over the engines whose
  current partial addresses another H0 (the cross-node rule), one beat per
  cycle into two FIFOs of `--anchored-uplink-packets` (default 4) four-beat
  partials, one for sibling H0s and one for other H1s.
- Output side, one beat per cycle: a sibling partial is switched by the H1
  child crossbar, which in this mode has `h1_mvms + h0_per_h1` sources and
  gives the mesh (parent) port priority so the mesh always drains; a partial
  for another H1 is arbitrated among the H1's uplinks
  (`UplinkInjectionArbiter`), takes the cross node's slot in the local
  injection arbiter and travels the mesh to the existing cross-partial
  receive path. Between packets the uplink moves to the other queue when its
  head is not accepted.
- Owner choice (`compile_anchored_schedule`): forced within-H0 blocks are
  counted first; the off-H0 blocks are grouped by H0 pair, split by a
  sequential greedy on the cost max(engine load, owner H1 injection-port
  load) with engine load 32/engines cycles per job and port load 4 beats x 3
  per cross-H1 packet (the weight that balanced community 128K's two ports
  under eight-way arbitration; `AZILLA_ANCHORED_PORT_WEIGHT` overrides it),
  then refined by local search that moves batches of blocks off the heaviest
  H0s (tried in descending load order) while the lexicographic objective
  (max port load over H1s, H0 loads sorted descending) improves. The
  sequential greedy alone leaves a load gradient in H0 order (dense 128K:
  297K-753K jobs per H0), and a makespan-only objective stalls on tied
  maxima; the sorted-vector form balanced dense 128K and community 128K
  (47.9K-50.0K jobs). A scheduling heuristic, not a hardware parameter.
- H0 state tables are global-indexed and filled like the flat baseline;
  publications are the (endpoint block, owner H1) pairs whose H1s differ.
  H1 and cross engines and DRAM systems carry no jobs. The quiet window
  before the epoch-done barrier waits for every H0 node as well as the cross
  nodes (H0s are the mesh sources here).

Per unordered block: one read, one stored copy, at most four partial flits on
the mesh (CIR: eight for cross-owned blocks). The summary carries an
`anchored` block with uplink beat/stall and engine output-stall counters.
`accuracy` is reported as `cycle-structured-unverified`.

`--anchored-accumulate 1` (accumulate-at-owner): the uplink sums each remote
partial beat into a per-remote-block accumulator at the owner H0 (one
read-modify-write beat per cycle, never stalls the engine) and, once the H0
node is done, flushes one four-beat packet per touched remote block in
ascending block order through the same FIFOs; the quiet window also waits
for every uplink to have flushed.

October 15 model fix (anchored-guarded code only): the RUN -> quiet-window
condition now requires every uplink to be *drained* (`H0Uplink::drained()`:
`flushed()` in accumulate mode, both FIFOs empty in FIFO mode). Before, FIFO
mode ignored the uplink FIFOs, so `EPOCH_DONE` could be injected while a
sibling-bound packet was still queued at an uplink (not NoC activity), i.e.
a partial delivered after completion; the RTL testbench waits for the drain
and the VCS differential exposed the gap (dense 512-spin 2x1 FIFO case: RTL
470 vs model 454 iteration cycles; the only differing transfers were the
`EPOCH_DONE` flits). Effect of the fix on the 17 small smoke cases re-run in
`cir-h0-anchored` (`results/anchored_rtl_20261015/model_fix/`): accumulate
mode byte-identical in all 17; FIFO mode changed in 4 (iteration +4 to +16
cycles), identical in 13. `cir` mode outputs are byte-identical to the stored
October 6 cases (the change sits inside the `anchored` guard). The campaign
`results/flat_stationary_baseline_20261015/` (anchored `anch4` points, FIFO
mode) predates the fix; its iteration counts can be low by at most the tail
drain of the last uplink, a few tens of cycles, not re-run.

RTL: `rtl/experimental/anchored/` implements this mode (accumulate and FIFO
uplinks) as a swap-in set with the testbench `tb/ising_mesh_anchored_tb.sv`;
`--dump-anchored-schedule PATH` writes the compiled owner assignment and
publications the testbench consumes. Small-geometry VCS/C++ differentials
(timing, every NoC transfer, NoC totals, plus the testbench's functional
next-state check): `scripts/run_anchored_rtl_differentials.py`,
`results/anchored_rtl_20261015/REPORT.md`, `docs/anchored_rtl.md`. Those
configurations are RTL-matched; everything else in this mode remains
cycle-structured-unverified (the summary label is not changed).

## Clock ratio, front end and memory technology (October 2026)

Three options, all modes, defaults unchanged and byte-identical to the
recorded evidence (`results/anchored_rtl_20261015/model_fix/default64.json`):

- `--ticks-per-cycle N`: Ramulator ticks (250 ps) per accelerator cycle.
  40 is the RTL-verified ratio (10 ns, 100 MHz, `docs/comparison_contract.md`);
  4 models a 1 ns cycle against the same memory. Convert cycles to time with
  the ratio actually used; the 1 GHz figures are a modeling assumption, not a
  closed timing result.
- `--max-outstanding N`: 32-byte requests in flight per H0 streamer
  (`PerformanceConfig::max_outstanding`, RTL `dram_weight_streamer`
  `MAX_OUTSTANDING`, default 64). At 4 ticks per cycle 64 x 32 B caps the
  stream near 92 B per cycle per H0 at ~20 cycles of DRAM latency; the 1 GHz
  campaigns use 256.
- `--ramulator-config`: `tb/ramulator_hbm4_config.py` exports a co-packaged
  HBM4 slice (Ramulator2 `HBM4`, `HBM4_32Gb_4Hi`, 8 Gb/s, 8 x 64-bit channels
  per H0 = 512 GB/s, the same peak as the 128-pin GDDR6 projection; the tick
  stays 250 ps). `AZILLA_ADDR_MAPPER=MOP4CLXOR` replaces the `RoBaRaCoCh`
  map, under which a sequential 32-byte stream lands in one bank group and is
  tCCD_L-bound at 4 ticks per cycle; with MOP4 the remaining plateau is the
  activate rate (1 KB rows, 4 lines per activate, ~261 B per cycle per H0 at
  the 512 B peak). The RTL testbench still hardcodes `tb/ramulator_128x32.yaml`.

Campaigns: `results/clock_sensitivity_20261019` (calibration grid),
`results/dense_1ghz_hbm4_20261019` and `results/sparse_1ghz_hbm4_20261020`
(anchored+accumulate 16 engines/H0 vs flat 32 engines/H0 at 4 ticks, HBM4,
MOP4, 256 outstanding). All are cycle-structured-unverified: no RTL
differential exists at 4 ticks, 16/32 engines or 256 outstanding, and the
testbench lacks parameters for the outstanding count and the yaml.

## Known gaps

- Plain `cores-only` of `azilla_cycle_model.cli` (directed per-endpoint
  fetch), hybrid scheduling and the arithmetic-fixture differential cases
  are not ported; the binary is timing-only.
- Buffered v3 cores-only is ported but verified on one small case only.
- Nondefault `--link-*` and `--chiplet-link-latency-cycles` are verified only
  on small geometries (three harness cases).
- `simulate-mapped-exact-events --artifact` reads the manifest geometry and
  text schedule only; it does not validate the NumPy permutation arrays.

