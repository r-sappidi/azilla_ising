#!/usr/bin/env python3
"""Audit completed component reports without treating partial area as chip area."""
import hashlib,json,re,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'build/synthesis/gf12/private/support_v3_20260908'
OUT=ROOT/'results/support_synthesis_audit_20260908'
def save(p,d):
    t=p.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)
def main():
    OUT.mkdir(exist_ok=False)
    while True:
        d=json.loads((SRC/'status.json').read_text())
        if d['status']=='failed':raise RuntimeError(d)
        rows=[]
        for p in SRC.glob('*/status.txt'):
            text=p.read_text();s=re.search(r'setup_slack_ps\s+([-+\d.eE]+)',text);h=re.search(r'hold_slack_ps\s+([-+\d.eE]+)',text)
            a=re.search(r'Total cell area:\s*([-+\d.eE]+)',(p.parent/'area.txt').read_text())
            assert s and h and a,'unrecognized timing/area report'
            rows.append(dict(target=p.parent.name,setup_slack_ps=float(s[1]),hold_slack_ps=float(h[1]),
                area_um2=float(a[1]),setup_hold_met=float(s[1])>=0 and float(h[1])>=0,
                reports={str(q):hashlib.sha256(q.read_bytes()).hexdigest() for q in p.parent.iterdir() if q.suffix=='.txt'}))
        save(OUT/'status.json',dict(status='waiting' if d['status']=='running' else d['status'],components=rows,
            full_area_power_verified=False,DRC_and_physical_review_required=True))
        if d['status']=='reports-complete':
            manifest=json.loads((SRC/'manifest.json').read_text())
            assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in manifest['sources'].items())
            assert len(rows)==6
            save(OUT/'audit.json',dict(status='reports-parsed',components=rows,source_guards_pass=True,
                scope='register-mapped payload and support logic only; no physical closure'))
            break
        time.sleep(60)
if __name__=='__main__':
    try:main()
    except Exception as e:
        if OUT.exists():save(OUT/'failure.json',dict(error=repr(e)))
        raise
