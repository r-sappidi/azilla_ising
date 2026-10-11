# Dense analytical model: shared-fetch v3

This model predicts dense interaction counts, canonical weight storage/read
volume, delivered payload, and direct-mesh traffic without enumerating spin
pairs or simulating cycles. It also provides necessary latency lower bounds
for both execution modes and an **unfitted steady-state CIR latency estimate**.
It does not provide a validated large-mesh end-to-end latency predictor or a
cores-only latency predictor.

Implementation: [`dense_analytical.py`](../model/azilla_cycle_model/dense_analytical.py).
Validation: [`verify_dense_analytical_v3.py`](../scripts/verify_dense_analytical_v3.py).
This supersedes the historical `check_dense_analytical_model.py` for v3 analysis;
that older script consumes a different experiment generation.

## Contract

- One timing-only dense iteration; 32 spins per core, all off-diagonal block
  pairs occupied, contiguous active vertices, and identity block mapping.
- Default: eight H0 tiles per H1, 256 cores per H0, 4/2/4 H0/H1/cross engines
  (forwarding lanes in cores-only). H1 count grows above 65,536 spins. Smaller
  problems retain one full H1 with inactive padding cores.
- Canonical unordered 32×32 int8 block fetched once at the same hierarchy
  owner in both modes; v3 two-slot, two-unicast forwarding for cores-only.
- Default H1-pair owner allocation and direct XY-routed mesh, 256-bit flits,
  one accepted flit per port/directed link per cycle. No multicast or loss.
- Counts exclude repeated iterations and distinguish diagonal core SRAM
  allocations from off-diagonal hierarchy DRAM jobs. No arithmetic or
  solution-quality claim is implied by complete block occupancy.

Nondefault link serialization/credits, custom owner maps, dynamic execution,
different payload widths, and alternative memory organizations require a
corresponding extension and new checks. This model does not implement them.

## Work and memory equations

Let B=N/32 be the active core blocks, b[n] the active blocks in H1 n, and
c[n,h] the active blocks in H0 h. Define C(x,2)=x(x−1)/2.

```
J_total       = C(B,2)
J_H0[n,h]     = C(c[n,h],2)
J_H1[n]       = C(b[n],2) − sum_h J_H0[n,h]
J_cross[n,m]  = b[n] × b[m], n < m
J_owner[o]    = sum of J_cross[n,m] assigned to owner o

canonical DRAM weight bytes = 1,024 × J_total
32-byte DRAM row requests   = 32 × J_total
active diagonal SRAM bytes = 1,024 × B
```

These count padded 32×32 blocks, not packed triangular scalar storage. Every
occupied off-diagonal block yields two endpoint contributions. CIR delivers
256 bytes per block (two 128-byte partials); cores-only delivers 2,048 bytes
(two complete weight matrices, one transposed). Consequently the modeled
interaction payload is exactly eight times smaller in CIR. This is a byte
count, **not an eightfold latency or energy prediction**.

The DRAM traffic is identical in both modes. Capacity and bandwidth must be
provisioned separately: at 1,048,576 spins the model counts 536,854,528
canonical jobs and 549,739,036,672 bytes (~512 GiB) of off-diagonal weights.
This is not evidence that a particular memory package can accommodate them.

## State publication and mesh traffic

For each H1 pair (a,b), let o be its assigned cross-compute/fetch owner.
Its result/weight flow contains four/32 flits from o to each endpoint for
every canonical block job. Owner-local packets still use injection/ejection,
but traverse zero physical links.

CIR publishes each active source block once per distinct required owner,
including an owner on its own H1. Cores-only publishes each block once to
every other active destination H1. These state policies are **not identical**.
Source/destination publication groups are deduplicated before counting.
Each H1 also emits one owner-local epoch-done flit.

For every aggregated source/destination flow, route X first, then Y. Add its
flits to every directed link on that path. The sum of link counts is physical
hop-flit volume; it is distinct from injected payload. Arbitration changes
timestamps/stalls but not these accepted volumes under the stated routing
and no-loss contract. Utilization, stalls, and memory latency are not inferred
from volume alone.

Counting and routing operate on hierarchy-level groups, not B² jobs. The
existing owner allocator is reused to preserve the actual placement policy;
its own cost grows with mesh size. There is no claim of constant runtime for
arbitrarily large meshes. For the tested range through a 4×4 mesh, evaluation
of 20 size/mode combinations takes about 0.02 seconds after Python imports.

## Latency: bounds versus estimate

The exact request-free testbench initialization protocol is:

```
T_init = 3 + 33 × allocated_core_count
```

This counts padding cores and is a testbench loading protocol, not measured
host-to-device initialization latency.

Necessary iteration bounds take the maximum, not the sum, of:

