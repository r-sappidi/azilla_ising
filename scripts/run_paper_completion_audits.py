#!/usr/bin/env python3
"""Read-only evidence collection; audit-gated traffic plots and artifact inventory.

Does not change simulation sources/results or promote extrapolations to RTL.
"""
import csv,hashlib,json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/paper_completion_20260908'
V3=ROOT/'results/shared_fetch_cir_matched_v3_20260907'
MIN=ROOT/'results/paper_deadline_20260907/minimum_matrix_v1/timing/provisional_exact_matrix_v1'
OLD=ROOT/'results/paper_validation_20260907/provisional_exact_matrix_v1'
NAMES=[f'{f}_n{n}{s}' for n in (65536,131072) for f,s in [('toroidal_d4',''),('community_d16','_s1'),('uniform_d16','_s1')]]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,d):
    t=p.with_suffix('.tmp');t.write_text(json.dumps(d,indent=2)+'\n');t.replace(p)
def csvwrite(p,rows):
    if not rows:return
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def coverage():
    cases=[('cores-only','64K toroidal',ROOT/'results/full64k_shared_fetch_rtl_20260907_v1/full65536/differential.json','single-H1 timing'),
           ('cir','64K toroidal',ROOT/'results/toroidal_speedup_verification_20260907_v1/cir64_differential.json','archived VCS timing')]
    for mode in ('cir','cores-only'):
        for n in (512,1024,2048):cases.append((mode,f'{n} dense',ROOT/f'results/dense_multi_h1_vcs_v2_20260908/{mode}/{n}/differential.json','2x1H1,2H0/H1,4/8/16cores/H0; timing'))
    for stall in (7,13):cases.append(('cores-only',f'2048 dense stall{stall}',ROOT/f'results/dense_two_h1_backpressure_20260908/stall_{stall}/differential.json','two-H1 timing'))
    cases.extend([('cir','16K active dense',ROOT/'results/dense_vcs_validation_v3_20260908/cir/16384/differential.json','1H1,8H0,256cores; timing'),
       ('cores-only','16K active dense',ROOT/'results/dense_core16k_timeout_retry_20260908/16384/differential.json','1H1,8H0,256cores; watchdog-only retry')])
    rows=[]
    for mode,name,p,scope in cases:
        d=read(p) if p.exists() else {}
        passed=bool(d) and d.get('status')=='pass' and all(d.get('checks',{}).values())
        rows.append(dict(mode=mode,case=name,status='pass' if passed else 'pending-or-review',
            scope=scope,evidence=str(p),evidence_sha256=sha(p) if p.exists() else '',
            checks=d.get('checks',{})))
    save(OUT/'coverage.json',dict(rows=rows,large_geometry_certification=False))
    (OUT/'coverage.md').write_text('# RTL timing coverage\n\n|Mode|Case|Status|Scope|\n|---|---|---|---|\n'+
        ''.join(f"|{r['mode']}|{r['case']}|{r['status']}|{r['scope']}|\n" for r in rows)+
        '\nReports are case-specific. Arithmetic coverage is separate. Source binding is retained in each linked campaign manifest; table inclusion alone does not extend it.\n')
    return all(r['status']=='pass' for r in rows[-2:])
