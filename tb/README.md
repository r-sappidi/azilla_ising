# Ising RTL Testbench Quick Start

Run these commands from the `azilla_ising` repository root.

## Spin-core test

```bash
make -C tb run
```

## Full hierarchy and FlooNoC test

The default test models 256 spins using `tb/datasets/g256_smoke.txt` and does
not skip zero weight blocks:

```bash
make -C tb floo-mesh-test
```

Enable testbench-side zero-block skipping with:

```bash
make -C tb floo-mesh-test SKIP_ZERO_BLOCKS=1
```

Select a dataset and hierarchy configuration by passing Make variables:

```bash
make -C tb floo-mesh-test \
  MESH_X_COUNT=4 \
  MESH_Y_COUNT=4 \
  H0_COUNT=4 \
  CORES_PER_H0=32 \
  H0_MVM_COUNT=4 \
  H1_MVM_COUNT=4 \
  CROSS_MVM_COUNT=4 \
  FIFO_DEPTH=4 \
  ITERATION_COUNT=1 \
  SKIP_ZERO_BLOCKS=1 \
  DATASET=g65536_kings.txt
```

Supported variables and their defaults are:

| Variable | Default | Meaning |
|---|---:|---|
| `MESH_X_COUNT` | 2 | Top-level mesh width |
| `MESH_Y_COUNT` | 1 | Top-level mesh height |
| `H0_COUNT` | 2 | H0 tiles under each H1 tile |
| `CORES_PER_H0` | 2 | 32-spin cores in each H0 tile |
| `H0_MVM_COUNT` | 1 | MVM engines in each H0 node |
| `H1_MVM_COUNT` | 1 | MVM engines in each H1 node |
| `CROSS_MVM_COUNT` | 1 | Cross-H1 MVM engines per mesh node |
| `FIFO_DEPTH` | 4 | Partial-result FIFO depth |
| `ITERATION_COUNT` | 1 | Ising iterations to simulate |
| `SKIP_ZERO_BLOCKS` | 0 | Skip entirely zero off-diagonal blocks |
| `DATASET` | `g256_smoke.txt` | File under `tb/datasets` |
| `NOC_STATS_FILE` | `noc_stats.csv` | Per-link and per-endpoint NoC statistics output |

The implemented spin count is:

```text
MESH_X_COUNT * MESH_Y_COUNT * H0_COUNT * CORES_PER_H0 * 32
```

This must equal the vertex count in the dataset header. Mesh dimensions,
hierarchy counts, engine counts, and FIFO depth must be supported powers of
two; invalid configurations terminate with an explanatory error.

## Dataset format

Store datasets in `tb/datasets`. The text format is:

```text
<vertex_count> <known_best_cut>
<one_based_source> <one_based_destination> <signed_int8_weight>
...
```

Missing interactions are treated as zero. Include both directed records when
the input matrix is symmetric.

Add `+VERBOSE_BLOCKS` directly to the generated simulator command when
debugging individual block dispatches. Build products are kept in separate
configuration-specific directories under `build/`.

## Ramulator-backed node-local DRAM

Build Ramulator 2.1 once before running the memory-timed tests:

```bash
cmake -S third_party/ramulator2 -B third_party/ramulator2/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DRAMULATOR_PYTHON_BINDINGS=OFF
cmake --build third_party/ramulator2/build -j
```

The checked-in `tb/ramulator_128x32.yaml` is sufficient for the timing tests.
Building with Python bindings disabled avoids regenerating source files inside
the Ramulator submodule. Enable the bindings only when intentionally
regenerating the YAML through `tb/ramulator_config.py`.

Run the standalone tagged weight-streamer check with:

```bash
make -C tb dram-streamer-test
```

Run the complete hierarchy with one independent Ramulator interface per H0,
H1, and cross-H1 compute node with:

```bash
make -C tb floo-mesh-dram-test DATASET=g256_smoke.txt
```

This uses the checked-in `ramulator_128x32.yaml`. When intentionally changing
`MEM_PIN_COUNT` or `MEM_PIN_GBPS`, build/install Ramulator's optional Python
bindings and add `REGENERATE_RAMULATOR_CONFIG=1` to regenerate the YAML.

In Ramulator mode, the testbench compiles the selected dense or sparse block
set into per-node descriptor queues. A ready-aware round-robin dispatcher at
each H0, H1, and cross-H1 node assigns the next descriptor to an engine whose
streamer command port is ready. Backpressure stalls only the affected port;
other interfaces and engines continue dispatching. `SKIP_ZERO_BLOCKS=0`
therefore uses a fixed dense schedule containing every off-diagonal block pair.

Each full-mesh run prints aggregate NoC traffic and hotspot summaries and
writes `NOC_STATS_FILE` as CSV. The CSV reports accepted flits, completed
packets, packet-type counts, valid-but-stalled cycles, utilization over the
iteration window, and backpressure fraction for every endpoint and directed
physical link. For example, use `NOC_STATS_FILE=logs/run_noc.csv` to retain a
run-specific report.

