# Ising benchmark and traffic-analysis campaign

## Canonical fixed-H1 paper matrix

The earlier campaign below remains useful for workload discovery and case
studies, but it changes geometry with capacity and is not the primary scaling
matrix.  Main-paper weak scaling uses a fixed H1 tile: 8 H0s/H1, 256
cores/H0, 32 spins/core, and 4/2/4 H0/H1/cross MVMs.  Only the H1 mesh grows:
1x1, 2x1, 2x2, 4x2, and 4x4 for 64K through 1M spins.

Generate the canonical datasets and run the resumable identity-placement
screen with:

```bash
python3 scripts/prepare_canonical_sparse_campaign.py
WORKERS=1 scripts/run_canonical_sparse_screen.sh
```

The canonical manifest contains a deterministic degree-4 torus plus three
seeds each of uniform and community-local degree-16 graphs at every size.  A
degree sensitivity at 128K adds uniform degrees 4, 8, 32, and 64.  Results use
unique paths under `results/paper_canonical/`; generated data remain ignored by
Git.  Mapping, geometry, execution-mode, exact-event, and RTL confirmations
must consume this same manifest rather than independently selecting datasets.

This workflow produces reproducible performance, NoC traffic, mapping, and
interconnect-sensitivity data for Azilla. Generated datasets and results live
under `results/paper_benchmarks/`, which is intentionally ignored by Git. The
scripts, schemas, and methodology are tracked.

## Benchmark rationale and provenance

The primary suite is sparse because Azilla is evaluated as a scalable sparse
Ising-iteration accelerator.

- **Gset** is the standard public Max-Cut collection used by many Ising-machine
  papers. This campaign fetches exact G14, G19, G21, G22, G31, G32, G35, G37,
  G47, G70, and G81 files from Stanford. The selection includes random,
  toroidal, planar, and larger sparse cases. The 2026 natively sparse FPGA qSB
  paper uses Gset instances spanning 800–4,000 vertices and specifically
  discusses G19, G21, G31, and G47; it also highlights G14, G35, and G37 in
  performance comparisons.
- **A1–A3 scale-matched proxies** reproduce only the published problem sizes
  and symmetric nonzero counts from that sparse-FPGA paper: 10,000/524,000,
  16,000/786,000, and 20,000/1,048,000. The original graph instances are not
  public, so these are explicitly labeled random proxies and must never be
  described as the authors' A1–A3 datasets.
- **Random average-degree-25 graphs** provide a scalable unstructured sparse
  family. A 2026 GPU OIM study reports regular degree-25 Max-Cut graphs through
  20 million spins. Our graphs have a fixed edge count and average degree 25,
  but are not regular; this distinction is recorded in the manifest.
- **Degree-4 toroidal graphs** provide a scalable structured/local family
  analogous to the toroidal Gset cases. They test whether mapping preserves
  locality and whether adding hierarchy resources maintains iteration rate.
- Dense Rudy K(N) instances used by the 100,000-spin coherent Ising machine and
  full-connectivity multi-chip Ising-machine work are intentionally not part of the main
  scaling sweep. They are relevant dense stress cases, not evidence for the
  sparse architecture's target claim.
- Other sparse Ising-machine papers evaluate integer factoring, 3SAT, graph
  coloring, and EDA layout decomposition. Those are important application
  studies, but using them here would require auditable problem-to-Ising
  frontends and algorithmic quality evaluation. They are recorded as future
  functional workloads rather than being replaced with synthetic lookalikes.

Primary sources:

- Gset files: <https://web.stanford.edu/~yyye/yyye/Gset/>
- Natively sparse FPGA qSB: <https://doi.org/10.1038/s41467-026-75119-0>
- Degree-25 large sparse OIM: <https://doi.org/10.1038/s41598-026-66268-9>
- 100,000-spin dense CIM: <https://doi.org/10.1126/sciadv.abe7953>
- Streaming multi-chip full-connectivity Ising machine:
  <https://arxiv.org/abs/2311.17370>
