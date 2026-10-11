# Repository layout and maintenance

## Directory responsibilities

| Path | Contents | Retention |
|---|---|---|
| `rtl/`, `tb/`, `dv/` | Hardware, testbenches and fixtures | Preserve source and input datasets |
| `model/` | Models, tests and mapping integration | Preserve behavior and regression oracles |
| `scripts/` | Stable workflow entry points, indexed in its README | Keep paths used by campaigns |
| `docs/` | Architecture, contracts and methodology | Start with its README |
| `paper/`, `acmart-primary/` | Manuscript and template | Distinct user work, not build products |
| `msim_matlab/`, `compression_mapping/` | Numerical and mapping references | Preserve independent implementations |
| `third_party/`, `rtl/FlooNoC/` | External dependencies | Preserve pins and local modifications |
| `build/` | Compiler products, tools and private synthesis | Not all disposable; inspect dependencies |
| `results/` | Frozen sources, inputs, manifests, audits and traces | Preserve research evidence, including negative results |
| `logs/`, `plots/`, `mappings/`, `traces/` | Run-specific outputs | Retain provenance and reproduction paths |

`AGENTS.md` and `LOCAL_RESEARCH_STATUS.md` are private and ignored. Never
commit vendor PDKs, licenses, macro models or proprietary derived files.

## Safe build-cache cleanup

The utility defaults to a dry run. It accepts explicit immediate children of
`build/` named `ising_mesh_floo_*` with Verilator makefile markers. Only `.gch`,
`.o`, `.a` and `.d` compiler products older than seven days are removable.
Tracked files and symlinks are excluded. It checks the user's running/stopped
processes and retains generated C++, headers, executables and results.
It does not protect other users' processes; do not use it on shared builds.

```bash
python3 scripts/cleanup_build_cache.py --build-dir <exact-legacy-directory-name>
# After reviewing the dry run, with a new manifest and an existing parent:
python3 scripts/cleanup_build_cache.py --build-dir <exact-legacy-directory-name> \
  --apply --manifest results/<unique-cleanup-directory>/removed.jsonl
```

Do not schedule builds into the selected directories during cleanup. The audit
records each intended removal before unlinking, so an interrupted attempt may
include a final not-yet-removed entry. Check disk before treating it as complete.
Deleted caches are regenerable, but their original bytes are not recoverable
from the manifest. Historical reuse may require a clean Verilator rebuild.

Never apply blanket cleanup to VCS libraries, tools, Ramulator binaries,
source snapshots, raw measurements or PDK-dependent synthesis artifacts.

## Retention and migration

- Archive loose diagnostics with original/new paths rather than deleting evidence.
- Age, names and unfavorable results do not establish obsolescence. Use baseline
  versions, source hashes, dependencies and audits to determine validity.
- Do not relabel buffered measurements as streaming results.
- Source moves require coordinated changes to imports, Makefiles, documentation
  and launchers, plus compatibility for frozen manifests and queued campaigns.
- Some live wrappers use scripts under `build/` and snapshots under `results/`.
  Neither directory is safe for wholesale deletion.
- Root `internal_stats.csv` remains an input to an old archive watcher. Preserve
  it until that watcher is explicitly retired; new runs need unique output paths.

## October 5 cleanup

Details, cache-removal manifests and recoverable archives are under
`results/repository_cleanup_20261005/`. Source paths, experiment evidence,
workers and frozen snapshots were retained. Documentation indexes organize
entry points without moving scripts referenced by existing runs.
