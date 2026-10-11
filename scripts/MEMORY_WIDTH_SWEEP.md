# Projected memory-width sensitivity

This workflow retains the existing projected GDDR6-derived 32-Gb/s timing
table. It does not claim vendor GDDR7 timing. Selecting 2, 4, or 8 identical
x16 controllers produces x32/x64/x128 endpoints (128/256/512 GB/s raw).
Channel count also scales aggregate banks, queue capacity, and storage capacity;
this is provisioning sensitivity, not a pure link-serialization experiment.
The accelerator bridge remains 16 request lanes and 40 DRAM ticks per cycle.

Prepare unique generated configurations without changing active YAML files:

```sh
python3 scripts/run_memory_width_sweep.py prepare --output results/width_configs_NEW
python3 scripts/check_memory_width_configs.py \
  --configs-manifest results/width_configs_NEW/configs.json \
  --output results/width_configs_NEW/memory_smoke.json
```

The smoke tests 1,024 requests per width and verifies unique completions and
draining. It is not accelerator validation or a bandwidth characterization.
Each width and both execution modes additionally require integrated VCS timing,
backpressure, and exact-event equivalence certificates accepted by
`check_paper_validation_gate.py`. Old component PASS files are insufficient.

The execution plan is JSON with `configs_manifest`, `ramulator_library`,
`ramulator_library_sha256`, `certificates`, and `workloads`. Paths resolve from
the repository root; run the command there. `certificates` maps width strings
`"32"`, `"64"`, `"128"` to objects containing certificate paths under keys
`"cir"` and `"cores-only"`. Each workload contains:

```json
{
  "name": "smoke",
  "input_sha256": {"tb/datasets/g256_smoke.txt": "ACTUAL_SHA256"},
  "arguments": ["--dataset", "tb/datasets/g256_smoke.txt",
    "--mesh-x", "2", "--mesh-y", "2", "--h0-per-h1", "1",
    "--cores-per-h0", "2", "--h0-mvms", "1", "--h1-mvms", "1",
    "--cross-mvms", "4", "--idle-refresh-period-ticks", "7600"]
}
```

Supply `--schedule` and its hash when evaluating a fixed mapped workload;
the dataset must use the same vertex permutation. The runner applies exactly
the same workload arguments at every width and in both modes. It validates all
six certificates before starting and rechecks the current certificate before
each run. Source/library changes close the gate. Run serially with:

```sh
python3 scripts/run_memory_width_sweep.py run --plan results/width_plan.json \
  --output results/width_runs_NEW
```

Each point gets its own metric prefix, log, command record and `/usr/bin/time -v`
output. Existing output directories are refused. Failures stop the sweep.
Successful exits are marked `completed-unreviewed`, not paper-ready; conservation,
workload completeness and output plausibility still need review. No calibration,
hybrid, Verilator, synthesis, or large performance job is launched by preparation.
