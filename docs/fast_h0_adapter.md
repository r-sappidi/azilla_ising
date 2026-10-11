# Opt-in exact H0 adapter optimization

`azilla_cycle_model.fast_adapters.FastH0Adapter` replaces exhaustive
per-core/per-engine selection with selection over destinations actually named
by valid engines and existing packet locks. Immutable empty outputs are shared;
combinational results are cached using partials, destination ready bits and
lock state. The original `H0Adapter` remains unchanged as the oracle.

No arithmetic latency, memory requests, routing policy, arbitration priority,
packet lock, ready signal, or cycle count is intentionally changed. Locked
engines retain priority even while their valid signal is low. The cache key
includes lock values to remain correct under diagnostic state edits.

This optimization is separate from `fast_cli`: it neither activates that
entrypoint's other optimizations nor changes any existing supervisor gate.
Existing Python workers and their frozen snapshots are unaffected. A running
simulation cannot adopt it without a restart/checkpoint mechanism.

## Use

Replace `azilla_cycle_model.cli` with `azilla_cycle_model.fast_adapter_cli`
for an exact-event timing command. All other arguments remain unchanged:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.fast_adapter_cli \
  simulate-exact-events --help
```

This changes the CIR H0 adapter; it does not accelerate the separately
implemented shared-fetch cores-only pipeline. It preserves the reference's
fidelity labels and does not extend RTL differential coverage.

## Verification

```bash
PYTHONPATH=model python3 -m unittest discover -s model/tests -p test_fast_adapters.py -v
PYTHONPATH=model python3 -m unittest discover -s model/tests -p test_adapters.py -v
python3 scripts/check_fast_h0_adapter.py --output results/fast_h0_adapter_new
```

The randomized test compares outputs before/after edges and all lock state
over 15,000 cycles across 1/8/256-core adapters, including changing ready
signals, invalid and out-of-range destinations, same-destination contention,
reset and diagnostic lock edits. A separate test checks idle cache behavior.

The complete-run harness uses the frozen dense-v3 source snapshot and patches
only its H0 adapter in an isolated process. It compares every result field
and accepted transfer timestamp for:

- 1,024 active dense spins in one full 8-H0 × 256-core H1;
- 512 dense spins in two H1s, each with two H0s × four cores;
- the same two-H1 case with shuffled schedule order.

It records both outputs, unprofiled wall time, source hashes and pass/fail
checks. Require `report.json` status `pass` before citing that run. These are
complete model-equivalence tests, not new RTL runs or a 64K-active certificate.
The reordered case is a schedule-order test, not an entire mapped-artifact
workflow certificate. Timing measurements are sequential on a shared server,
so speedup is diagnostic rather than a controlled simulator benchmark.

### September 8 measured results

All three complete-run comparisons passed, with identical result fields and
accepted transfer traces (`results/fast_h0_adapter_20260908/report.json`):

| Case | Original wall seconds | Candidate wall seconds | Speedup |
|---|---:|---:|---:|
| Full H1, 1K active dense | 39.958 | 17.651 | 2.264× |
| Two small H1s, 512 dense | 1.219 | 1.169 | 1.043× |
| Same, reordered schedule | 1.209 | 1.171 | 1.033× |

These include initialization and complete iteration execution. The full-H1
case has the intended 8×256-core adapter geometry but only 1K active spins;
2.264× is not a measured speedup for a completed 64K-active workload.
