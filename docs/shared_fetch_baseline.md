# Experimental shared-fetch endpoint baseline

## Intended CIR-matched baseline (version 3, validation gated)

The original implementation below is retained as a historical restricted
baseline, not the intended compute-placement comparison. Inspection found
two differences from CIR: one downstream replay slot instead of two, and
one weight delivery per entire H0/source instead of independently banked
destination outputs. Its speedups must not be used as evidence against the
intended CIR-matched endpoint design.

The opt-in `shared_fetch_double_cli` implements `single_fetch_two_slot_cir_routes_v3`:

- Same canonical memory owner, single block fetch, Ramulator limits and two
  streamer reassembly slots per source lane as CIR.
- Two additional downstream forwarding slots per lane, corresponding to
  CIR's two downstream weight slots; fill/drain overlap on different slots.
- H0-local source lanes may deliver concurrently to distinct endpoint cores.
- H1-local lanes arbitrate independently per destination H0. Each external
  H0 link remains one beat/cycle, shared with cross-H1 delivery and locked
  through an entire 32-beat weight packet.
- The cross-H1 mesh retains its existing injection, buffering and backpressure.
- Both endpoints receive complete weight blocks and compute/accumulate locally;
  no interaction computation is performed in the forwarding nodes.

No DRAM bandwidth or free multicast is added. Forwarding storage is currently
register-modeled: two KiB/lane, or 76 KiB/H1 at 4/2/4, in addition to the
streamer storage. Equal logical slot counts do not establish equal physical
area. Endpoint state/control implementation still needs geometry-specific
verification; this change does not magically certify full 64K shared-fetch RTL.

Version 3 validation and the gated six-point 64K/128K rerun are isolated under
`results/shared_fetch_cir_matched_v3_20260907/`. Inspect its `status.json` and
individual differential reports before accepting results. Original active
campaign snapshots and the v1 entry point are not modified.

All nine v3 small full-wrapper VCS comparisons passed (1/4/16 canonical
jobs per source per epoch, stalls 0/7/13, two arithmetic epochs). They check
total cycles, per-core debug edges, transfers, memory, state reads and next
states. The six-point rerun is active/queued after this gate. A separate
unit test checks four simultaneous H0 deliveries to distinct cores, but the
small VCS fixture is not full 64K RTL nor a general high-fanout certificate.

A subsequent parameterized single-H1 testbench,
`tb/shared_fetch_single_h1_tb.sv`, instantiated all 2048 endpoint cores for
the 65536-spin toroidal case (8 H0, 256 cores/H0, 4/2/4 lanes). Both VCS
and the v3 model report 67587 initialization, 16849 iteration and 84436 total
cycles. Full recorded core, memory-request/response, state-access and local
router transfer traces match. The preceding 256-spin smoke also passed.
Evidence is under `results/full64k_shared_fetch_rtl_20260907_v1/`; reproduce
with `scripts/start_single_h1_validation.py --output results/<unique-name>`.
This is one-iteration timing-only coverage of one H1 and this specific graph,
not numerical arithmetic validation or multi-H1 coverage.

## Historical single-slot implementation

This is an opt-in, versioned alternative to the directed-fetch cores-only
baseline. Existing `cli`/`fast_cli` commands and frozen campaigns are unchanged.

## Mechanism

Each canonical unordered off-diagonal block is fetched once from the same
H0/H1/cross owner used by CIR. A source-lane replay register buffer captures
32 rows, then sends two complete unicast packets/streams, in canonical and
reverse destination order. Endpoint cores perform normal/transposed arithmetic
and accumulate their own contributions. The hierarchy performs no interaction
arithmetic. Read acceptance and completion are modeled by live Ramulator.

The conservative first implementation adds one 1KiB register buffer per source
lane to the existing two-slot DRAM reassembly storage. A buffer is retained
until both copies have been accepted downstream. Filling and replay do not
overlap within that buffer. Sources retain their existing arbitration, delivery
bandwidth, and NoC backpressure. There is no multicast assumption. This halves
canonical weight reads relative to directed fetches, not endpoint weight bytes.
At nominal4/2/4 provisioning,38 source lanes/H1 add38KiB of replay registers/H1.
This is a bounded, implementable baseline, not an optimized multicast design.