- Interaction-centric universal sparse Ising machine:
  <https://doi.org/10.1038/s41928-024-01228-7>
- Sparse probabilistic-bit Ising machine (factoring and 3SAT):
  <https://doi.org/10.1038/s41928-022-00774-2>

## Reproduce the datasets

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 scripts/prepare_ising_benchmarks.py \
  --output-dir results/paper_benchmarks/datasets \
  --include-million
```

`manifest.json` and `manifest.csv` record the source URL, generator seed,
problem vertices, padded hardware capacity, isolated padding, edge count,
density, geometry, notes, and SHA-256 digest. Gset's second header field is its
edge count; converted Azilla files use `known_cut=0` rather than misrepresenting
that value as a known optimum. Every undirected edge is written in both
directions so a dataset is valid for RTL arithmetic as well as timing-only
analysis.

## Broad calibrated-event sweep

```bash
export PYTHONPATH=$PWD/model

python3 scripts/run_ising_benchmark_sweep.py \
  --manifest results/paper_benchmarks/datasets/manifest.json \
  --output-dir results/paper_benchmarks/event_sweep \
  --workers 4

MPLBACKEND=Agg python3 scripts/plot_ising_traffic_heatmaps.py \
  --sweep-dir results/paper_benchmarks/event_sweep

python3 scripts/analyze_traffic_patterns.py
```

The traffic analysis emits both per-instance classifications and a family-level
summary. Log-log scaling exponents are calculated only for the homogeneous
degree-25 and toroidal generator families; heterogeneous Gset instances and
the three scale-matched proxies are deliberately not fit as a scaling series.

Each point retains:

- initialization, iteration, total, active-network, and skipped cycles;
- H0, H1-local, and cross-H1 block jobs and per-resource service estimates;
- injected/ejected flits, physical link traversals, packet-type counts, hop
  histogram, injection stalls, link stalls, and estimated transferred bytes;
- every directed link's offered cycles, accepted flits, packet count,
  utilization, acceptance fraction, and backpressure;
- per-H1 job and state-publication load;
- graph edge density, occupied-block density, edges per occupied block, cross
  fraction, link/node imbalance, busiest link, mapper/configuration provenance,
  hashes, Git state, host, Python version, and simulator wall time.

The generated four-panel heatmaps show directed-link volume, utilization over
the iteration window, stalled cycles, and H1-package job load. PNG is convenient
for inspection; PDF is retained for paper figures.

## Mapping and scheduling comparisons

```bash
python3 scripts/run_mapping_comparison.py \
  --manifest results/paper_benchmarks/datasets/manifest.json \
  --names gset_g14,gset_g22,gset_g32,gset_g35,gset_g70,gset_g81,random_d25_n16384,toroidal_d4_n16384 \
  --output-dir results/paper_benchmarks/mapping_comparison

python3 scripts/run_owner_policy_comparison.py \
  --artifact-root results/paper_benchmarks/mapping_comparison \
  --output-dir results/paper_benchmarks/owner_policy_comparison
```

The first command compares identity placement, a seeded random placement, and
the bundled hierarchical mapper under a common per-block owner policy. It also
writes a correctly permuted full dataset beside every artifact for arithmetic
or Ramulator follow-up. The second command holds each vertex placement fixed
and compares the optimized per-block owner assignment with the RTL's default
H1-pair owner policy. This separation is necessary: placement and cross-job
scheduling can improve or harm traffic independently.

Confirm mapping finalists with the exact/Ramulator model (G70 is the campaign's
representative point):

```bash
for campaign_variant in identity_optimized_owner provided_mapper random; do
  campaign_artifact="results/paper_benchmarks/mapping_comparison/gset_g70/$campaign_variant"
  campaign_output="results/paper_benchmarks/exact_mapping_confirmation/gset_g70/$campaign_variant"
  mkdir -p "$campaign_output"
  python3 -m azilla_cycle_model.cli simulate-mapped-exact-events \
    --artifact "$campaign_artifact" --dataset "$campaign_artifact/dataset.txt" \
    --h0-mvms 1 --h1-mvms 1 --cross-mvms 4 \
    --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
    --ramulator-config tb/ramulator_128x32.yaml \
    --metrics-prefix "$campaign_output/metrics"
