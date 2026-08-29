# Mapping-test quick guide

Run commands from the repository root with `PYTHONPATH=model`. A mapping test
has four stages: create an artifact, permute the dataset, validate the result,
and simulate it. Always compare mappings using the same graph, geometry,
engine counts, memory configuration, and random seed.

## 1. Generate a mapping

For the built-in sparse mapper and the 1,048,576-spin 256-8-16 geometry:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli map-graph \
  --dataset tb/datasets/g1048576_kings.txt \
  --output-dir mappings/kings_builtin \
  --write-permuted-dataset mappings/kings_builtin/dataset.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 8 --cores-per-h0 256 \
  --device cuda --require-cuda --seed 1
```

The artifact contains the vertex permutation, occupied 32x32 block pairs,
cross-H1 owners, and `schedule.txt`. The emitted `dataset.txt` has the same
permutation applied to its weights. Never combine a mapped artifact with the
original, unpermuted dataset.

## 2. Validate it

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli validate-schedule \
  --dataset mappings/kings_builtin/dataset.txt \
  --schedule mappings/kings_builtin/schedule.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 8 --cores-per-h0 256
```

This rejects missing or duplicate blocks, invalid owners, and geometry
mismatches.

## 3. Screen mappings quickly

Use the fixed-profile model to rank many candidates in seconds:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.cli simulate-mapped-events \
  --artifact mappings/kings_builtin \
  --h0-mvms 16 --h1-mvms 16 --cross-mvms 16
```

This is a calibrated extrapolation. Use it for screening, not final absolute
cycle claims.

## 4. Measure finalists with exact timing

Build the Ramulator bridge once, then run each selected mapping:

```bash
make -C tb cycle-model-ramulator-library

PYTHONPATH=model python3 -m azilla_cycle_model.cli \
  simulate-mapped-exact-events \
  --artifact mappings/kings_builtin \
  --dataset mappings/kings_builtin/dataset.txt \
  --h0-mvms 16 --h1-mvms 16 --cross-mvms 16 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --idle-refresh-period-ticks 7600 \
  | tee mappings/kings_builtin/exact-result.txt
```

Compare `iteration_cycles`, injected/ejected/link flits, and injection,
ejection, and link stalls. Also report mapper runtime and peak memory. The
million-spin exact timing run currently takes about 10.7 minutes per mapping
on one CPU core.

## Using another mapping algorithm

Adapt its output through the stable Python interface:

```python
from azilla_cycle_model.mapping_adapter import (
    artifact_from_mapping, write_permuted_dataset,
)

artifact = artifact_from_mapping(
    geometry,
    permutation,          # permutation[new_vertex] = old_vertex
    inverse_permutation,  # inverse_permutation[old_vertex] = new_vertex
    occupied_coords,      # occupied block pairs after permutation
    metadata={"algorithm": "my_mapper", "seed": seed},
)
artifact.save("mappings/my_mapper")
write_permuted_dataset(
    source_dataset, "mappings/my_mapper/dataset.txt", artifact
)
```

`artifact_from_mapping` checks the permutation and assigns legal cross-H1
owners. A custom `owner_assigner=` can be supplied when testing scheduling as
well as placement.

For a fair experiment, include identity and random placements alongside each
proposed mapper, repeat stochastic mappers across seeds, and run the exact
model on the most promising candidates. Small representative cases should
also be checked with the arithmetic-enabled model when functional equivalence
is part of the experiment.
