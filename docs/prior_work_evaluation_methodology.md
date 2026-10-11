# Prior-work evaluation methodology for Azilla

This note extracts evaluation practices from primary papers most relevant to a
hierarchical, sparse Ising accelerator. It is a methodology guide, not a
related-work claim. Facts below are limited to what the cited papers report;
an em dash means that the paper does not make that item a central reported
metric.

## Methodology matrix

| Work | Evaluation fidelity | Workloads and scale | Baselines and ablations | Traffic/latency methodology | Area/energy methodology | Repetition and validation practice |
|---|---|---|---|---|---|---|
| [Tatsumura et al., multi-chip Ising machine (Nature Electronics 2021)](https://doi.org/10.1038/s41928-021-00546-4) | Measured multi-FPGA implementation | Dense MaxCut through 16,384 vertices on eight FPGAs | Optimized simulated annealing and prior Ising-machine implementations | Reports step time, computational throughput, synchronization/stall behavior, and scaling with FPGA count | Board-level power/energy comparisons | Solution quality is compared at a specified runtime/step budget; the multi-chip behavior is measured rather than inferred |
| [Kashimata et al., streaming multi-chip architecture](https://arxiv.org/abs/2311.17370) | Measured 1--8 FPGA clusters plus an analytical projection to 79 FPGAs | Dense MaxCut, 1,024--32,768 measured; 100,000 projected | Prior non-overlapped multi-chip architecture; CIM and software annealing for time-to-target | Strong-scaling curves, compute/communication overlap, communication-latency limit, pipeline efficiency, throughput, and time-to-target | FPGA resource tables; projection is explicitly separated from measurement | Model is checked against an 8-chip measurement: 34,418 modeled versus 33,787 measured GMAC/s; the 79-chip point is clearly labeled projected |
| [Tatsumura et al., FPGA-based Ising machine (FPL 2019)](https://doi.org/10.1109/FPL.2019.00019) | Implemented Arria-10 FPGA, with measured FPGA/GPU execution | Dense 2,048- and 4,096-spin problems; all-to-all MaxCut | GPU implementation, coherent Ising machine, optimized simulated annealing | Time per solve/step and MVM arithmetic throughput | FPGA resources and measured/platform power used for energy-efficiency comparison | Compares solution quality as well as speed; fixed problem and solver settings are needed for cross-platform timing |
| [Yu et al., natively sparse FPGA Ising machine (Nature Communications 2026)](https://doi.org/10.1038/s41467-026-75119-0) | FPGA implementation plus algorithm simulation | Gset 800--4,000; generated sparse 10K/16K/20K sets with matched nonzero counts; additional weighted/QUBO tests | Sparse FPGA and dense FPGA designs; Float32 versus Int8; tiled format, load-balancing, and hazard-handling ablations | Time-to-target at 99% of best known, time per step, PE efficiency, preprocessing overhead, and memory-footprint sensitivity; identifies DRAM bandwidth as the bottleneck | FPGA resource use and storage reduction; no network-centric link study | Ten independent runs per Gset instance, 1,000 steps each; reports mean accuracy and variance; tests broader weights and QUBO families; controlled ablations keep step count identical |
| [Wang et al., interaction-centric universal Ising machine (Nature Electronics 2024)](https://doi.org/10.1038/s41928-024-01228-7) | Fabricated 40-nm RRAM compute-in-memory prototype plus FPGA implementation/artifact | MaxCut, graph coloring, and layout decomposition with arbitrary/sparse graph topology | Spin-centric organization, GPU, and CPU/ILP baselines depending on workload | End-to-end solve time and storage scaling rather than detailed NoC latency | Measured hardware energy and speedup; implementation source and source data are released | Demonstrates multiple problem classes and separates experimental hardware from algorithmic comparisons |
| [Ahn et al., Tesseract (ISCA 2015)](https://www.istc-cc.cmu.edu/publications/papers/2015/p105-ahn.pdf) | Cycle-level system simulation using processor, memory, and interconnect models | Five graph workloads; three principal real graphs with 4.2--7.4M vertices and 79--194M edges; limited checks up to 1.15B edges | DDR3 out-of-order CPU, HMC out-of-order CPU, many-core HMC, and incremental prefetcher variants | Execution time and scaling; inter-partition messages and prefetch effects are evaluated in workload context | McPAT/CACTI-style modeled system energy and area components; average energy reduction reported | Uses multiple graph shapes, geometric means, incremental mechanism ablations, and a limited larger-graph sensitivity check |
| [Song et al., GraphR (HPCA 2018)](https://arxiv.org/abs/1708.06248) | Architecture simulation with NVSim-derived ReRAM latency/energy; CPU/GPU execution measurements | PageRank, BFS, SSSP, SpMV, and collaborative filtering over six real graphs | CPU, GPU, and prior PIM architecture | Runtime and sensitivity to graph density/sparsity; emphasizes data movement but does not provide packet-level NoC tails | NVSim and published ReRAM device measurements; CPU energy from platform specification and GPU energy from `nvidia-smi` | Reports per-workload values and geometric means across 25 executions; density sensitivity guards against a single sparsity point |
| [NetSparse (MICRO 2025)](https://doi.org/10.1145/3725843.3756076) | Simulation of a 128-node cluster with sparse accelerators and in-network sparse operations | SparseSuite matrices and distributed sparse kernels; payload-size sensitivity | End-host sparse execution and alternative communication approaches | End-to-end kernel time, network traffic/goodput, packet-header overhead, and scale; explicitly studies small 4 B--1 KB sparse payloads | Modeled network/accelerator cost | Uses application-derived traffic rather than synthetic traffic alone; evaluates scaling and payload sensitivity |
| [Jiang et al., BookSim2 (ISPASS 2013)](https://crd.lbl.gov/assets/pubs_presos/booksimispass.pdf) | Detailed cycle-accurate NoC simulator validated against RTL | 3x3 mesh validation; uniform-random four-flit packets over offered-load sweeps; 100K measured cycles per point | Same topology/router configuration in BookSim2 and RTL; router-pipeline and arbitration variants | Offered versus accepted throughput, in-network latency, zero-load latency, saturation throughput, and latency-throughput curves; source queueing is explicitly excluded from “network latency” | Not a primary PPA paper | RTL and simulator agree within about 5% in reported network latency; configurations and measurement interval are fixed and disclosed |
| [Fischer et al., FlooNoC (TVLSI 2025)](https://arxiv.org/abs/2409.17606) | Complete post-layout 12-nm end-to-end implementation | 8x4 mesh, 288 RISC-V cores; DMA bulk traffic and latency-sensitive short messages | AXI4 multi-layer interconnect and prior NoCs | Per-link and aggregate bandwidth, endpoint transaction behavior, and physical byte-hops | Post-layout frequency, area overhead, power, and energy per byte per hop at a stated voltage; 645 Gb/s/link and 0.15 pJ/B/hop reported | Physical implementation establishes feasibility; workload and physical-network claims are not inferred solely from a high-level simulator |

## Practices that recur across the strongest papers

1. **Separate fidelity tiers.** Measured RTL/hardware, validated detailed models,
   and projections are labeled separately. Projected scale is credible only after
   matching the model to a smaller implemented system.
2. **Use both application traffic and controlled stress traffic.** Application
   traces establish relevance; offered-load sweeps reveal zero-load latency,
   saturation, and the onset of backpressure.
3. **Hold the comparison contract fixed.** Algorithm, target quality, iteration
   count, graph, precision, memory/link assumptions, and capacity must match.
   Incremental ablations isolate individual mechanisms.
4. **Report distributions, not only averages.** Ising quality needs multiple
   seeds and variance/success probability. Network performance needs median and
   tail latency, hotspot utilization, and imbalance in addition to totals.
5. **Pair speed with cost.** Strong work reports resource or area cost and energy,
   and normalizes communication energy as bytes or flits per hop. Azilla should
   defer energy claims until synthesis/physical assumptions are available, but
   should collect the activity counts now.
6. **Exercise diverse structure.** Graph size alone is insufficient. Prior graph
   accelerators vary topology, sparsity/density, degree distribution, and workload;
   sparse Ising work additionally tests weight distributions and problem classes.

## Required Azilla methodology

### CIR ablation

Compare CIR and destination-stationary cores-only execution using the identical
permutation, occupied unordered block set, coefficients, initial state, noise
stream, mesh, memory endpoints, link parameters, and iteration count. Report two
cost views: literal implementations and compute/area-normalized results once
synthesis is available. At minimum collect:

- iteration cycles, wall time, and speedup;
- unordered blocks versus directed evaluations;
- coupling capacity and weight bytes read;
- state, partial, and control packets/bytes;
- injected bytes, physical byte-hops, and hop-count distribution;
- source wait, in-network latency, destination wait, and end-to-end latency;
- per-link average/peak utilization, stalled cycles, longest stall episode, and
  average/maximum queue occupancy;
- per-H0/H1 job count, completion spread, DRAM requests, rejection rate, and
  memory-latency imbalance.

Plot CIR speedup and traffic reduction against the fraction of non-H0-local jobs.
This makes the claimed mechanism visible instead of reducing the evaluation to a
single dense-workload speedup.

### Traffic experiment protocol

For each reported configuration, use a warm-up phase followed by a declared
measurement interval or, for finite Ising iterations, timestamp every packet from
creation through ejection. Define latency terms explicitly:

```text
source_wait = injection_cycle - creation_cycle
network_latency = ejection_cycle - injection_cycle
destination_wait = consume_cycle - ejection_cycle
end_to_end_latency = consume_cycle - creation_cycle
byte_hops = payload_bytes * physical_links_traversed
offered_load = flits_offered / (active_cycles * directed_injection_ports)
accepted_load = flits_ejected / (active_cycles * directed_ejection_ports)
```

For synthetic validation, sweep uniform-random and adversarial hotspot/transpose
traffic from low load through saturation on a small RTL mesh and the corresponding
Python/hybrid model. For workload evaluation, replay actual mapped sparse and dense
schedules. Report CDFs for packet latency and link utilization, time-series burst
plots, directed-link heatmaps, H0/H1 endpoint heatmaps, and traffic composition by
packet type.

### Validation and statistics

- Check packet conservation by type: created = consumed, with no duplicate packet
  identifier; injected physical flits must equal the sum of link traversals.
- On a small geometry, match RTL and model counts exactly and report relative error
  for latency/cycle metrics. Do not promote this to unqualified validation of a
  larger geometry.
- For deterministic performance runs, repeat at least three times for wall time
  and report median plus range; accelerator cycle counts should be identical.
- For optimization quality, use at least 30 seeds where feasible and report best,
  median, interquartile range, success probability, and time-to-target at a declared
  objective threshold. Ten-run precedent exists, but 30 gives a more defensible
  distribution for stochastic claims.
- Pre-register or freeze configuration selection on a tuning set, then evaluate
  solution quality on a disjoint test set to avoid tuning every instance.

## Minimum paper figures and tables

1. CIR versus cores-only latency, bytes, byte-hops, and memory traffic across graph
   locality classes and sizes.
2. Latency-throughput curves from RTL and model on the same small NoC, including
   the relative-error panel.
3. Directed-link utilization heatmaps and packet-latency CDFs for representative
   low-, median-, and high-congestion workloads.
4. Scaling of iteration cycles, maximum-link utilization, p99 packet latency, and
   DRAM rejection rate through at least 128K spins.
5. Solver quality distribution and time-to-target across seeds, separately from
   implementation-correctness differential tests.
6. A fidelity table marking every result as RTL measured, RTL-differential model,
   exact-event/Ramulator, calibrated, or interconnect projection.

