# CIR versus cores-only comparison contract

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
memory statistics. The current 40 Ramulator ticks per accelerator cycle at
250 ps/tick imply 10 ns/cycle (100 MHz). A 1 ns synthesis constraint does not
authorize a different conversion without changing the modeled clock ratio.

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

## Implementation and evidence pointers

- `model/azilla_cycle_model/core_full_events.py`: directed job/read accounting.
- `model/azilla_cycle_model/scheduler.py`: interaction scheduling and ownership.
- `scripts/audit_validation_fairness.py`: paired-control and coverage audit.
- Local audit: `results/paper_deadline_20260907/validation_fairness_v1/REPORT.md`.
- Local resource accounting: `results/paper_deadline_20260907/resources_v1/RESOURCE_ACCOUNTING.md`.

The local reports are generated evidence, not required files in a clean clone.
This contract changes documentation only; it does not modify active simulations
or retroactively certify their results.