## Entry point

Use the usual exact-event arguments with
`python3 -m azilla_cycle_model.shared_fetch_cli simulate-exact-events
--execution-mode cores-only ...` (or the mapped counterpart).
Set `PYTHONPATH=model`. This entry point deliberately does not enable fast-model
optimizations; their interaction with the new path needs separate checks.
The output pipeline contract identifies `single_fetch_two_unicast_v1` and the
experimental controller. Do not merge it with directed-fetch results under an
ambiguous cores-only label. Nonzero package-local latency remains unsupported.

## Validation established September 7

- PairReplay versus `shared_fetch_replay.sv`:2,200 VCS edges, command/weight
  backpressure, repeated jobs and mid-activity reset.
- Nine integrated Python cases:1/2/4 lanes, repeated epochs, mixed hierarchy
  work, local stalls and serialized links; request/delivery conservation.
- Integrated256-spin VCS:2H1×2H0×2cores,4/2/4 source lanes, mixed nonzero
  weights/states, normal/transposed endpoint arithmetic, state publication,
  real routers and Ramulator; two full arithmetic epochs at stall0/7/13.
  Independent Python total cycles, every core debug edge, core events, memory
  requests/responses, state accesses and final states match exactly.
  Total cycles717/1425/865 for stalls0/7/13 respectively.
- The fixture contains one canonical block per hierarchy source per epoch;
  it does not establish full-geometry multi-engine saturation or arbitrary
  large-graph RTL coverage. Large timing points remain projections.

Reproduce the full fixture with `bash scripts/run_shared_fetch_publication_vcs.sh`;
use ENGINES=4,H0_ENGINES=4,H1_ENGINES=2,CROSS_ENGINES=4 and a unique OUTPUT_ROOT.
The default invocation otherwise uses one lane. The dedicated arithmetic checker
requires exactly32 accepted reads per canonical block and64 delivered rows for
the two directions. The old checker remains unchanged and correctly rejects
the new read count. Failed setup attempts are retained locally.

## SRAM candidate and limits

`j_block_sram_single_rw_candidate.sv` implements two independent single-RW slot
arrays and two row-zero shadow registers (64bytes/core at256-bit rows). It
preserves the tested final-load/row-zero-read overlap without an extra cycle.
Same-slot overlaps at other rows fail an assertion rather than being serialized
silently. Separate state-bank guards reject simultaneous same-bank publication
writes and operand reads; this is a restricted phase contract, not a replacement
for the general1R1W component under arbitrary external traffic.

The candidate passed the same three full shared-fetch VCS/Python differentials
with identical717/1425/865 cycle totals and arithmetic. At2,048cores/H1, row-zero
shadow storage adds128KiB/H1. Cost this explicitly; it is not free SRAM area.

This closes a demonstrated logical overlap in the covered core fixture. It
does NOT bind actual GF12 macro instances, establish SRAM-inclusive timing/area,
or prove phase separation for every hierarchy configuration. Production RTL
remains unchanged. Wider CIR/core access audits, macro pin/polarity/output
register integration and resource updates remain necessary before physical
closure claims. No PDK files are copied or committed.

## Queued evaluation

`scripts/run_shared_fetch_followups.py` freezes a separate source snapshot and
requires six passing full-wrapper reports before preparation. It runs six
64K/128K shared-fetch points (three families);128K waits for the original
12-point minimum matrix. A separate two-worker queue waits for that matrix
then runs four128K constrained-link points on community/uniform, both original
modes. These link results do not validate shared-fetch link sensitivity.

`scripts/finalize_minimum_matrix_figures.py` waits for all12 original audits,
rehashes source outputs and generates matched latency/communication plots and
a CSV. Visual review and claim/fidelity review remain manual release steps.

Local evidence: `results/shared_fetch_validation_v1/` and
`results/shared_fetch_followups_v1/`. These paths are ignored generated outputs.
