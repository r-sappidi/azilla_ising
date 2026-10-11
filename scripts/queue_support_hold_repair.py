#!/usr/bin/env python3
"""Apply existing bounded hold ECO to completed support targets with setup margin."""
import hashlib,json,os,re,shutil,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'build/synthesis/gf12/private/support_v3_20260908'
OUT=ROOT/'build/synthesis/gf12/private/support_hold_v3_20260908'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):
    t=p.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)
def main():
    OUT.mkdir(exist_ok=False)
    script=OUT/'hold_eco.tcl'
    shutil.copy2(ROOT/'build/synthesis/gf12/private/balanced_mvm_candidate/hold_eco.tcl',script)
    script_hash=sha(script)
    for period in (10000,1000):
        for tag in ('replay','streamer2','streamer4'):
            name=f'{tag}_{period}ps';source=BASE/name
            while not (source/'status.txt').exists():
                d=json.loads((BASE/'status.json').read_text())
                if d['status']=='failed':raise RuntimeError(d)
                save(OUT/'status.json',dict(status='waiting',target=name));time.sleep(60)
            data=(source/'status.txt').read_text()
            setup=float(re.search(r'setup_slack_ps\s+([-+\d.eE]+)',data)[1])
            hold=float(re.search(r'hold_slack_ps\s+([-+\d.eE]+)',data)[1])
            case=OUT/name;case.mkdir()
            if setup<0 or hold>=0:
                save(case/'audit.json',dict(status='setup-failure-needs-review' if setup<0 else 'already-setup-hold-met',setup=setup,hold=hold));continue
            ddc=source/'mapped.ddc';digest=sha(ddc)
            save(OUT/'status.json',dict(status='repairing-hold',target=name))
            env=dict(os.environ,AZILLA_OUT=str(case),AZILLA_INPUT_DDC=str(ddc),
                AZILLA_GF12_DB=json.loads((BASE/'manifest.json').read_text())['library_read_in_place'],LC_ALL='C')
            with (case/'dc.log').open('x') as log:
                subprocess.run(['/usr/bin/time','-v','-o',str(case/'wall.time'),
                    '/home/lab.apps/vlsiapps/design_compiler/Q-2019.12-SP3/bin/dc_shell','-f',str(script)],
                    cwd=case,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            assert sha(script)==script_hash and sha(ddc)==digest
            assert (case/'status.txt').exists()
            save(case/'audit.json',dict(status=(case/'status.txt').read_text().strip(),input_ddc_sha256=digest,
                script_sha256=script_hash,structural_equivalence_review_required=True,physical_closure=False))
    save(OUT/'status.json',dict(status='bounded-repairs-finished',physical_closure=False))
if __name__=='__main__':
    try:main()
    except Exception as e:
        if OUT.exists():save(OUT/'failure.json',dict(error=repr(e)))
        raise