done
python3 scripts/analyze_exact_mapping.py
```

## High-fidelity timing confirmation

Build the shared Ramulator backend, then run selected points with
`simulate-exact-events` and a unique `--metrics-prefix`:

```bash
make -C tb cycle-model-ramulator-library

# Strict timing/transfer differential against saved 256-spin and 16K RTL traces.
PYTHONPATH=model python3 scripts/check_exact_event_model.py

python3 -m azilla_cycle_model.cli simulate-exact-events \
  --dataset results/paper_benchmarks/datasets/prepared/gset_g70.txt \
  --mesh-x 4 --mesh-y 4 --h0-per-h1 2 --cores-per-h0 16 \
  --h0-mvms 1 --h1-mvms 1 --cross-mvms 4 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --metrics-prefix results/paper_benchmarks/exact_confirmation/gset_g70/identity

python3 scripts/analyze_exact_calibration.py
python3 scripts/analyze_dram_patterns.py
```

The exact-event mode executes the timing-visible hierarchy, streamers,
Ramulator requests, arbitration, backpressure, and differentially checked NoC
one active cycle at a time. For unverified geometries its honest accuracy label
is `cycle-structured-unverified`; do not rename it. The broad model uses
`calibrated-extrapolation`: its packet routes and traffic volume are exact for
the compiled schedule, but its endpoint block completion times use RTL-derived
constants. Headline timing should therefore come from exact/Ramulator points,
with the calibration-error table shown for the broader sweep.

The DRAM analysis aggregates accepted/completed/rejected requests, weighted
and maximum latency, and request-load CV/Gini overall and separately for H0,
H1-local, and cross-H1 memory tiles.

The strict check requires the saved RTL event traces in `build/`; regenerate
them with the RTL differential workflow before claiming a new RTL revision is
covered. It compares initialization/iteration timing, all six aggregate NoC
counters, and every accepted injection, hop, and ejection cycle and packet.

## Interconnect sensitivity and summary figures

```bash
python3 scripts/run_interconnect_sensitivity.py \
  --manifest results/paper_benchmarks/datasets/manifest.json \
  --names gset_g70,toroidal_d4_n16384 \
  --workers 4

MPLBACKEND=Agg python3 scripts/plot_paper_benchmark_summary.py
```

The sensitivity sweep changes one-way hop latency, flit serialization interval,
finite credit capacity/return delay, package-local chiplet latency, and a
combined stress point. These are projections, not measurements of a named UCIe
or rack implementation. Record the assumed accelerator clock before converting
cycles to seconds, bandwidth, or throughput.

## Interpretation rules

1. Report problem vertices and padded hardware spin capacity separately.
2. Report edge sparsity and occupied 32×32 block sparsity separately. Azilla
   schedules blocks, so equal-degree graphs can have very different work.
3. Never combine calibrated timing and exact/Ramulator timing in one curve
   without distinct markers and an explicit error table.
4. Mapping comparisons must use the same geometry, engine counts, dataset seed,
   and owner policy; scheduling comparisons must hold the vertex permutation
   fixed.
5. Traffic heatmaps are directed. Opposite directions share a physical channel
   location visually but have independent counters in the current model.
6. A performance iteration is not time-to-solution. Algorithmic convergence,
   solution quality, restart count, and energy are separate results that this
   timing campaign does not provide.
7. The homogeneous-family scaling fits co-scale hardware capacity with problem
   size. They characterize the proposed scale-out configurations, not the
   asymptotic runtime of a fixed hardware instance.
