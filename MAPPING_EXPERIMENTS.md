# Testing Mapping Algorithms and Architecture Configurations

This guide describes the compile-once hybrid NoC model used to compare sparse
mapping, cross-H1 work placement, and hierarchy configurations without
re-elaborating the complete accelerator for every experiment.

For end-to-end scalability projections, the preferred path is now the Python
event-compressed model described in `model/README.md`. The hybrid workflow in
this document remains useful as an independent real-RTL router replay and NoC
trace generator; it is not the endpoint/memory performance model.

The model instantiates the real Azilla/FlooNoC router RTL. A software workload
generator replaces the arithmetic datapaths with timed packet releases. Once
the maximum router fabric is compiled, mappings and supported runtime
geometries can be changed by editing text files and rerunning the executable.

## What can be changed at runtime

The following do not require Verilator to rebuild the router fabric:

- active mesh dimensions within the compiled maximum rectangle;
- H0 tiles grouped under each H1;
- placement of cross-H1 block computations on mesh nodes;
- the number of modeled cross-H1 engines per node;
- fixed block-service latency;
- sparse block schedule and packet release order.

The following are compile-time router properties and require rebuilding:

- maximum mesh dimensions;
- router FIFO depth;
- flit width or packet format;
- changes to FlooNoC or the Azilla router wrapper.

Architectural constants retained by the runtime model are:

```text
32 spins per block/core
32 blocks per H0
1,024 spins per H0
4 flits per 32-element partial result
```

For a selected configuration, the dataset size must be:

```text
spin_count = mesh_x * mesh_y * h0_per_h1 * 1,024
```

## Mapper-to-event interface

The graph partitioner is vendored under
`model/graph_mapping/graph_compression`. Its sparse entry point avoids a
dense adjacency matrix and returns:

1. `permutation[new_vertex] = old_vertex`;
2. the inverse old-to-new permutation;
3. unique occupied 32-by-32 block coordinates.

`model/azilla_cycle_model/mapping_adapter.py` turns these values into a
versioned artifact. It fixes local blocks to their RTL H0/H1 resources and
assigns only cross-H1 blocks to compute owners. This separation is important:
the generic mapper's free assignment of every tile to an abstract core is not
a legal representation of Azilla's resident hierarchy.

Create and run an artifact with:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli map-graph \
  --dataset tb/datasets/g16384_kings.txt \
  --output-dir mappings/g16384 \
  --write-permuted-dataset mappings/g16384/dataset.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 1 --cores-per-h0 32 \
  --device cuda --require-cuda

PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-mapped-events \
  --artifact mappings/g16384
```

The artifact also emits `schedule.txt`, so the same mapping can feed this
document's hybrid replay or the exact arithmetic/Ramulator model. Exact
functional runs must use the emitted permuted dataset with that schedule.
Using mapped block coordinates with the original vertex numbering is invalid.

The artifact-direct exact timing command is:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-mapped-exact-events \
  --artifact mappings/g16384 \
  --dataset mappings/g16384/dataset.txt \
  --h0-mvms 1 --h1-mvms 1 --cross-mvms 16 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --idle-refresh-period-ticks 7600 \
  --metrics-prefix results/g16384/my_mapper \
  --transfer-trace results/g16384/my_mapper_transfers.csv
```

This uses live Ramulator2 timing rather than the older event model's fixed
endpoint profile. It steps every active memory, hierarchy, and network cycle
and is intended for final mapping comparisons. The optional metrics prefix
produces:

```text
<prefix>_summary.json   complete machine-readable result and hop histogram
<prefix>_noc.csv        per-endpoint and per-directed-link traffic/stalls
<prefix>_nodes.csv      per-H1 work allocation and completion timing
<prefix>_dram.csv       per-memory-system requests, retries, and latency
```

`--transfer-trace` additionally writes every accepted injection, physical hop,
and ejection with its cycle and packet metadata. The NoC CSV includes offered
cycles, accepted flits, stalls, completed packets, packet-type counts,
utilization, and backpressure. The node CSV separates H0, H1, and cross-H1
jobs, state-publication load, and completion cycles. The DRAM CSV identifies
each hierarchy endpoint and reports accepted/rejected/completed requests,
outstanding requests, and average/maximum latency in ticks and accelerator
cycles.

The exporter checks that per-resource accepted/stall sums equal the global NoC
counters, that the hop histogram reproduces physical-link traffic, and that
each DRAM system completes exactly 32 reads per scheduled block before writing
the files.

Alternative algorithms should call
`artifact_from_mapping(..., owner_assigner=...)`. This provides one stable
test interface for vertex-placement algorithms and cross-owner algorithms,
while artifact validation prevents illegal local movement, duplicate blocks,
missing cross owners, and permutation inconsistencies.

For a packaged implementation, pass
`--chiplet-link-latency-cycles N` to either event simulator. This models a
fully-pipelined, fixed-latency crossing inside each H1 package for H0 state
publication/snapshot traffic and H1/cross partial return traffic. Keep this
parameter separate from the `--link-*` options, which describe physical links
between H1 packages. Nonzero values are projections rather than RTL-certified
timing, and are recorded in the exact-event summary JSON.

