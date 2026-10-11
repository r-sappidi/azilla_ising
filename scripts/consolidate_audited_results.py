#!/usr/bin/env python3
"""Build a private, source-grounded Sheets payload; never exports PDK material."""
import collections
import hashlib
import json
import math
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/consolidated_audited_20261002'
POLICY='single_fetch_two_slot_cir_routes_v3'
tabs={}; sources={}; excluded=[]
def rel(p):
    p=Path(p).resolve()
    return str(p.relative_to(ROOT))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):
    p=Path(p);sources[rel(p)]=sha(p);return json.loads(p.read_text())
def resolve(p):return Path(p) if Path(p).is_absolute() else ROOT/p
def table(name,headers):tabs[name]=[headers];return tabs[name]
def verified_files(d):
    for f,h in d.get('files',{}).items():
        p=resolve(f)
        if p.is_file():
            if sha(p)!=h:raise ValueError('changed evidence '+str(p))
        else:raise ValueError('missing evidence '+str(p))
def gm(v):return math.exp(sum(map(math.log,v))/len(v))

def main():
    OUT.mkdir(exist_ok=True)
    runs=[]
    for campaign in ['cir_matched_all_campaigns_v3_active','mapping_shared_fetch_v3_20260908']:
        root=ROOT/'results'/campaign
        plan=read(root/'plan.json')
        for item in plan['points']:
            case=root/'points'/item['campaign']/item['relative'];audit=case/'audit.json'
            if not audit.exists():continue
            try:
                a=read(audit);verified_files(a)
                assert a.get('status') in ['conservation-pass','adopted-unchanged-cir']
                p=case/'metrics_summary.json'
                if not p.exists():
                    paths=[resolve(x) for x in a.get('files',{}) if x.endswith('_summary.json')]
                    if paths:p=paths[0]
                    elif a.get('adopted_from'):p=resolve(a['adopted_from'])/'metrics_summary.json'
                d=read(p);mode=item['mode']
                if mode=='cores-only':assert d['cores_only_ablation']['pipeline_contract']['fetch_policy']==POLICY
                g=item['geometry'];assert all(d['geometry'][k]==v for k,v in g.items())
                assert sha(resolve(item['dataset']))==item['dataset_sha256']
                mapping=item['relative'].split('/')[-2] if campaign.startswith('mapping') else 'baseline'
                dataset=item['relative'].split('/')[0]
                run=dict(dataset=dataset,mode=mode,mapping=mapping,group='Mapping' if campaign.startswith('mapping') else 'Sparse',
                    summary=d,path=p,audit=audit,geometry=g,engines=item['engines'],dataset_sha=item['dataset_sha256'],
                    memory_sha=item['ramulator_config_sha256'],spins=d['geometry']['spin_count'])
                runs.append(run)
            except Exception as e:excluded.append([str(case.relative_to(ROOT)),str(e)])
    dense=ROOT/'results/matched_dense_exact_v3_20260907'
    for audit in sorted(dense.glob('points/*/*/implicit/audit.json')):
        a=read(audit);verified_files(a)
        if a.get('status')!='conservation-pass':continue
        p=audit.parent/'metrics_summary.json';d=read(p);mode=a['mode']
        if mode=='cores-only':assert d['cores_only_ablation']['pipeline_contract']['fetch_policy']==POLICY
        runs.append(dict(dataset='dense_'+str(a['problem_spins']),mode=mode,mapping='implicit-dense',group='Dense',
            summary=d,path=p,audit=audit,geometry=d['geometry'],engines=[4,2,4],spins=a['problem_spins'],
            dataset_sha='implicit occupancy; see encoding gate',memory_sha='frozen dense campaign'))

    raw=table('Run inventory',['Run ID','Group','Dataset','Mapping','Mode','Problem spins','Spin cores capacity','H1 mesh','H0 per H1','Cores per H0','H0 H1 cross engines','Init cycles','Iteration cycles','Total cycles','Evidence tier','Fetch policy','Dataset SHA256','Memory config SHA256','Summary path','Summary SHA256','Audit path'])
    traffic=table('Traffic and DRAM',['Run ID','Canonical block reads','Scheduled jobs (mode-specific)','Injected flits','Physical link flits','State flits','Partial flits','Other flits','Average hops','Injection stalls','Ejection stalls','Link stalls','DRAM requests','DRAM completions','DRAM rejections','Mean request latency cycles','Max request latency cycles','Summary path'])
    paired=collections.defaultdict(dict)
    for idx,r in enumerate(sorted(runs,key=lambda x:(x['group'],x['spins'],x['dataset'],x['mapping'],x['mode'])),1):
        base=re.sub(r'_n(\d+)',r'_\1',r['dataset'])
        mode_label='endpoint_only' if r['mode']=='cores-only' else r['mode']
        r['id']='_'.join([base]+([] if r['group']=='Dense' else [r['mapping']])+[mode_label])
        d=r['summary'];g=r['geometry'];t=d['timing_cycles'];n=d['noc'];dr=d.get('dram_systems',[])
        raw.append([r['id'],r['group'],r['dataset'],r['mapping'],r['mode'],r['spins'],g['mesh_x']*g['mesh_y']*g['h0_per_h1']*g['cores_per_h0'],f"{g['mesh_x']}x{g['mesh_y']}",g['h0_per_h1'],g['cores_per_h0'],'/'.join(map(str,r['engines'])),t['initialization'],t['iteration'],t['total'],'Qualified exact-event projection; not full-geometry RTL certified',POLICY if r['mode']=='cores-only' else 'CIR',r['dataset_sha'],r['memory_sha'],rel(r['path']),sha(r['path']),rel(r['audit'])])
        req=sum(x['accepted_requests'] for x in dr);comp=sum(x['completed_requests'] for x in dr);reject=sum(x['rejected_requests'] for x in dr)
        latency=sum(x['average_latency_cycles']*x['completed_requests'] for x in dr)/comp if comp else 0
        traffic.append([r['id'],req/32,d['scheduled_jobs']['total'],*[n.get(k,0) for k in ['injected_flits','physical_link_flits','state_flits','partial_flits','other_flits','average_hops','injection_stalls','ejection_stalls','link_stalls']],req,comp,reject,latency,max((x['maximum_latency_cycles'] for x in dr),default=0),rel(r['path'])])
        paired[r['group'],r['dataset'],r['mapping']][r['mode']]=r
    pairtabs={k:table(k+' pairs',['Dataset','Spins','Spin cores','Mapping','CIR iteration cycles','Endpoint iteration cycles','Iteration speedup','CIR total cycles','Endpoint total cycles','Total speedup','CIR run ID','Endpoint run ID','Matched geometry and memory','Interpretation']) for k in ['Sparse','Dense','Mapping']}
    pairs=[]
    for (group,dataset,mapping),m in sorted(paired.items()):
        if set(m)!={'cir','cores-only'}:continue
        c,e=m['cir'],m['cores-only'];assert c['geometry']==e['geometry'] and c['engines']==e['engines'] and c['dataset_sha']==e['dataset_sha'] and c['memory_sha']==e['memory_sha']
        ct,et=c['summary']['timing_cycles'],e['summary']['timing_cycles'];speed=et['iteration']/ct['iteration'];total=et['total']/ct['total']
        pairtabs[group].append([dataset,c['spins'],c['summary']['geometry']['spin_count']//32,mapping,ct['iteration'],et['iteration'],speed,ct['total'],et['total'],total,c['id'],e['id'],'Yes; engines and source dataset matched','Simulation result; no full-size RTL certification'])
        pairs.append(dict(group=group,dataset=dataset,spins=c['spins'],speed=speed,total=total))

    impact=table('Mapping impact',['Dataset','Mode','Mapping','Identity iteration cycles','Mapped iteration cycles','Mapped divided by identity latency','Identity block reads','Mapped block reads','Identity physical flits','Mapped physical flits','Interpretation'])
    for (group,dataset,mapping),m in sorted(paired.items()):
        if group!='Mapping' or mapping=='identity':continue
        original=paired.get((group,dataset,'identity'),{})
        for mode,r in m.items():
            if mode not in original:continue
            a,b=original[mode]['summary'],r['summary'];x=a['timing_cycles']['iteration'];y=b['timing_cycles']['iteration']
            impact.append([dataset,mode,mapping,x,y,y/x,sum(d['accepted_requests'] for d in a['dram_systems'])/32,sum(d['accepted_requests'] for d in b['dram_systems'])/32,a['noc']['physical_link_flits'],b['noc']['physical_link_flits'],'Ratio >1 means mapping is slower; do not confuse with CIR-vs-core speedup'])

    validation=table('RTL validation',['Case','Mode','Result','Geometry or scope','Init cycles','Iteration cycles','Total cycles','Checks passed','Coverage limitation','Evidence path','SHA256'])
    coverage=read(ROOT/'results/paper_completion_20260908/coverage.json')
    candidates=[(x['case'],x['mode'],resolve(x['evidence']),x['scope']) for x in coverage['rows']]
    for directory in ['mesh_accuracy_20260921','mesh4x2_accuracy_20260922']:
        for p in (ROOT/'results'/directory).rglob('differential.json'):
            if 'snapshot' in str(p):continue
            candidates.append((str(p.relative_to(ROOT/'results'/directory).parent),'cir' if '/cir/' in str(p) else 'cores-only',p,'small multi-H1 timing'))
    seen=set()
    for label,mode,p,scope in candidates:
        if p in seen:continue
        seen.add(p);d=read(p)
        if d.get('status')!='pass' or not all(d['checks'].values()):excluded.append([rel(p),'differential did not pass']);continue
        t=d.get('observed_timing',[d.get('initialization_cycles',''),d.get('iteration_cycles',''),d.get('model_total','')])
        validation.append([label,mode,'PASS',json.dumps(d.get('geometry',scope)),*t,', '.join(d['checks']),'Only recorded events / exact tested geometry; timing only',rel(p),sha(p)])

    arithmetic=table('Arithmetic validation',['Case','Status','Checked state bits','Iterations','Directed jobs','DRAM requests','Graph or repeated stress','Scope','Evidence path','SHA256'])
    for p in sorted((ROOT/'results/shared_fetch_cir_matched_v3_20260907').glob('shared_repeat*/*.arithmetic.json')):
        d=read(p);assert d['status']=='PASS'
        log=p.with_name(p.name.replace('.arithmetic.json','.log'))
        if log.exists():assert sha(log)==d['rtl_log_sha256']
        arithmetic.append([str(p.relative_to(ROOT/'results/shared_fetch_cir_matched_v3_20260907')),d['status'],d['checked_next_state_bits'],d['iterations'],d['directed_jobs'],d['dram_requests'],'graph' if d['graph_case'] else 'repeated-block stress','256 spins; independent signed-field reference; limited coefficients/fixture, not large-size numerical certification',rel(p),sha(p)])

    refs={'gset_g21':931,'gset_g32':1410,'gset_g35':7687,'gset_g37':7691,'gset_g47':6657,'gset_g70':9594,'gset_g81':14060}
    qroot=ROOT/'results/retuned_quality_v2_20260908';manifest=read(qroot/'manifest.json');verified_files(manifest)
    assert read(qroot/'complete.json')['checks']=='pass'
    assert read(qroot/'figure_audit.json')['independent_saved_state_objectives']
    trials=read(qroot/'results.json');assert len(trials)==172
    datasets={d['name']:d for d in manifest['datasets']}
    qt=table('Quality fixed trials',['Dataset','Family','Allocated spins','Solver seed','Updates','Best cut','Best iteration','Final cut','Local search cut','Best divided by local search','Declared published best known','Best divided by best known','Independent objective check','Scope','Evidence path'])
    qs=table('Quality fixed summary',['Dataset','Family','Allocated spins','Trials','Updates per trial','Best of trials','Mean best cut','Mean paired local-search ratio','Declared best known','Best divided by best known','Schedule','Scope','Evidence path'])
    for t in trials:
        assert t['independent_best_final_objective_pass'];d=datasets[t['dataset']];ref=refs.get(t['dataset'],'')
        qt.append([t['dataset'],d['family'],t['spins'],t['seed'],t['iterations'],t['best_cut'],t['best_iteration'],t['final_cut'],t['local_search_cut'],t['ratio_to_local_search'],ref,t['best_cut']/ref if ref else '',True,d['scope']+'; software reference, not RTL solver result',rel(qroot/'results.json')])
    for name,d in datasets.items():
        tt=[t for t in trials if t['dataset']==name];assert len(tt)==4;ref=refs.get(name,'');best=max(x['best_cut'] for x in tt)
        qs.append([name,d['family'],d['spins'],len(tt),1000,best,sum(x['best_cut'] for x in tt)/len(tt),sum(x['ratio_to_local_search'] for x in tt)/len(tt),ref,best/ref if ref else '', 'a16 b1 noise32 linear',d['scope']+'; best-known not proven optimum',rel(qroot/'results.json')])
    tuned=table('Quality tuned evaluation',['Campaign','Dataset','Solver seed','Updates','Best cut','Final cut','Declared best known','Best divided by best known','Parameters','Independent objective audit','Scope','Evidence path'])
    for root in ['gset_instance_tuning_20260908','gset_robust_tuning_20260908']:
        for p in sorted((ROOT/'results'/root/'evaluation').rglob('audit.json')):
            d=read(p)
            assert d['saved_objective_audit']=='pass' and d['independent_best_final_objective_pass']
            tuned.append([root,d['dataset'],d['seed'],d['iterations'],d['best_cut'],d['final_cut'],d['reference_cut'],d['reference_ratio'],json.dumps(d['parameters']),True,'Per-instance tuned; not global schedule or new-graph holdout; software reference',rel(p)])

    overview=table('Read me',['Topic','Result or rule','Qualification'])
    overview.extend([
        ['Snapshot','2026-10-02; static audited snapshot','Not automatically refreshed; ongoing/incomplete points excluded from pair tables'],
        ['Evidence tiers','RTL validation vs qualified exact-event projections vs independent software solution quality','These are different forms of evidence; do not merge into a blanket verified claim'],
        ['Primary endpoint baseline',POLICY,'Legacy cores-only and calibrated models excluded'],
        ['Speedup definition','Endpoint-only cycles / CIR cycles','Iteration excludes initialization; total includes initialization plus one iteration'],
        ['Simulation meaning','Live Ramulator exact-event timing','Not silicon performance; GF12 feasibility and physical links remain separate'],
        ['Validation scope',f'{len(validation)-1} passing recorded differentials included','Passing small multi-H1 tests does not certify nominal 128K/256K full geometry'],
        ['Traffic accounting','Canonical block reads = DRAM requests / 32 for these runs','Core scheduled jobs are directed; CIR jobs are unordered. Do not compare those totals as equal work'],
        ['Solution quality','172 fixed-schedule trials, 43 graphs; tuned evaluation kept separate','Software reference, not hardware time-to-solution; synthetic ratios use simple local search'],
        ['Gset denominator','Declared published best-known cuts used by archived campaign','Not a claim of mathematical optimality or latest literature record'],
        ['Source paths','Repository-relative paths refer to /home/rsappidi/azilla_ising','Artifacts remain local; these paths are provenance, not public downloads'],
        ['Physical data','No PDK files or restricted macro/library contents uploaded','Failed GF12 run is not an accepted area/timing result'],
        ['Uncertainty','Large-geometry projections remain coverage-qualified','Fair endpoint provisioning and broader RTL validation remain open'],
    ])
    for size in [65536,131072,262144]:
        v=[p for p in pairs if p['group']=='Sparse' and p['spins']==size and (p['dataset'].endswith('_s1') or p['dataset'].startswith('toroidal'))]
        if len(v)==3:overview.append([f'{size} sparse three-family GM',f"{gm([x['speed'] for x in v]):.6f}x iteration; {gm([x['total'] for x in v]):.6f}x total",'Community, uniform, toroidal; one matched instance each; qualified projection'])
    dense128=next(p for p in pairs if p['group']=='Dense' and p['spins']==131072)
    overview.append(['128K dense',f"{dense128['speed']:.6f}x iteration; {dense128['total']:.6f}x total",'Separate from sparse geomean; qualified projection'])
    overview.append(['Gset reference sources','Archived denominators, not newly surveyed best-known records','https://leria-info.univ-angers.fr/~jinkao.hao/papers/MaHaoAOR2016.pdf ; https://leria-info.univ-angers.fr/~jinkao.hao/papers/GoudetGoeffonHaoCOR2024.pdf ; https://arxiv.org/pdf/2505.18508'])
    ex=table('Exclusions and gaps',['Item','Reason'])
    ex.extend(excluded+[
        ['Legacy/calibrated/hybrid campaigns','Superseded or not primary fidelity; excluded'],
        ['Failed 3x2 CIR validation','Unsupported power-of-two restriction; replaced by passing 4x2 check'],
        ['Incomplete pairs','Individual audited runs may appear in inventory; no speedup calculated until both finish'],
        ['GF12 full H0','Synthesis memory failure and physical-library assembly failure; no accepted full-H0 PnR result'],
        ['Memory-width sweep','Six certificates still missing; no new width-validation claim'],
        ['Large-size numerical correctness','Timing-only results do not prove 128K/256K arithmetic values'],
        ['Old solution-quality sweeps','Pre-alignment/prior schedule results excluded; current fixed schedule and audited tuned evaluation retained'],
        ['Tuning search trials','Training/coarse/refine exploration excluded from result tables; evaluation seeds only'],
        ['Physical link and dense analytical extrapolations','Not independently certified silicon/full-scale measurements; not counted as verified results'],
    ])
    prov=table('Source index',['Repository-relative source','SHA256'])
    prov.extend([list(x) for x in sorted(sources.items())])
    order=['Read me','RTL validation','Arithmetic validation','Sparse pairs','Dense pairs','Mapping pairs','Mapping impact','Run inventory','Traffic and DRAM','Quality fixed summary','Quality fixed trials','Quality tuned evaluation','Exclusions and gaps','Source index']
    for rows in tabs.values():
        rows[0]=[h.replace('Run ID','Run label').replace('run ID','run label') for h in rows[0]]
    result={k:tabs[k] for k in order}
    (OUT/'workbook_data.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:len(v)-1 for k,v in result.items()}));print('EXCLUDED',excluded)

if __name__=='__main__':main()
