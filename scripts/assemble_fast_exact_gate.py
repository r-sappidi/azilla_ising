#!/usr/bin/env python3
"""Assemble a strict optimization gate; incomplete evidence returns exit 2."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys
from run_fast_exact_continuation import digest, save, signature_key


class Pending(Exception):
    pass


def hashes(model):
    return {str(p.relative_to(model)):digest(p) for p in sorted(model.rglob('*.py'))}


def read(path):
    if not path.exists():
        raise Pending(str(path))
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        raise Pending('writer may be updating: ' + str(path))


def full_report(directory, expected_names, oracle_hashes, optimized_hashes, evidence):
    path=directory/'report.json'; report=read(path)
    if report['status'] in ('failed','incomplete') or any(c['status'] in ('mismatch','execution-failure','missing-dataset') for c in report['cases']):
        raise RuntimeError('failed full differential: '+str(directory))
    if report['status']!='passed':raise Pending(str(directory))
    if {c['case']['name'] for c in report['cases']}!=set(expected_names) or len(report['cases'])!=len(expected_names):
        raise RuntimeError('wrong full differential case set')
    assert report['oracle_sha256']==oracle_hashes and report['optimized_sha256']==optimized_hashes
    assert hashes(Path(report['oracle']))==oracle_hashes and hashes(Path(report['optimized']))==optimized_hashes
    evidence.add(path)
    for item in report['cases']:
        assert item['status']=='pass' and len(item['execution'])==2
        assert all(e['returncode']==0 for e in item['execution'])
        for source,sha in item['input_sha256'].items():
            assert digest(source)==sha
            evidence.add(Path(source))
        assert {a['name'] for a in item['compared_artifacts']}=={'metrics_summary.json','metrics_noc.csv','metrics_nodes.csv','metrics_dram.csv','transfers.csv'}
        for a in item['compared_artifacts']:
            for mode in ('oracle','optimized'):
                artifact=directory/item['case']['name']/mode/a['name']
                assert artifact.stat().st_size==a['bytes'] and digest(artifact)==a['sha256']
                evidence.add(artifact)


def assemble(args):
    oracle=Path(args.oracle).resolve();optimized=Path(args.optimized).resolve()
    oh=hashes(oracle/'model');fh=hashes(optimized/'model')
    evidence=set()
    names=[n+'_'+mode for n in ('single_sparse','single_dense','two_uneven','four_conflict','sixteen_sparse','two_dense','kings16k','torus32k','torus64k') for mode in ('cir','cores-only')]
    full_report(args.full,names,oh,fh,evidence)
    full_report(args.mapped,['mapped_torus64k_cir','mapped_torus64k_cores-only'],oh,fh,evidence)
    full_report(args.refresh0,['kings16k_cir','kings16k_cores-only'],oh,fh,evidence)
    for item in read(args.refresh0/'report.json')['cases']:
        for execution in item['execution']:
            command=execution['command']
            assert '--idle-refresh-period-ticks' in command
            assert command[command.index('--idle-refresh-period-ticks')+1]=='0'
    component=read(args.components)
    assert component['status']=='pass' and component['tests_run']==13
    assert component['failures']==component['errors']==component['skipped']==0
    assert component['source_sha256']==fh and component['sources_unchanged']
    evidence.add(args.components)
    wm=read(args.windows/'manifest.json'); envelope=read(Path(wm['envelope']))
    signatures=envelope['required_signatures']
    assert len(signatures)==78 and wm['indices']==list(range(78)) and wm['cycles']>=2000
    assert wm['oracle_hashes']==oh and wm['optimized_hashes']==fh
    assert digest(args.library)==wm['library_sha256']
    evidence.update((args.windows/'manifest.json',Path(wm['envelope']),args.library))
    summary=read(args.windows/'summary.json')
    if any(c['status']=='fail' for c in summary):raise RuntimeError('failed window comparison')
    if len(summary)<78:raise Pending(f'windows {len(summary)}/78')
    assert len(summary)==78 and {c['index'] for c in summary}==set(range(78))
    evidence.add(args.windows/'summary.json')
    for index,signature in enumerate(signatures):
        case=args.windows/f'case_{index:03d}'
        comparison=read(case/'comparison.json');ref=read(case/'reference/summary.json');fast=read(case/'optimized/summary.json')
        assert comparison==next(c for c in summary if c['index']==index)
        assert comparison['status']=='pass' and all(v is True for v in comparison['checks'].values())
        assert signature_key(comparison['signature'])==signature_key(signature)
        assert digest(signature['ramulator_config'])==signature['ramulator_config_sha256']
        evidence.add(Path(signature['ramulator_config']))
        for key in ('status','clock','active_window_cycles','final_sha256','event_counts','event_sha256'):
            assert ref[key]==fast[key]
        for label,record,expected in [('reference',ref,oh),('optimized',fast,fh)]:
            assert record['source_sha256']==expected and record['sources_unchanged']
            assert min(record['dram_accepted'],record['dram_completed'],record['completed_compute_jobs'])>0
            assert record['status']=='complete' or record['active_window_cycles']>=2000
            levels=['h0','h1']+(['cross'] if signature['geometry']['mesh_x']*signature['geometry']['mesh_y']>1 else [])
            assert all(record['level_accepted'][l]>0 for l in levels)
            # Rehash decompressed evidence rather than trusting only summaries.
            for event,sha in record['event_sha256'].items():
                trace=case/label/(event+'.jsonl.gz')
                h=hashlib.sha256()
                with gzip.open(trace,'rb') as f:
                    for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
                assert h.hexdigest()==sha
                evidence.add(trace)
            final=case/label/'final_state.json.gz'
            with gzip.open(final,'rb') as f:assert hashlib.sha256(f.read()).hexdigest()==record['final_sha256']
            evidence.update((final,case/label/'summary.json'))
        evidence.update((case/'comparison.json',case/'job.json',case/'dataset.txt'))
    return dict(status='pass',baseline_snapshot=str(oracle),optimized_snapshot=str(optimized),
        baseline_files={'model/'+p:h for p,h in oh.items()},optimized_files={'model/'+p:h for p,h in fh.items()},
        covered_signatures=signatures,evidence=[dict(path=str(p.resolve()),sha256=digest(p)) for p in sorted(evidence)],
        counts=dict(full=18,mapped=2,refresh0=2,windows=78,components=13),
        scope='Exact optimization equivalence within this configuration envelope; no additional RTL coverage or paper-ready promotion.')


def main():
    if not __debug__:raise RuntimeError('assertions must be enabled')
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('oracle','optimized','full','mapped','refresh0','windows','components','library','output'):
        ap.add_argument('--'+name,type=Path,required=True)
    args=ap.parse_args()
    if args.output.exists():ap.error('refusing to overwrite gate')
    try:gate=assemble(args)
    except Pending as error:
        print('PENDING',error,flush=True);return 2
    save(args.output,gate);print('PASS gate assembled',args.output)
    return 0


if __name__=='__main__':sys.exit(main())
