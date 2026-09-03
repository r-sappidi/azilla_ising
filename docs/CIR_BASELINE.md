# CIR ablation contract

This document fixes the baseline used to evaluate Azilla's compute-in-router
(CIR) organization. It is an experimental contract, not a claim that the
cores-only design is the preferred Azilla implementation.

## CIR design

Azilla stores each occupied unordered off-diagonal 32-by-32 coupling block
once. An H0-local, H1-local, or cross-H1 symmetric MVM evaluates the block
once and produces one 32-element contribution for each endpoint. Endpoint
spin cores perform the final accumulation and state update. Here, CIR means
that interaction computation is attached to the communication hierarchy; it
does not mean that arithmetic is inserted into the FlooNoC router pipeline.

## Cores-only baseline

The baseline is destination-stationary. Every spin core owns 32 output spins,
one one-sided local MVM datapath, and the complete accumulation for those
spins. An unordered off-diagonal block `(A, B)` becomes two directed jobs:

- destination A reads the A-to-B row representation and evaluates A's field;
- destination B reads the transposed B-to-A representation and evaluates B's
  field.

The two directional representations are logical weight copies, and each job
issues 32 256-bit weight requests. Jobs targeting one core execute serially;
different cores may execute concurrently. H1-local and cross-H1 MVM engines
are absent. A remote state block is transferred at most once to each consuming
H1 and may be reused by its destination cores. No partial-result packets leave
a core. State remains double-buffered and commits synchronously at the
iteration boundary.

## Controlled quantities

CIR and cores-only comparisons must use the same graph, vertex placement,
initial state, coefficient/noise schedule, H0/H1 geometry, mesh, memory
technology, aggregate H0 memory interfaces, link parameters, arithmetic
widths, and iteration count. Report both raw performance and resource demand.
The literal baseline activates one already-present local MVM per spin core,
whereas CIR adds smaller shared H0/H1/cross pools. Raw latency is therefore not
an iso-area or iso-compute result. Area-normalized conclusions require the
separate synthesis study.

## Required measurements

Every comparison records iteration cycles, directed versus unordered jobs,
logical weight capacity, weight reads, state and partial traffic, injected
flits, physical-link flits, flit-hops, stalls, link utilization, endpoint load,
and memory request latency/rejection. Traffic is checked for conservation.

The calibrated event model is used only for screening. Its cores-only mode
does not resolve contention among all destination cores sharing one H0 memory
interface. Finalists require the live-Ramulator exact-event path and a
representative RTL reproduction. Until those complete, calibrated cores-only
speedups are labeled `calibrated-extrapolation` and are not headline results.

## Static core-preferred hybrid

The `hybrid` calibrated-model mode partitions every unordered off-diagonal
block before an iteration starts. A block is owned exclusively either by its
two endpoint cores, which execute two directed jobs, or by its native H0,
H1-local, or cross-H1 CIR pool, which executes one symmetric job.

The deterministic scheduler searches per-core incident-job caps. For each cap
it retains blocks at their endpoint cores in stable schedule order and spills
the remainder to CIR. It selects the partition minimizing the maximum nominal
service time across destination cores and all CIR queues; ties retain more work
at cores. This is a static screening policy, not runtime work stealing.

The exact-event hybrid path sends core-local and H0-CIR weight requests through
the same 16-lane H0 Ramulator endpoint, so the two execution classes contend
for the modeled memory interface. H1 and cross-H1 CIR pools retain distinct
endpoints. It is labeled `cycle-structured-unverified`: unlike the CIR path, it
has not been differentially matched to the integrated mesh RTL. The standalone
256-spin mixed-ownership RTL test checks exclusive arithmetic composition with
four core-owned and three CIR-owned blocks in both Verilator and VCS. It is a
subsystem validation, not a full-mesh hybrid timing result.