def inventory():
    rows=[]
    for n,x,y in [(65536,1,1),(131072,2,1),(262144,2,2),(524288,4,2),(1048576,4,4)]:
        h=x*y
        for mode in ('cir','cores-only'):
            rows.append(dict(spins=n,mode=mode,mesh_x=x,mesh_y=y,h1_packages=h,h0_tiles=8*h,
                cores=2048*h,endpoint_mvms=2048*h,hierarchy_mvms=38*h if mode=='cir' else 0,
                memory_interfaces=10*h,source_lanes=38*h,canonical_reads_per_block=1,
                endpoint_weight_bytes=4194304*h,streamer_reassembly_bytes=77824*h,
                replay_bytes=77824*h if mode=='cores-only' else 0,
                hierarchy_weight_bytes=77824*h if mode=='cir' else 0,
                endpoint_current_state_register_bits=n,
                h0_state_capacity_bytes=8*h*(256 if mode=='cir' else n//32)*4,
                h1_state_capacity_bytes=h*2048*4 if mode=='cir' else h*(n//32)*4,
                cross_state_capacity_bytes=h*(n//32)*4 if mode=='cir' else 0,
                state_capacity_scope='full-address logical capacity; endpoint state/accumulator registers separate',
                complete_area='unavailable',complete_power='unavailable'))
    csvwrite(OUT/'resource_inventory_v3.csv',rows)
    files=['rtl/core.sv','rtl/dram_weight_streamer.sv','rtl/shared_fetch_replay.sv','rtl/shared_fetch_replay_double.sv','rtl/banked_state_sram.sv']
    save(OUT/'resource_sources.json',{p:sha(V3/'snapshot'/p) for p in files})
    (OUT/'resource_reconciliation.md').write_text('# Corrected v3 resource accounting\n\n'
        'Both modes fetch each canonical off-diagonal block once. The older resource report incorrectly described two cores-only reads and omitted v3 replay storage.\n\n'
        'At64K: 2048cores,38source lanes,10memory interfaces/H1. Both modes retain4MiB endpoint weight slots and76KiB streamer reassembly payload. Cores-only additionally retains76KiB replay payload; CIR retains76KiB hierarchy weight SRAM. Replay/streamer storage in the new support synthesis is register-mapped, not an SRAM macro estimate.\n\n'
        'Endpoint spin state and partial accumulators are registers in core.sv; banked SRAM applies to hierarchy/operand tables. Candidate row-zero shadow registers add128KiB/H1 if adopted; they are not part of the current production performance model.\n\n'
        'Historical GF12 component figures remain partial. Current support synthesis is under build/synthesis/gf12/private/support_v3_20260908. No vendor files or numeric macro data are copied here. Complete wrapper/cache-controller, NoC/PHY/package/DRAM and physical costs remain unresolved.\n')
def artifact_inventory():
    tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    safe=[]
    for name in tracked:
        p=ROOT/name
        if not p.is_file() or p.is_symlink():continue
        if name.startswith(('model/','scripts/')) and p.suffix in ('.py','.sh','.cpp','.md'):
            safe.append(dict(path=name,sha256=sha(p)))
        elif name in ('README.md','requirements.txt','MAPPING_EXPERIMENTS.md','tb/README.md'):
            safe.append(dict(path=name,sha256=sha(p)))
    save(OUT/'artifact_inventory.json',dict(status='local-inventory-not-release-bundle',files=safe,
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        license_review_pending=True,PDK_included=False))
    (OUT/'REPRODUCIBILITY.md').write_text('# Reproduction handoff\n\n'
        'Frozen primary source: results/shared_fetch_cir_matched_v3_20260907/snapshot. Corrected endpoint entry point: azilla_cycle_model.shared_fetch_double_cli, single_fetch_two_slot_cir_routes_v3. CIR: azilla_cycle_model.cli. Never substitute plain-cli cores-only.\n\n'
        'Use the per-point command.json/input.json and frozen dataset/config/library hashes. Preserve original and mapped vertex ordering contracts. Every timing point exports summary,NoC,node,DRAM data; elapsed host time is not accelerator time. Validation reproduction launchers: scripts/queue_dense_multi_h1_vcs.py and scripts/retry_dense_single_h1_timeout.py. Quality: scripts/run_retuned_quality_sweep.py, then its frozen analysis.py. These local launchers currently depend on existing snapshots; clean-clone automation remains a release requirement.\n\n'
        'Do not archive the workspace wholesale. Results, AGENTS.md, local status notes, licensed PDKs, vendor netlists and synthesis databases must not enter the public bundle. artifact_inventory.json is a source inventory, not a completed public release.\n')
def collect():
    rows=[];inputs={};noc=[];dram=[];nodes=[];complete=True
    for name in NAMES:
        dataset=ROOT/'results/paper_canonical/datasets/prepared'/f'{name}.txt'
        dh=sha(dataset)
        for mode in ('cir','cores-only'):
            candidates=[V3/'timing'/name] if mode=='cores-only' else [MIN/name/mode,OLD/name/mode]
            case=next((p for p in candidates if (p/'metrics_summary.json').exists()),None)
            if case is None:complete=False;continue
            files=[case/f'metrics_{suffix}' for suffix in ('summary.json','noc.csv','nodes.csv','dram.csv')]
            if not all(p.exists() for p in files):complete=False;continue
            if mode=='cores-only':
                if not (case/'audit.json').exists():complete=False;continue
                audit=read(case/'audit.json');assert audit['fetch_policy']=='single_fetch_two_slot_cir_routes_v3'
                assert all(sha(p)==h for p,h in audit['files'].items())
                assert sha(V3/'datasets'/dataset.name)==dh
            else:
                cmdfile=case/'command.json'
                if not cmdfile.exists():complete=False;continue
                cmd=read(cmdfile)
                assert sha(cmd[cmd.index('--dataset')+1])==dh
                assert sha(cmd[cmd.index('--ramulator-config')+1])==sha(V3/'snapshot/tb/ramulator_128x32.yaml')
                for key,count in [('h0',4),('h1',2),('cross',4)]:assert int(cmd[cmd.index('--'+key+'-mvms')+1])==count
                inputs[str(cmdfile)]=sha(cmdfile)
            d=read(files[0]);assert d['execution_mode']==mode
            n=65536 if '_n65536' in name else 131072
            assert d['geometry']['spin_count']==n and d['geometry']['h0_per_h1']==8 and d['geometry']['cores_per_h0']==256
            systems=d['dram_systems'];assert all(x['accepted_requests']==x['completed_requests'] and not x['outstanding_requests'] for x in systems)
            assert sum(x['accepted_requests'] for x in systems)==32*sum(x['scheduled_jobs'] for x in systems)
            assert d['noc']['injected_flits']==d['noc']['ejected_flits']
            for p in files:inputs[str(p)]=sha(p)
            inputs[str(dataset)]=dh
            rows.append(dict(dataset=name,mode=mode,spins=n,iteration_cycles=d['timing_cycles']['iteration'],
                total_cycles=d['timing_cycles']['total'],accuracy=d['accuracy']))
            for file,output in [(files[1],noc),(files[2],nodes),(files[3],dram)]:
                with file.open() as f:
                    for r in csv.DictReader(f):output.append(dict(dataset=name,mode=mode,**r))
    save(OUT/'collection.json',dict(cells=len(rows),expected=12,complete=complete,files=inputs,rows=rows))
    return complete,rows,noc,dram,nodes,inputs
def figures(rows,noc,dram,nodes,inputs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    directory=OUT/'figures';directory.mkdir(exist_ok=False)
    # Six matched panels: plot recorded directed links, never analytical stalls.
    fig,axes=plt.subplots(2,3,figsize=(13,6),squeeze=False)
    for col,family in enumerate(('toroidal_d4','community_d16','uniform_d16')):
        selected=[r for r in noc if r['dataset'].startswith(family+'_n131072') and r['scope']=='link']
        keys=sorted({(int(r['node']),r['direction']) for r in selected})
        for j,mode in enumerate(('cir','cores-only')):
            bykey={(int(r['node']),r['direction']):r for r in selected if r['mode']==mode}
            assert set(keys)==set(bykey),'incomplete directed-link pair'
            for row,metric in enumerate(('window_utilization','stall_cycles')):
                axes[row,col].bar([i+(j-.5)*.36 for i in range(len(keys))],[float(bykey[k][metric]) for k in keys],.36,label=mode)
                axes[row,col].set_xticks(range(len(keys)),[f'{n}:{direction}' for n,direction in keys])
                axes[row,col].set_ylabel('Recorded window utilization' if row==0 else 'Stalled cycles')
                axes[row,col].set_title(family+' 128K');axes[row,col].grid(axis='y',alpha=.2)
        axes[0,col].legend()
    fig.tight_layout()
    for suffix in ('pdf','png'):fig.savefig(directory/f'directed_link_traffic.{suffix}',dpi=200)
    plt.close(fig)
    payload=[]
    for r in dram:
        jobs=int(r['scheduled_jobs']);payload.append(dict(dataset=r['dataset'],mode=r['mode'],level=r['level'],node=r['node'],
          canonical_jobs=jobs,weight_read_bytes=jobs*1024,endpoint_delivery_bytes=jobs*(256 if r['mode']=='cir' else 2048),
          scope='analytical block payload, excluding state/control/headers/hop replication'))
    csvwrite(OUT/'analytical_payload_by_source.csv',payload)
    csvwrite(OUT/'matched_noc.csv',noc);csvwrite(OUT/'matched_dram.csv',dram);csvwrite(OUT/'matched_nodes.csv',nodes)
    save(directory/'audit.json',dict(status='inputs-and-conservation-checked',files=inputs,
       visual_review_required=True,paper_ready=False,scope='large geometry projections; representative RTL coverage separate'))
def main():
    OUT.mkdir(exist_ok=False)
    inventory();artifact_inventory()
    while True:
        validation=coverage()
        complete,rows,noc,dram,nodes,inputs=collect()
        save(OUT/'status.json',dict(status='waiting-gates',complete_cells=len(rows),required_cells=12,dense_validation_passed=validation))
        if complete and validation:
            figures(rows,noc,dram,nodes,inputs)
            save(OUT/'status.json',dict(status='complete-pending-manual-review',paper_ready=False));break
        time.sleep(60)
if __name__=='__main__':
    try:main()
    except Exception as e:
        if OUT.exists():save(OUT/'failure.json',dict(error=repr(e)))
        raise
