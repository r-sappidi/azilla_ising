# Script index

Run scripts from the repository root. Existing paths are retained because
frozen manifests, active jobs and script imports depend on them. These groups
organize the entry points without breaking that interface.

## Current streaming endpoint workflow (VCS)

1. `evaluate_streaming_endpoints.py`: component arithmetic and shared-fetch RTL.
2. `evaluate_streaming_workloads.py`: small matched solution trajectories and mesh timing.
3. `validate_streaming_campaign.py`: integrated RTL/model differentials.
4. `run_streaming_nominal_validation.py`: nominal 64K/128K timing comparisons.
5. `run_streaming_endpoint_matrix.py`: streaming endpoint cells paired with
   audited CIR points (exact-event timing projections, not RTL coverage).
6. `supervise_dense_provisioning.py`: guarded provisioning queue through
   `queue_dense_streaming_provisioning.py`, dependent on nominal validation.

See [streaming instructions](../docs/streaming_endpoints.md). Use unique output
directories and inspect `--help` before launching. A stale status file is not
proof of a running queue. Resume only after checking process identities,
orphaned workers, source hashes, disk space and previous failure evidence.

## Buffered v3 and CIR validation

- `start_single_h1_validation.py`, `check_shared_fetch_single_h1.py` and
  `run_shared_fetch_single_h1.sh`: full single-H1 endpoint checks.
- `check_shared_fetch_two_h1.py`, `run_shared_fetch_two_h1.sh` and
  `run_mesh_accuracy_suite.py`: multi-H1 paths and contention.
- `check_cir_exact_vcs_trace.py`: CIR event/cycle comparison against VCS evidence.
- `check_cpp_exact_model.py`, `check_cpp_exact_vcs_trace.py`: C++ port of
  the exact-event model (`docs/cpp_exact_model.md`) against the Python
  oracle and against stored CIR VCS traces, with the three-way statement.
- `check_cpp_streaming_model.py`, `check_cpp_streaming_vcs_trace.py`,
  `run_streaming_oracle.py`, `run_streaming_rtl_all.py`: the same checks for
  the C++ cores-only streaming/buffered endpoint path.
- `audit_shared_fetch_v3.py`, `audit_validation_fairness.py`: baseline controls.
- `check_16k_arithmetic.py`: independent arithmetic reference workflow.

Historical `check_cycle_model.py`/`check_full_cycle_model.py` and Verilator
scale scripts remain reproducibility tools, not the current VCS campaign default.

## Performance campaigns

- `run_cir_matched_campaign.py`: versioned paired campaign execution.
- `run_matched_dense_exact.py`: dense exact-event pairs.
- `run_frozen_mapping_ablation.py`, `run_mapping_comparison.py`: mapping studies.
- `run_memory_width_sweep.py`, `run_sparse_geometry_sweep.py`,
  `run_interconnect_sensitivity.py`: hardware sensitivities.
- `run_fast_dense64k.py`, `run_fast_core128k.py`, `check_fast_shared_fetch.py`,
  `check_fast_h0_adapter.py`: opt-in model acceleration and equivalence checks.

Older launchers may select directed-fetch, calibrated, single-slot or buffered
models. Inspect their policy and snapshots; do not automatically restart them.
Hybrid/static/dynamic experiments are exploratory, not certified default modes.

## Workloads and numerical quality

- `prepare_ising_benchmarks.py`, `prepare_planted_maxcut_suite.py`: inputs.
- `run_reference_solution_quality.py`, `run_broad_solution_quality.py`:
  independent numerical evaluation.
- `run_gset_instance_tuning.py`, `run_robust_gset_tuning.py`,
  `run_fresh_quality_holdout.py`: tuning and held-out quality checks.
- `diagnose_degree32_quality.py`, `analyze_g81_trajectories.py`: diagnostics.

Timing-only results are not numerical-quality results. Keep initialization,
noise schedules, update rule and iteration budgets with every quality result.

## Analysis and publication

- `consolidate_audited_results.py`, `audit_paper_results.py`,
  `index_paper_artifacts.py`: audit and index completed evidence.
- `analyze_noc_trace.py`, `analyze_dram_patterns.py`, `analyze_traffic_patterns.py`:
  traffic and memory analysis.
- `plot_paper_matched_pairs.py`, `plot_dense_internal_traffic.py`,
  `plot_rtl_internal_stats.py`: figures from scoped, labeled results.
- `check_dense_analytical_model.py`, `verify_dense_analytical_v3.py`:
  analytical-model checks.
- `check_manuscript_draft.py`: manuscript checks.

## Maintenance and synthesis

- `cleanup_build_cache.py`: dry-run-first cleanup of explicit old Verilator
  compiler caches; never removes executables, source snapshots or results.
- `test_cleanup_build_cache.py`: file-selection and retention safety tests.
- `setup_yosys_asap7.sh`, `run_asap7_synthesis.sh`,
  `audit_support_synthesis.py`: synthesis support, separate from validation.
- `test_dense_streaming_provisioning.py`: queue recovery tests.

Do not relocate or delete campaign-specific scripts merely because their names
mention an old deadline. Check running/paused processes and frozen manifests first.
