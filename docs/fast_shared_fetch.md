# Simulator-only optimization for cores-only shared-fetch v3

`azilla_cycle_model.fast_shared_fetch_cli` selects the same two-slot canonical
shared-fetch implementation as `shared_fetch_double_cli`, replacing only its
timing-only endpoint object with the existing `FastCoreIteration`.

The replacement uses a countdown for timing-only local-MVM bookkeeping and
caches the next-state sign vector until its accumulator is replaced. The
original core controller remains responsible for lifecycle transitions.
Memory service, weight transfers, replay slots, buffer capacity, SRAM accesses,
packet locks, routing, arbitration, MVM latency and compute placement are
unchanged. It does not select the other `fast_cli` optimizations, and does not
change the original implementation or any running process.

Use the same exact-event arguments, explicitly specifying cores-only:

```bash
PYTHONPATH=model python3 -m azilla_cycle_model.fast_shared_fetch_cli \
  simulate-exact-events --execution-mode cores-only --help
```

This entrypoint is timing-only, not an arithmetic validation path. Results
retain the original fidelity labels and full-geometry coverage limitations.

Validation commands:

```bash
PYTHONPATH=model python3 -m unittest discover -s model/tests -p test_fast_core.py -v
python3 scripts/check_fast_shared_fetch.py --output results/fast_shared_fetch_check_new
```

The complete-run harness compares the original frozen v3 model against the
candidate on full-H1 dense1K, two-H1 dense512, dense512 with injected stalls,
and sparse512. It checks every result field plus accepted NoC transfers and
internal core/memory/state event timestamps. Require the saved report's
`status: pass`; a harness run or directory existing is not a passing result.
Recorded wall times are sequential diagnostic measurements on a shared host.

`scripts/run_fast_core128k.py` requires the local passing gate and launches a
separate frozen 128K uniform sparse replay. It saves source/input hashes,
wall time and independent metrics, audits conservation and compares saved
metrics against the original run when available. It does not stop or alter
the original. Completing a small equivalence check does not constitute
128K RTL verification.

September8 local validation passed all four cases, including every recorded
internal core/memory/state event and NoC transfer timestamp. Candidate speedups
were1.164x(fullH1dense1K),1.116x(twoH1dense512),1.106x(stall7),1.118x(sparse512).
These are modest diagnostic gains, not a measured128K speedup. Results:
`results/fast_shared_fetch_validation_20260908/retry/report.json`.
The first attempt failed while serializing tuple-keyed diagnostic counters;
the harness export was corrected without changing either simulator.