## One-time build

Run from the repository root:

```bash
make -C tb hybrid-noc-build
```

The default build contains a maximum 4-by-4 mesh with four-flit-deep router
input FIFOs. Override the maximum only when a larger fabric is needed:

```bash
make -C tb hybrid-noc-build \
  HYBRID_MAX_X=8 HYBRID_MAX_Y=8 HYBRID_FIFO_DEPTH=4
```

Changing a maximum or FIFO depth creates a different build configuration.

## Three mapping decisions

Mapping experiments should distinguish three independent decisions:

1. **Spin placement:** which 32-spin block and H1 own each problem vertex.
   The current generator derives placement from the dataset's vertex order.
   Test a graph partitioner or vertex permutation by producing a consistently
   permuted dataset before generating the schedule.
2. **Interaction placement:** which mesh node computes each cross-H1 block.
   This is controlled by the `owner_node` field in the mapping file.
3. **Engine dispatch:** which engine at the selected owner accepts the block.
   Engine IDs are deliberately absent from the mapping file. The model assigns
   work to the earliest available modeled engine, representing ready-aware
   dynamic dispatch.

Separating these decisions makes it possible to determine whether an
improvement comes from graph locality, balanced compute ownership, or temporal
scheduling.

## Mapping file format

A custom mapping is a whitespace-separated text file with one unordered
off-diagonal 32-by-32 block interaction per line:

```text
block_a block_b owner_node
```

- `block_a` and `block_b` are zero-based global 32-spin block IDs.
- Require `block_a < block_b`.
- Each required interaction should appear exactly once.
- Missing interactions are omitted for sparse execution.
- For interactions within one H0 or H1, use `-1`; the owner is determined by
  the hierarchy and the field is ignored.
- For cross-H1 interactions, `owner_node` is a zero-based mesh node in
  row-major order: `node = y * mesh_x + x`.
- Do not include an engine ID.

For a 16,384-spin, 2-by-2 configuration with four H0s per H1, each H1 owns 128
global blocks. Examples are:

```text
0 8 -1
0 40 -1
0 140 2
```

These records mean:

- blocks 0 and 8 interact inside the same H0;
- blocks 0 and 40 interact across H0s but inside the same H1;
- blocks 0 and 140 reside in different H1s, and mesh node 2 computes their
  symmetric block and returns a partial to each endpoint H1.

For the last record, the generator also publishes the two frozen state blocks
to node 2. Duplicate publications to the same owner are automatically removed.

The performance model does not perform Ising arithmetic, so it cannot detect a
semantically incomplete mapping from final spin states. A mapping algorithm
must preserve every required nonzero unordered block exactly once. Compare its
scheduled H0, H1, and cross-H1 job counts against a known-good baseline before
interpreting performance.

## Generate a baseline workload

Without a custom schedule, the generator finds nonzero 32-by-32 blocks in the
sparse dataset and uses the built-in distance-balanced owner allocator:

```bash
python3 scripts/generate_hybrid_noc_workload.py \
  --dataset tb/datasets/g16384_kings.txt \
  --output logs/baseline_4x4.traffic \
  --mesh-x 4 --mesh-y 4 \
  --h0-per-h1 1 \
  --cross-engines 16 \
  --block-service-cycles 64
```

The command prints the number of H0-local, H1-local, and cross-H1 block jobs.
Save this output with experimental results so mapping coverage can be checked.

## Run a custom mapping

Suppose an algorithm produces `mappings/my_mapping.txt`:

```bash
python3 scripts/generate_hybrid_noc_workload.py \
  --dataset tb/datasets/g16384_kings.txt \
  --schedule mappings/my_mapping.txt \
  --output logs/my_mapping.traffic \
  --mesh-x 4 --mesh-y 4 \
  --h0-per-h1 1 \
  --cross-engines 16 \
  --block-service-cycles 64

make -C tb hybrid-noc-run \
  HYBRID_TRAFFIC=logs/my_mapping.traffic \
  HYBRID_STATS_PREFIX=logs/my_mapping \
  HYBRID_STATS_INTERVAL=20
```

The equivalent one-command form is:

```bash
make -C tb hybrid-noc-test \
  HYBRID_DATASET=tb/datasets/g16384_kings.txt \
  HYBRID_SCHEDULE=mappings/my_mapping.txt \
  HYBRID_MESH_X=4 HYBRID_MESH_Y=4 \
  HYBRID_H0_PER_H1=1 \
  HYBRID_CROSS_ENGINES=16 \
  HYBRID_BLOCK_SERVICE_CYCLES=64 \
  HYBRID_TRAFFIC=logs/my_mapping.traffic \
  HYBRID_STATS_PREFIX=logs/my_mapping \
  HYBRID_STATS_INTERVAL=20
```

Use a unique output prefix for every configuration. Otherwise later runs will
overwrite the earlier CSV traces.

## Compare hierarchy configurations

Several 16,384-spin configurations retain the fixed 1,024-spin H0:

