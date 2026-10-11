# Azilla compression and mapping library

This directory is a transferable bundle of the graph-to-NoC mapping code used
by the Azilla mapping experiments. It can be copied independently of the main
repository.

## What is included

- `graph_mapping/graph_compression/tile_compressor.py`: balanced graph
  partitioning, within-partition permutation, occupied-block extraction, and
  lossy low-weight block pruning.
- `graph_mapping/graph_compression/tile_scheduler.py`: generic tile-to-square-
  mesh scheduling with endpoint reuse and locality heuristics.
- `azilla_integration/pipeline.py`: the recommended end-to-end API and Azilla
  schedule writer.
- `examples/compile_example.py`: runnable 4K-spin example.
- `examples/run_mapping_stage_experiments.py`: full staged timing experiment.
- `examples/run_rtl_mapping_sweep.py` and
  `examples/analyze_rtl_mapping_sweep.py`: repository-integrated RTL replay and
  traffic analysis.
- `reference/azilla_cycle_model_mapping_adapter.py`: the adapter used inside
  the full Azilla repository. It is reference code and expects the rest of the
  `azilla_cycle_model` package.

## Install and test

Python 3.10 or newer is required. CUDA is optional; the same API works on CPU.

```bash
cd compression_mapping_handoff
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -p 'test_*.py'
python examples/compile_example.py
```

PyTorch installation can be platform-specific. If `pip install -e .` does not
select the desired CUDA build, install PyTorch first using its platform
selector, then repeat the editable install.

## Input contract

The primary API consumes three one-dimensional PyTorch tensors with one entry
per Ising edge:

- `src[k]`: first zero-based spin index.
- `dst[k]`: second zero-based spin index.
- `weight[k]`: signed Ising coupling.

Keep all three tensors on the same device. Duplicate edges are accepted. The
pipeline normalizes placement scores using coupling magnitude. For an Azilla
configuration, the spin count must satisfy:

```text
num_vertices = mesh_x * mesh_y * blocks_per_node * tile_size
```

Azilla uses `tile_size=32`. `blocks_per_node` is the number of 32-spin blocks
owned by one H1 node. For example, 16 nodes with 8 blocks each represent 4,096
spins.

## Recommended API

```python
import torch
from azilla_integration import CompileConfig, compile_mapping, write_schedule

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
src = src.to(device)
dst = dst.to(device)
weight = weight.to(device)

config = CompileConfig(
    mesh_x=4,
    mesh_y=4,
    tile_size=32,
    blocks_per_node=8,
    lossy_weight_budget=0.01,
    seed=1,
)
mapping = compile_mapping(src, dst, weight, 4096, config)
write_schedule("mapping.schedule", mapping)
```

`mapping.permutation[new_spin]` gives the original spin index placed at the new
index. `mapping.inverse_permutation[old_spin]` gives its new index. Apply the
permutation consistently to spin state, fields, couplings, and decoded output.

`mapping.block_pairs` contains the retained upper-triangular off-diagonal
32-by-32 blocks. `mapping.owners` aligns one-to-one with those pairs. Local
blocks use owner `-1`; cross-H1 blocks contain the selected node ID.

## Compiler stages

The façade runs these cumulative stages:

1. Partition vertices equally across NoC nodes while increasing coupling weight
   internal to each node.
2. Permute vertices within partitions to cluster interactions into fewer
   occupied 32-by-32 blocks.
3. Remove the lightest off-diagonal blocks without exceeding
   `lossy_weight_budget` of total absolute coupling magnitude.
4. Compare endpoint-local and balanced cross-node ownership as complete
   schedules and retain the lower-cost result. The default objective is NoC
   link traversal count, and ties retain endpoint-local ownership.

For cycle-model-driven selection, pass an `owner_objective` callback to
`compile_mapping`. It receives `(block_pairs, owners, config)` and must return a
scalar where lower is better. Running the complete candidate through the Azilla
event model reproduces the no-regression scheduled curves in this repository.

## Integrating with the Azilla repository

From the main repository, either install this directory with `pip install -e`
or place it on `PYTHONPATH`. Produce a schedule and then generate the RTL replay
traffic:

```bash
python scripts/generate_hybrid_noc_workload.py \
  --dataset graph.txt \
  --schedule mapping.schedule \
  --output mapping.traffic \
  --mesh-x 4 --mesh-y 4
```

The schedule file format is:

```text
# block_a block_b owner
0 17 0
2 18 1
```

Before solving, reorder the dataset using `mapping.inverse_permutation`. After
solving, convert new-index spin results back to original numbering using
`mapping.permutation`.

The experiment scripts bundled under `examples/` assume they are run from the
full Azilla repository because they import its event model and invoke its RTL
testbench. The compression library and `compile_example.py` are standalone.

## Important accuracy note

Lossy compression deliberately changes the Ising problem. A 1% discarded
absolute-weight budget is an experiment setting, not an accuracy guarantee.
Measure Max-Cut or application-quality loss on representative graphs before
choosing a production budget. Set `lossy_weight_budget=0.0` for lossless block
elimination only.
