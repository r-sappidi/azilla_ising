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
  -DCMAKE_BUILD_TYPE=Release
cmake --build third_party/ramulator2/build -j
```

Run the standalone tagged weight-streamer check with:

```bash
make -C tb dram-streamer-test
```

Run the complete hierarchy with one independent Ramulator interface per H0,
H1, and cross-H1 compute node with:

```bash
make -C tb floo-mesh-dram-test DATASET=g256_smoke.txt
```

In Ramulator mode, the testbench compiles the selected dense or sparse block
set into deterministic per-engine queues and drives every H0, H1, and cross-H1
command port concurrently. Backpressure stalls only the affected port; other
interfaces and engines continue dispatching. `SKIP_ZERO_BLOCKS=0` therefore
uses a fixed dense schedule containing every off-diagonal block pair.

Each full-mesh run prints aggregate NoC traffic and hotspot summaries and
writes `NOC_STATS_FILE` as CSV. The CSV reports accepted flits, completed
packets, packet-type counts, valid-but-stalled cycles, utilization over the
iteration window, and backpressure fraction for every endpoint and directed
physical link. For example, use `NOC_STATS_FILE=logs/run_noc.csv` to retain a
run-specific report.

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
block_a block_b owner engine
```

`owner` selects the cross-H1 compute node; it is ignored for H0/H1-local
pairs and may be `-1`. Modify the file, then rerun the already-built executable
with `+SCHEDULE=<path>` and no elaboration or C++ compilation:

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
