# Architectural comparison dimensions

This comparison separates an Ising solver's numerical method, storage/dataflow,
and implementation evidence. It is a qualitative comparison, not a normalized
cross-paper performance ranking.

| Work | Organization | Evidence | Relevant comparison with Azilla |
|---|---|---|---|
| [Aadit et al., Nature Electronics 2022](https://www.nature.com/articles/s41928-022-00774-2) | Sparse probabilistic Ising processing | Sparse-machine implementation and probabilistic-computing results | Sparsity and parallel spin processing are established approaches; Azilla's distinct question is hierarchical placement of symmetric block work. |
| [Yue et al., Nature Electronics 2024](https://www.nature.com/articles/s41928-024-01228-7) | Interaction-centric compressed sparse storage and compute-in-memory, arbitrary graph topology | 40-nm RRAM implementation; MaxCut and graph-coloring applications | Compare occupied-block storage overhead and locality, not merely support for sparse/arbitrary graphs. |
| [Kashimata et al., IEEE Access 2024](https://arxiv.org/abs/2311.17370) | Full-connectivity multi-chip streaming; overlap computation and inter-chip communication | Eight-FPGA experiments and a larger theoretical-model projection | Strong-scaling and communication-limit analysis are useful comparators. Azilla's fixed-package scaling experiment is weak scaling; the terms are not interchangeable. |
| [Fischer et al., TVLSI 2025](https://arxiv.org/abs/2409.17606) | Wide-link NoC with end-to-end parallel streams and separate short-message links | 12-nm physical implementation | Supports choice of detailed network metrics and implementation infrastructure; its physical results do not characterize Azilla's package links. |

## Required matched views

- Primary latency and initialization-inclusive latency: same graph, geometry,
  mapping, memory, iteration count and corrected shared-fetch baseline.
- Communication explanation: paired directed-link accepted traffic/stalls,
  hierarchy work placement, and memory latency/rejections. Analytical payload
  counts are supplementary and exclude state/control and hop replication.
- Quality: frozen training selection, independent graph and solver seeds,
  best and final cuts, and comparison targets named explicitly. Local-search
  ratios do not measure distance to a known optimum.
- Cost: separate endpoint/compute-node logic, streamers, replay payload,
  state/weight storage and physical interfaces. Published implementations and
  component-level estimates are different evidence categories.

## Source-review scope

Publisher abstracts and arXiv bibliographic/abstract pages checked September 8,
2026. These rows intentionally avoid detailed experimental claims not confirmed
in that review. Full-text closest-work review, broader recent sparse/in-network
accelerator coverage and final coauthor novelty assessment remain to be finished.
