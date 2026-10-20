# CIR versus cores-only comparison contract

**Historical comparison details.** For current buffered v3 and streaming
policies, use [shared-fetch](shared_fetch_baseline.md) and
[streaming endpoints](streaming_endpoints.md). The directed-fetch accounting
below must not be applied to those separately versioned designs.

## Question and scope

The intended baseline now requires CIR-matched single fetch, downstream
double buffering and destination-banked routing, with all interaction
arithmetic at endpoint cores. The earlier directed-fetch and single-slot,
single-delivery-H0 variants below are historical ablations, not that baseline.
Their ratios must be regenerated against the versioned intended baseline;
see [shared-fetch baseline](shared_fetch_baseline.md).

The comparison measures the latency and communication of two implemented
interaction-processing organizations: symmetric computation at hierarchy
nodes with block-fetch reuse, and destination-core computation with directed
block fetches. It measures their combined mechanisms, not compute placement
in isolation. Hybrid scheduling is outside the primary comparison.

## Implemented differences

For each occupied unordered off-diagonal 32-by-32 int8 coupling block:

| Property | CIR | Cores-only |
|---|---|---|
| Weight ownership | Canonical H0, H1-local, or cross-H1 memory owner | Same canonical owner; weights are not relocated to endpoint SRAM permanently |
| Work | One symmetric job computing both endpoint contributions | Two directed jobs, one at each destination core |
| Canonical weight reads per iteration | One 1,024-byte block read | Two 1,024-byte block reads, with transpose interpretation where required |
| Interaction computation | Owner's hierarchy MVM pool | Destination core's MVM |
| Result delivery | Two 128-byte partial vectors | Contributions accumulated at destination cores; no hierarchy-generated partial return |
| Weight delivery | Owner-local compute consumes the block | Each destination receives the full block |
| Off-diagonal compute resources | Shared H0/H1/cross engine pools | One physical MVM per core |

These byte counts describe logical payloads, not physical flit-hops or all
traffic. State publication, packet overhead, local transfers, and path length
must be accounted for separately. Two reads do not imply two stored copies.
Diagonal blocks remain endpoint-local; the table does not describe their work.

The nominal H1 contains eight H0s and 256 cores per H0, with 32 spins per core.
CIR has 8×4+2+4 = 38 hierarchy engines per H1 for off-diagonal interactions;
cores-only can use 2,048 endpoint engines per H1. Endpoint engines also exist
in the CIR architecture; this is not a comparison of equal active engine
counts. In cores-only, each H0 additionally provisions a globally addressable
banked operand cache of four bytes per global spin block. Report its replicated
capacity rather than substituting the CIR H0-local state-table capacity.

## Paired controls

Hold the following equal within each mode pair:

- Dataset and signed weights, vertex permutation, padded geometry, and occupied
  unordered block set for that permutation.
- Canonical block owner allocation and memory placement at every hierarchy
  level, memory-interface topology, Ramulator configuration/library, and clock ratio.
- H0/H1/cross provisioning parameters, link parameters, arbitration settings,
  iteration count, and timing-model source version or verified equivalent version.
- Arithmetic format and update rule; in separate arithmetic experiments, also
  initial state and coefficient/noise schedule. Timing-only runs do not evaluate
  numerical states and are not solution-quality comparisons.

Equal provisioning parameters do not equalize active compute, traffic, fetch
counts, cache capacity, or area. Width and provisioning sensitivities each form
their own matched pairs; do not pool them with the nominal configuration.
Across different vertex mappings, occupied block counts may legitimately change;
validate each artifact against its own permuted graph, not a common block list.

## Reporting and acceptance

Report iteration cycles and initialization-inclusive total cycles separately,
alongside available compute, logical block reads, measured mesh traffic and
memory statistics. The default 40 Ramulator ticks per accelerator cycle at
250 ps/tick imply 10 ns/cycle (100 MHz); the C++ model's `--ticks-per-cycle 4`
models a 1 ns cycle explicitly and results must state which ratio was used.
A 1 ns synthesis constraint does not authorize a different conversion without
changing the modeled clock ratio, and a modeled 1 GHz ratio is not timing
closure.

