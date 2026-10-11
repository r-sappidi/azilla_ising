#!/usr/bin/env python3
"""Copy immutable validation code into an ignored, uniquely named snapshot.

Only model source and the explicitly enumerated validation sources are copied.
Datasets, libraries, configurations, RTL traces and output evidence are passed
as absolute paths to the unchanged checker. No PDK files are included.
"""
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CHECKERS=('check_cir_exact_vcs_trace.py','check_exact_event_model.py',
          'check_16k_cycle_model.py')
TESTBENCH=('ising_mesh_tb.sv','ramulator_node_frontend.sv',
           'ramulator_dpi_bridge.sv','ramulator_dpi.cpp')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    destination=Path(sys.argv[1]).resolve()
    destination.relative_to((ROOT/'results').resolve())
    if destination.exists():
        raise SystemExit('refusing to overwrite an existing snapshot')
    files=sorted(p for p in (ROOT/'model').rglob('*')
                 if p.is_file() and '__pycache__' not in p.parts)
    files += [ROOT/'scripts'/name for name in CHECKERS]
    files += [ROOT/'tb'/name for name in TESTBENCH]
    for path in files:
        if path.is_symlink() or path.suffix.lower() in ('.lib','.lef','.gds','.db'):
            raise RuntimeError(f'unexpected non-source artifact: {path}')
    before={str(p.relative_to(ROOT)):digest(p) for p in files}
    for source in files:
        target=destination/source.relative_to(ROOT)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
        target.chmod(0o444)
    unchanged=all(digest(ROOT/name)==value and digest(destination/name)==value
                  for name,value in before.items())
    manifest=dict(status='ready' if unchanged else 'invalid-concurrent-source-change',
                  created_utc=datetime.now(timezone.utc).isoformat(),
                  original_root=str(ROOT), snapshot=str(destination),
                  files=before, command=sys.argv)
    (destination/'snapshot_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (destination/'snapshot_manifest.json').chmod(0o444)
    if not unchanged:
        raise SystemExit('source changed while copying; snapshot is not runnable')
    print(json.dumps(dict(status='ready',snapshot=str(destination),files=len(files))))

if __name__=='__main__':main()
