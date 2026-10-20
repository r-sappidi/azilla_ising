# Documentation index

## Start here

- [Repository setup](../README.md): dependencies and entry points.
- [Model usage](../model/README.md): executable models and result schemas.
- [RTL usage](../tb/README.md): testbenches, VCS and differential checks.
- [Script index](../scripts/README.md): validation, campaigns and analysis tools.
- [Repository maintenance](repository_maintenance.md): layout and safe cleanup.

## Current architecture and comparisons

- [Current architecture](current_architecture.md): implementation-level
  summary of the H0-anchored, accumulate-at-owner CIR design, its flat
  state-exchange baseline, widths, provisioning, clock ratio and what is
  RTL-verified versus model-only.
- [Shared-fetch baseline](shared_fetch_baseline.md): buffered v3 endpoint
  comparison, with canonical single-fetch memory ownership.
- [Streaming endpoints](streaming_endpoints.md): opt-in RTL architecture and
  matching model, correctness checks and coverage limits.
- [Anchored CIR RTL](anchored_rtl.md): swap-in RTL for CIR with H0-anchored
  symmetric ownership and accumulate-at-owner uplinks, its testbench and the
  small-geometry VCS/C++ differential gate.
- [Mapping experiments](../MAPPING_EXPERIMENTS.md): permutation and artifact contract.
- [Benchmark methodology](../BENCHMARK_CAMPAIGN.md): dataset preparation and metrics;
  calibrated-model instructions are historical, not the primary evaluation path.
- [Architecture overview](paper_architecture_overview.md) and
  [architecture details](paper_architecture.md): manuscript-oriented descriptions;
  verify version-specific statements against the implementation.

## Models and methodology

- [C++ exact-event model](cpp_exact_model.md): compiled port of the
  timing-only exact-event model for CIR and streaming/buffered endpoints,
  byte-identical to the Python oracle and checked against stored VCS traces;
  also the only implementation of the flat state-exchange baseline and the
  H0-anchored mode, with the clock-ratio, front-end and memory options used
  by the 1 GHz / HBM4 campaigns.
- [Dense analytical model](dense_analytical_model.md): bounds/projections and checks.
- [Fast H0 adapter](fast_h0_adapter.md) and
  [fast shared-fetch model](fast_shared_fetch.md): opt-in optimizations;
  equivalence does not extend RTL validation coverage.
- [Prior-work methodology](prior_work_evaluation_methodology.md) and
  [comparison dimensions](paper_comparison_dimensions.md): evaluation planning.
- [Synthesis](../SYNTHESIS.md): separate feasibility workflow, not timing-model proof.

## Historical contracts

[Original CIR baseline](CIR_BASELINE.md) and
[directed-fetch comparison](comparison_contract.md) describe earlier endpoint
organizations. Keep them for provenance, not as defaults for new comparisons.
Never merge directed-fetch, buffered v3 and streaming measurements into one
unversioned "cores-only" series.

## Private evidence

Local `AGENTS.md` is the concise operating guide. `LOCAL_RESEARCH_STATUS.md`
is the dated research ledger; its older entries are not live status. Detailed
snapshots, commands, audits and results remain in ignored `results/`. They are
not required files in a clean checkout and must not be committed accidentally.
