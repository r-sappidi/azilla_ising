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
