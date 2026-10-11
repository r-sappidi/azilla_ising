#!/usr/bin/env python3
"""Local model-source bundle and import/unit smoke; no PDK/binaries/results."""
import hashlib,json,os,shutil,subprocess,tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'results/shared_fetch_cir_matched_v3_20260907/snapshot'
OUT=ROOT/'results/model_artifact_draft_v3_20260908'
def save(p,d):
    t=p.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)
def main():
    OUT.mkdir(exist_ok=False);save(OUT/'status.json',dict(status='preparing'))
    src=OUT/'source';src.mkdir()
    shutil.copytree(BASE/'model',src/'model',ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.so','*.npz','*.npy'))
    (src/'tb/datasets').mkdir(parents=True)
    shutil.copy2(ROOT/'tb/datasets/g256_smoke.txt',src/'tb/datasets/g256_smoke.txt')
    (src/'scripts').mkdir()
    shutil.copy2(BASE/'scripts/check_paper_validation_gate.py',src/'scripts/check_paper_validation_gate.py')
    for name in ('requirements.txt','README.md','MAPPING_EXPERIMENTS.md'):
        if (BASE/name).exists():shutil.copy2(BASE/name,src/name)
        elif (ROOT/name).exists():shutil.copy2(ROOT/name,src/name)
    (src/'ARTIFACT_SCOPE.md').write_text('# Frozen model source draft\n\n'
        'Timing model source for corrected single_fetch_two_slot_cir_routes_v3. Use azilla_cycle_model.shared_fetch_double_cli for cores-only and azilla_cycle_model.cli for CIR.\n\n'
        'This is a local source/verification draft, not a complete paper artifact. The small256-spin unit-test fixture is included. Live Ramulator libraries, RTL toolchains, licensed PDKs, campaign inputs and generated results are excluded. Public reproduction still requires pinned external dependencies, downloadable workload recipes, exact experiment manifests and license review. Root README files describe the working repository, not standalone installation of this subset.\n')
    hashes={str(p.relative_to(src)):hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob('*') if p.is_file()}
    save(OUT/'manifest.json',dict(sources=hashes,source_snapshot=str(BASE),PDK_included=False,release_ready=False))
    save(OUT/'status.json',dict(status='unit-testing'))
    env=dict(os.environ,PYTHONPATH=str(src/'model'),PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    cmd=['/home/rsappidi/miniforge3/envs/azilla/bin/python3','-m','unittest','discover','-s','model/tests','-v']
    save(OUT/'command.json',cmd)
    with (OUT/'tests.log').open('x') as f:
        subprocess.run(['/usr/bin/time','-v','-o',str(OUT/'tests.time'),*cmd],cwd=src,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    assert all(hashlib.sha256((src/p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    with tarfile.open(OUT/'model-source-draft.tar.gz','w:gz') as tar:
        for p in sorted(hashes):tar.add(src/p,arcname='azilla-model/'+p,recursive=False)
    save(OUT/'status.json',dict(status='complete-source-smoke',release_ready=False))
if __name__=='__main__':
    try:main()
    except Exception as e:
        if OUT.exists():save(OUT/'status.json',dict(status='failed',error=repr(e)))
        raise