Accept a pair only after both runs complete, input/configuration provenance
matches, and applicable completion/conservation checks pass. Preserve individual
paired results and dispersion, including losses. Keep geometry-specific RTL
coverage distinct from large-model projections and optimization equivalence.
Analytical internal payload counts are not measured internal stall statistics.

Supported claim: “For the evaluated workloads and configurations, symmetric
hierarchy execution with fetch reuse achieves the reported latency and traffic
relative to the implemented directed-fetch endpoint baseline.”

Unsupported claims include an isolated compute-placement speedup, superiority
over every possible cores-only design, equal-area or equal-energy superiority,
and large-geometry RTL validation inferred from small differential tests.

A stronger endpoint baseline can fetch a canonical block once and deliver it
to both destination cores. A separately versioned implementation now has small
integrated VCS differential coverage and an active performance campaign; see
[shared-fetch baseline](shared_fetch_baseline.md). It is not part of the original
directed-fetch matrix. Until matched results are audited, the original comparison
does not establish performance against that alternative. Shared-fetch buffering
cost and unequal compute resources still preclude an automatic iso-area claim.

## Flat weight-stationary baseline (October 2026)

The cores-only baseline above keeps canonical hierarchy ownership and moves
weight blocks to the destination cores. Built Ising/SB machines instead keep
weights where the update happens and exchange states. The C++ exact-event
model implements that organization as `--execution-mode flat-stationary`
(see `docs/cpp_exact_model.md`): each H0 stores the row representation of
every occupied block of its 8,192 spins in its own DRAM system (two stored
copies and two 1,024-byte reads per unordered block), its directed engines
return partials to local cores on chip, and only one-bit state blocks cross
the mesh, one flit per (source block, consuming H1). It uses the same mesh,
links, Ramulator configuration, initialization, publication cadence and
completion barrier as CIR; H1-local and cross-H1 engines and DRAM systems
are idle. Hold the engines per H0 explicit when reporting: 4 matches AZilla's
H0 pool, 8 matches its H0 multipliers (a directed engine uses half the
multipliers of the symmetric engine per row cycle), and neither counts
AZilla's H1/cross pools or the H1/cross DRAM channels the flat design does
not have. No RTL implements it; results are cycle-structured-unverified.

## H0-anchored symmetric ownership (October 2026)

`--execution-mode cir-h0-anchored` keeps CIR's single symmetric fetch and
engines but anchors every off-H0 block at one endpoint H0, returning the
remote partial through a per-H0 uplink (H1 crossbar for siblings, mesh for
other H1s). Compared with CIR it halves the mesh partial flits per cross-H1
block and spreads weight bytes and engine work over the H0 DRAM systems and
engine pools; compared with the flat baseline it halves the stored copies and
reads. It idles the H1-local and cross-H1 pools and DRAM systems, so equal
engines per H0 means fewer total engines than CIR. With
`--anchored-accumulate 1` the uplink sums remote partials per block at the
owner and flushes once per touched block after the node is done, which is the
primary design since October 15 (`docs/current_architecture.md`). Swap-in
RTL (`rtl/experimental/anchored/`) matches the model cycle for cycle on small
geometries and dense 64K single-H1 at 4 engines, 64 outstanding and 40 ticks
(`docs/anchored_rtl.md`); every other configuration, including the 1 GHz /
HBM4 provisioning, remains cycle-structured-unverified.

## Implementation and evidence pointers

- `model/azilla_cycle_model/core_full_events.py`: directed job/read accounting.
- `model/azilla_cycle_model/scheduler.py`: interaction scheduling and ownership.
- `scripts/audit_validation_fairness.py`: paired-control and coverage audit.
- Local audit: `results/paper_deadline_20260907/validation_fairness_v1/REPORT.md`.
- Local resource accounting: `results/paper_deadline_20260907/resources_v1/RESOURCE_ACCOUNTING.md`.

The local reports are generated evidence, not required files in a clean clone.
This contract changes documentation only; it does not modify active simulations
or retroactively certify their results.
