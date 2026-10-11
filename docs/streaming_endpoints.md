# Streaming endpoint candidate

This is an opt-in architecture change, not a simulator speed optimization.
Existing buffered v3 campaigns and their source files are unchanged.

## RTL contract

Select `rtl/experimental/streaming/core.sv` and `mvm.sv` **instead of**
`rtl/core.sv` and `rtl/mvm.sv`; do not compile both pairs. The alternate
files deliberately retain the original module names and hierarchy ports.

- One physical MVM serves both the resident diagonal block and remote jobs.
- The diagonal block still uses synchronous SRAM reads.
- A remote job starts its MVM when the frozen operand state response arrives.
- Every accepted 256-bit weight beat evaluates one canonical row. Gaps pause
  the row index and intermediate results; no speculative row consumption.
- Normal and transpose products use the same arithmetic as the baseline.
- Remote rows bypass weight SRAM. The experimental SRAM instance retains its
  original capacity; no SRAM area reduction has been claimed.
- Completion/retirement and iteration commit remain explicit. There is no
  extra endpoint engine and no change to shared-fetch ownership or NoC routing.

## Reproduce the RTL-first gates

Run from the repository root with the normal VCS license environment. Use a
new output directory each time. Build and run wall time are saved separately.

```bash
python3 scripts/evaluate_streaming_endpoints.py --output results/streaming_rtl_NEW
python3 scripts/evaluate_streaming_workloads.py --output results/streaming_workloads_NEW
```

The first gate checks signed arithmetic, transpose, gaps, nonzero noise,
three-iteration lifecycle, and nine two-iteration live-Ramulator/shared-fetch
cases. Repeated-block cases are explicitly stress stimuli, not graph results.

The second gate compares buffered and streaming spin/cut trajectories for
three 64-spin graph families, two seeds, two noise amplitudes and 32 iterations.
It also compares 18 timing workloads (dense occupied blocks, sparse blocks,
and a destination hotspot; 2x1/2x2/4x2 meshes; stalled and unstalled delivery).
Timing occupancy witnesses are not dense arithmetic datasets. Solution checks
establish preservation of the update algorithm, not approximation guarantees
or near-optimal MaxCut on real benchmark suites.

## Opt-in model and strict differentials

Only after the RTL correctness/performance gate, use:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.streaming_cli \
  simulate-exact-events --execution-mode cores-only [usual model arguments]

python3 scripts/check_streaming_core.py \
  --log results/streaming_rtl_NEW/component/streaming/run.log \
  --output results/streaming_rtl_NEW/component/streaming/differential.json

python3 scripts/validate_streaming_campaign.py \
  --rtl results/streaming_rtl_NEW --workloads results/streaming_workloads_NEW \
  --output results/streaming_differentials_NEW
```

The new fetch-policy label is
`single_fetch_two_slot_cir_routes_v3_streaming_endpoint_v1`. Shared fetching
remains v3; the suffix distinguishes the changed endpoint timing contract.
Do not merge results under the old buffered label or bypass old figure gates.

The component differential replays external inputs. Integrated comparisons
derive controller timing independently and check total timing, accepted
core/memory/state/NoC events as exposed by each checker; the arithmetic wrapper
also checks per-cycle core controls and final states. No failed check is waived.
The campaign records source hashes and verifies they remain unchanged.

Scope: nominal 4/2/4 source lanes, existing baseline memory and direct-wire
links. Model testing at larger sizes can build on mechanism-level validation;
this is not full-size RTL verification or physical-link/DRAM-device validation.
Changed memory widths, routing, scheduling, or additional simulator shortcuts
need representative new checks. Do not switch ongoing campaigns automatically.

## Completed candidate validation (October 4, 2026)

- 107 Python tests; component differential: 420 cycles and 96 arithmetic fields.
- 27 integrated RTL/model differentials passed with unchanged source hashes:
  nine shared-fetch arithmetic/event cases and 18 timing cases on 2x1, 2x2,
  and 4x2 meshes, up to 2048 spins.
- All 12 matched 32-iteration solution trajectories agree with buffered RTL
  and the independent scalar state-update reference.
- Matched mesh iteration speedups range from 1.007x to 1.760x at the same
  assumed clock. No synthesis or physical timing closure was performed.

This supports workload experimentation within the stated mechanism coverage.
It does not turn large modeled runs into full-size RTL measurements. Buffered
performance data remains valid for the old architecture; comparisons against
streaming endpoints require separately labeled new measurements.

## Nominal geometry timing checks (October 4, 2026)

The source-hashed campaign in `results/streaming_nominal_20261004_v1/`
completed successfully with 256 cores/H0, eight H0/H1 and 4/2/4 source lanes.

| Workload | Buffered iteration cycles | Streaming iteration cycles |
|---|---:|---:|
| 65,536-spin toroidal, one H1 | 16,849 | 14,768 |
| 131,072-spin hierarchy stress, two H1, unstalled | 216,574 | 201,507 |
| Same stress with forced stalls | 620,623 | 602,755 |

Recorded cycle/event differentials passed, with source hashes unchanged.
These are timing-only workloads, not full-size numerical-quality checks or
coverage of all dense/sparse traffic patterns. The hierarchy stress case is
an occupancy witness, not a benchmark solution-quality result.
Reproduction entry point: `scripts/run_streaming_nominal_validation.py`.