- per-resource 32×ceil(jobs/engines) CIR arithmetic demand;
- per-resource 64×ceil(jobs/lanes) cores-only forwarding demand;
- maximum injection, ejection, and directed-link flit counts;
- cores-only per-core arithmetic demand, 32×(B−1);
- cores-only per-child H1 input demand, 32×c×(B−c).

These bounds omit positive delays. Their slack against an observed run is
not prediction error, and ratios of two lower bounds are not speedups.

For CIR, a saturated isolated hierarchy engine initiates a job every 38
cycles when its two operands occupy different state SRAM banks, or every
39 cycles when they share a bank. This follows the existing controller and
one-cycle SRAM response; a 64-job component test checks both periods.
The analytical model counts same-bank jobs S exactly using the eight block
index residue classes, then estimates:

```
T_CIR_steady = max over resources of ceil((38×J + S)/engine_count),
               also bounded by injection, ejection and directed-link demand
```

There are **no fitted coefficients, RTL timestamp lookups, or per-size
correction factors**. The approximation assumes saturated engines, balanced
job dispatch and overlapping stages. It omits startup/drain, inter-engine
bank contention, DRAM admission/refresh stalls, and queueing/backpressure
penalties. It is not a proven upper bound or a bounded-error guarantee.
No arbitrary fixed DRAM delay has been introduced to conceal those effects.

## Verification obtained September 8

- Explicit block enumeration checks hierarchy placement, same-bank counts,
  state deduplication and flow volumes for five geometries with full and
  partially active populations, for both modes.
- All source/destination paths in a 4×4 mesh match the cycle model's router.
- Four dedicated unit tests pass, including the 38/39-cycle component test.
- All available work/request/initialization/traffic comparisons pass on ten
  completed v3 exact-event points: both modes at 1K, 4K, 8K, 16K and 32K, with the
  fixed full-H1 geometry.
- Injection/ejection types, every directed-link volume and initialization
  match six VCS traces: both modes at 512, 1,024 and 2,048 spins, each with
  two H1s, two H0s/H1 and respectively 4/8/16 cores/H0.
- Separate passed VCS differential reports independently confirm both full-H1
  16K reference iteration times. This is not 64K-active or large-mesh RTL
  verification.

CIR steady-state signed iteration error, (estimate/reference−1):

| Geometry | Active spins | Error |
|---|---:|---:|
| Fixed full H1 | 1,024 | −6.307% |
| Fixed full H1 | 4,096 | −0.543% |
| Fixed full H1 | 8,192 | −0.423% |
| Fixed full H1 | 16,384 | −0.183% |
| Fixed full H1 | 32,768 | −0.203% |
| Two small H1s | 512 | −20.984% |
| Two small H1s | 1,024 | −7.541% |
| Two small H1s | 2,048 | −2.634% |

The larger fixed-H1 points agree closely; the smallest two-H1 case does not.
Report both. These are tests against available reference points,
not a prospective held-out dataset. The formula has not been tuned to them.
The verifier's `pass` means conservation and lower-bound consistency, not
acceptance of every latency estimate. Cores-only bound slack is substantial;
do not promote those bounds into end-to-end performance estimates.

## Permissible scalability claims and remaining gate

Exact formulas support storage, delivered communication, hierarchy work
placement, mesh hop volume and necessary service scaling through one million
spins under this contract. With fixed H1 capacity and growing H1 count, global
dense work/storage grow quadratically; per-owner work depends on the actual
pair allocator and mesh bottlenecks. Scalability does not mean constant
iteration time for dense all-to-all interactions.

CIR latency beyond covered cases may be shown as an **analytical projection**,
alongside observed reference markers and measured error. Do not claim the
0.55% fixed-H1 error persists to 128K–1M. Before using projected high-size
latency as a principal performance result, compare the unchanged formula
against completed 64K fixed-H1 and 128K+ multi-H1 runs, including memory
and link saturation. Existing simulation campaigns were left untouched.
The formula alone cannot establish a large-size CIR-versus-core speedup.

## Reproduce

Run from the repository root, with completed local v3 reference artifacts:

```bash
PYTHONPATH=model python3 -m unittest discover -s model/tests -p test_dense_analytical.py -v
python3 scripts/verify_dense_analytical_v3.py --output results/dense_analytical_new
python3 scripts/plot_dense_analytical_v3.py \
  --report results/dense_analytical_new/report.json \
  --output plots/dense_analytical_new
```

Output directories must be new. The verifier reads completed audited results,
rejects a changed summary or incorrect cores-only fetch policy, records source
hashes, and writes `report.json`, `validation.csv`, `scaling.csv` and a summary.
Incomplete points are excluded; no active simulation or RTL source is changed.
Plots explicitly distinguish observed cycles, projections and lower bounds.
