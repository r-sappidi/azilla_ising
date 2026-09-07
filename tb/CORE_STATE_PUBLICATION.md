# Cores-only frozen-state publication differential

Run `bash scripts/run_core_state_publication_vcs.sh` from the repository root.
The runner saves a VCS build, log, wall-time logs, and a hashed comparison report
under a unique output directory (override `OUTPUT_ROOT`). It does not certify an entire
Ising iteration or a large mesh.

The bounded fixture uses two H1 nodes, two H0s per H1, and two 32-spin cores per
H0. Its three unordered interaction blocks are (0,1), (0,2), and (0,4). Two
epochs publish different nonzero state words. Real FlooNoC routers transport the
remote words and real eight-bank synchronous state SRAMs hold the H0 operands.

The explicit phase contract is:

1. Gather one resident core state per H1 per cycle (four cycles here).
2. Release required remote publications at relative cycles 0, 2, ... and drain
   the actual network, including four quiet cycles (nine cycles here).
3. Fill only required H0 operand-cache entries through one shared write per H1
   per cycle (five cycles here). A word needed by two H0s consumes two writes;
   no free multicast or global cache preload is assumed.

Off-diagonal core work cannot start until all three phases finish. The local
diagonal MVM can independently overlap publication. This fixture verifies the
publication boundary, not that arithmetic overlap.

`model/azilla_cycle_model/core_publication.py` implements the reusable phase
without RTL timestamps as inputs. A supplied quiescent `Mesh` is preserved,
including arbitration history, across epochs and compute phases. The checker
compares accepted injection/hop/ejection cycles, actual state payloads, gather
and cache-write cycles, phase durations, and synchronous cache-read results.
Additional topology/contention coverage remains necessary beyond this bounded
two-node fixture. Full-iteration integration has its own separate validation.