For time-resolved analysis, enable the optional interval and event traces:

```bash
make -C tb floo-mesh-dram-test TIMING_ONLY=1 DATASET=g4096_kings.txt \
  SIM_ARGS='+NOC_STATS_FILE=logs/run_noc.csv \
            +NOC_TIMELINE_FILE=logs/run_noc_timeline.csv \
            +NOC_EVENT_FILE=logs/run_noc_events.csv \
            +NOC_STATS_INTERVAL=100'
```

`NOC_TIMELINE_FILE` records interval deltas for every injection port,
ejection port, and directed physical link: offered and accepted traffic,
stall cycles, packet types, utilization, backpressure, and global in-flight
flits. `NOC_EVENT_FILE` records accepted flits and stall begin/end events with
packet metadata. Event output can be restricted without rebuilding using
`+NOC_TRACE_START=<cycle>`, `+NOC_TRACE_END=<cycle>`, and
`+NOC_TRACE_NODE=<linear_node_id>`. Leaving both trace-file plusargs unset
disables temporal file I/O.

Postprocess a trace using only the Python standard library:

```bash
python3 scripts/analyze_noc_trace.py \
  --timeline logs/run_noc_timeline.csv \
  --events logs/run_noc_events.csv \
  --output-prefix logs/run_noc_analysis
```

This produces directed-link time series, ranked resource hotspots, completed
stall episodes, and matched injection-to-ejection flit latencies. The monitor
reports externally observable ready/valid pressure and total in-flight flits;
it does not claim exact occupancy of FlooNoC's internal input FIFOs.

Generate PNG and vector PDF visualizations with:

```bash
scripts/visualize_noc_trace.sh logs/run_noc logs/run_noc_plots
```

The prefix identifies `<prefix>_timeline.csv` and `<prefix>_events.csv`. The
wrapper reruns the CSV analysis and creates link-utilization/backpressure time
series, a directed mesh hotspot map, in-flight traffic, a flit-latency
histogram, and a stall-episode timeline. To control formats or the number of
displayed links, invoke the plotting script directly:

```bash
python3 scripts/plot_noc_trace.py \
  --timeline logs/run_noc_timeline.csv \
  --events logs/run_noc_events.csv \
  --output-dir logs/run_noc_plots \
  --format both --top-links 12
```

The plotting script requires Matplotlib. PDF output is suitable for vector
figures; PNG output defaults to 180 DPI for quick inspection.

### Compile-once timing model and runtime schedules

Set `TIMING_ONLY=1` to remove only the MVM add/subtract arithmetic while
retaining the original row timing, hierarchy control, result buffers,
Ramulator streamers, packet adapters, and FlooNoC RTL:

```bash
make -C tb floo-mesh-dram-test TIMING_ONLY=1 \
  DATASET=g4096_kings.txt SKIP_ZERO_BLOCKS=0 \
  MESH_X_COUNT=4 MESH_Y_COUNT=4 H0_COUNT=2 CORES_PER_H0=4 \
  H0_MVM_COUNT=1 H1_MVM_COUNT=1 CROSS_MVM_COUNT=16 \
  SIM_ARGS='+DUMP_SCHEDULE=logs/g4096.schedule'
```

The schedule is a whitespace-separated runtime file with one record per
off-diagonal block:

```text
block_a block_b owner
```

`owner` selects the cross-H1 compute node; it is ignored for H0/H1-local
pairs and may be `-1`. Engine assignment is deliberately absent: the local
ready-aware round-robin arbiter chooses among currently available engines.
Modify the file, then rerun the already-built executable with
`+SCHEDULE=<path>` and no elaboration or C++ compilation:

```bash
build/ising_mesh_floo_4x4_h02_c4_e1-1-16_f4_s0_r1_i1_t1/Vising_mesh_tb \
  +DATASET=g4096_kings.txt +SCHEDULE=logs/alternate.schedule \
  +NOC_STATS_FILE=logs/alternate_noc.csv
```

Timing-only runs intentionally skip functional spin-state comparison because
partial payloads are zero. They preserve timing-relevant handshakes and packet
framing; use normal `TIMING_ONLY=0` runs for arithmetic correctness.

Exercise iteration teardown/restart and deterministic RTL noise with:

```bash
make -C tb floo-mesh-dram-test DATASET=g256_smoke.txt \
  ITERATION_COUNT=3 COEFF_A_VALUE=0 COEFF_B_VALUE=1 NOISE_AMPLITUDE=3
```

When an external Octave/MATLAB golden file was generated with the same
parameters, append `SIM_ARGS=+MATLAB_GOLDEN=<path>` to compare every spin on
every iteration.