| Mesh | H0s per H1 | H1 count | Spins per H1 |
|---|---:|---:|---:|
| 4 by 4 | 1 | 16 | 1,024 |
| 4 by 2 | 2 | 8 | 2,048 |
| 2 by 2 | 4 | 4 | 4,096 |

For example, run the 2-by-2 configuration with the already compiled 4-by-4
executable:

```bash
make -C tb hybrid-noc-test \
  HYBRID_DATASET=tb/datasets/g16384_kings.txt \
  HYBRID_MESH_X=2 HYBRID_MESH_Y=2 \
  HYBRID_H0_PER_H1=4 \
  HYBRID_CROSS_ENGINES=16 \
  HYBRID_BLOCK_SERVICE_CYCLES=64 \
  HYBRID_TRAFFIC=logs/config_2x2.traffic \
  HYBRID_STATS_PREFIX=logs/config_2x2 \
  HYBRID_STATS_INTERVAL=20
```

No Verilator elaboration occurs if the compiled executable is current and the
requested active mesh fits inside its maximum dimensions.

When changing the hierarchy, regenerate the mapping: H1 boundaries depend on
`h0_per_h1`, so a cross-H1 owner assignment valid for one configuration may
become H1-local or refer to different endpoint H1s in another.

## Analyze and visualize a run

The replay writes three compatible outputs:

```text
<prefix>.csv             aggregate counters
<prefix>_timeline.csv    counters per time interval
<prefix>_events.csv      accepted flits and stall transitions
```

Produce derived CSV tables with:

```bash
python3 scripts/analyze_noc_trace.py \
  --timeline logs/my_mapping_timeline.csv \
  --events logs/my_mapping_events.csv \
  --output-prefix logs/my_mapping_analysis
```

Generate plots with:

```bash
scripts/visualize_noc_trace.sh \
  logs/my_mapping logs/my_mapping_plots
```

The generated plots include:

- link utilization and backpressure over time;
- a directed mesh hotspot heatmap;
- in-flight flits and interval peak traffic;
- a matched injection-to-ejection latency histogram;
- per-resource stall episodes.

Useful comparison metrics are:

- total physical-link flit traversals, which captures path length and is a
  useful first-order NoC energy/work proxy;
- maximum and percentile directed-link utilization;
- total and longest valid-but-not-ready stall episodes;
- mean, p95, and maximum flit latency;
- peak in-flight flits;
- traffic balance across nodes and directed links;
- replay completion cycle under the same endpoint timing assumptions.

For exact-event mapping runs, also compare per-node H0/H1/cross work balance,
the flit-hop histogram, the most backpressured directed links, memory request
rejection rate, and memory-latency imbalance. These distinguish a mapping that
reduces communication from one that merely moves the critical bottleneck to a
different node or memory interface.

Finalists should also be swept across `--link-latency-cycles`,
`--link-flit-interval-cycles`, `--link-max-inflight-flits`, and
`--link-credit-return-cycles`. These respectively expose sensitivity to
per-hop delay, serialization bandwidth, the finite in-flight credit window,
and delayed backpressure. Non-default runs are physical-link projections; the
zero-additional-latency defaults retain the RTL differential baseline.

Do not compare only total injected flits. Two mappings can inject the same
number of partials while producing very different hop counts and hotspots.

## Recommended experiment procedure

For a controlled mapping study:

1. Keep the dataset, active geometry, engine count, block-service latency,
   FIFO depth, and statistics interval fixed.
2. Generate and save the built-in baseline.
3. Have each algorithm emit the same three-field mapping format.
4. Check that every algorithm schedules the same required unordered blocks.
5. Replay each mapping with a unique prefix.
6. Compare aggregate counts first, then timeline hotspots and stall episodes.
7. Repeat across several datasets and sparsity structures.
8. Confirm the most important points with the full RTL/Ramulator testbench.

For configuration sweeps, change one dimension at a time or record all
dimensions in a machine-readable manifest. At minimum, retain dataset name,
mesh dimensions, H0s per H1, engine count, service cycles, FIFO depth, mapping
algorithm and seed, simulator revision, total cycles, and wall time.

## Modeling boundaries

The hybrid replay is cycle-accurate for network behavior after a packet is
released. It uses the real FlooNoC XY routing, input buffering, ready/valid
backpressure, round-robin arbitration, and wormhole packet locking.

The default `block-service-cycles=64` is a fixed endpoint timing abstraction.
The replay does not currently run Ramulator for each scheduled block, perform
MVM arithmetic, verify final spins, model autonomous scheduler RTL, or infer
internal FIFO occupancy that is not exposed at the router boundary. Therefore:

- use it directly for comparative mapping, path, contention, and sensitivity
  studies under controlled release assumptions;
- calibrate service latency or supply measured release schedules before making
  absolute execution-time claims;
- validate selected configurations with the full RTL/Ramulator path before
  presenting them as final hardware performance results.

The 16,384-spin reference replay has already been checked against the full RTL
trace for externally visible traffic volume and routing: both produced 1,336
injected/ejected flits and 2,200 physical-link flit traversals. Congestion and
stall timing still depend on the endpoint release model.