The memory test defaults to a projected 128-pin, 32-Gb/s-per-pin GDDR-style
interface. Override it with `MEM_PIN_COUNT`, `MEM_PIN_GBPS`, and `MEM_LANES`.
`MEM_LANES` is the number of independent 256-bit RTL request/response lanes;
the pin count and pin rate configure Ramulator's aggregate physical bandwidth.
For example:

```bash
make -C tb floo-mesh-dram-test \
  MEM_PIN_COUNT=256 MEM_PIN_GBPS=32 MEM_LANES=32
```

Each scheduled 32-by-32 int8 block becomes 32 tagged 32-byte reads. Responses
may return out of order and are reassembled by `{engine, buffer, beat}` before
being streamed to the hierarchy-node MVM. Each physical interface permits at
most 64 outstanding reads. The generated `tb/ramulator_128x32.yaml` is a timing
projection derived from Ramulator's GDDR6 model; it is useful for architectural
comparison but is not a vendor-qualified 32-Gb/s GDDR timing specification.

## Ramulator performance sweep

Compare MVM counts and independent 256-bit result-injection lanes without
elaborating the full 65,536-spin mesh:

```bash
python3 scripts/run_ramulator_perf_sweep.py
```

Each measured point contains a real `hierarchy_node`, tagged weight streamer,
and Ramulator memory system. The script applies the measured block rates to
exact dense H0, H1, and cross-H1 interaction counts. Its default resource
limits match the current large configuration: 96 independent memory interfaces
and 384 total MVM engines. Logs and CSV results are written below
`build/ramulator_perf_sweep/`.

For a quick validation before the full sweep:

```bash
python3 scripts/run_ramulator_perf_sweep.py \
  --mvm-counts 1,2 --output-lanes 1,2 --work-blocks 32 \
  --h1-counts 8,16 --h0-counts 4,8
```

This is a dense first-order projection, not final full-system validation. It
includes measured DRAM timing, streamer/MVM overlap, and injection
backpressure, but excludes mesh hop contention, scheduler overhead, and
production CDC/PHY effects.

## Compile-once hybrid NoC replay

For a complete workflow covering custom mapping files, configuration sweeps,
metrics, and experimental methodology, see
[`MAPPING_EXPERIMENTS.md`](../MAPPING_EXPERIMENTS.md).

For rapid mapping and topology exploration, build a maximum 4-by-4 fabric of
the actual `azilla_floo_router`/FlooNoC RTL once:

```bash
make -C tb hybrid-noc-build
```

The runtime workload generator keeps the architectural 32-spin block and
1,024-spin H0 fixed. It reads sparse block occupancy from the dataset (or an
explicit mapped schedule), assigns cross-H1 work to the configured engines,
and emits state, four-flit partial, and epoch-done packets with release cycles:

```bash
python3 scripts/generate_hybrid_noc_workload.py \
  --dataset tb/datasets/g16384_kings.txt \
  --output logs/g16384_hybrid.traffic \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 1 \
  --cross-engines 16 --block-service-cycles 64
```

The traffic file begins with `mesh_x mesh_y last_phase`, followed by:

```text
phase release_cycle source_node destination_node packet_type block_id flit_count
```

Pass an existing `block_a block_b owner_node` mapping with
`--schedule <path>` or `HYBRID_SCHEDULE=<path>`. The mapping changes only the
runtime traffic; it does not rebuild the router fabric.

Replay it through the compiled routers and generate the same aggregate,
timeline, and event CSV schemas used by the full RTL testbench:

```bash
make -C tb hybrid-noc-run \
  HYBRID_TRAFFIC=logs/g16384_hybrid.traffic \
  HYBRID_STATS_PREFIX=logs/g16384_hybrid

scripts/visualize_noc_trace.sh \
  logs/g16384_hybrid logs/g16384_hybrid_plots
```

Or generate and run in one command:

```bash
make -C tb hybrid-noc-test \
  HYBRID_DATASET=tb/datasets/g16384_kings.txt \
  HYBRID_MESH_X=4 HYBRID_MESH_Y=4 HYBRID_H0_PER_H1=1 \
  HYBRID_CROSS_ENGINES=16 HYBRID_BLOCK_SERVICE_CYCLES=64 \
  HYBRID_TRAFFIC=logs/g16384_hybrid.traffic \
  HYBRID_STATS_PREFIX=logs/g16384_hybrid
```

Changing the active mesh rectangle, H0s per H1, mapping, engine count, or
release schedule does not re-elaborate RTL as long as it fits inside the
compiled maximum fabric. FIFO depth, flit format, and maximum dimensions are
compile-time router properties and do require rebuilding.

The replay is router-cycle-accurate for the supplied releases: it preserves
FlooNoC XY routing, input FIFOs, ready/valid backpressure, arbitration, and
wormhole packet locking. The default 64-cycle block service time is an
endpoint timing abstraction, not a live Ramulator execution. Calibrate it or
supply measured release cycles before using absolute end-to-end cycle counts;
packet paths, congestion, and link timing after release use the real RTL.
