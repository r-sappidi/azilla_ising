#!/usr/bin/env python3
"""Bind bounded RTL evidence and completed paired baselines to one snapshot."""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/paper_deadline_20260907/validation_fairness_v1'
BASE=ROOT/'results/paper_validation_20260907/provisional_exact_matrix_v1/snapshot'
CIR=ROOT/'results/paper_validation_20260907/cir_immutable_20260907_0027'
CAMPAIGNS=['provisional_exact_matrix_v1','provisional_mvm_sweep_v1','provisional_width_v1',
           'provisional_density_v1','provisional_geometry_v1','mapping_ablation_exact_v1']

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,data):(OUT/name).write_text(json.dumps(data,indent=2)+'\n')
def read(path):return json.loads(Path(path).read_text())
def model_hashes():return {str(p.relative_to(BASE)):sha(p) for p in (BASE/'model/azilla_cycle_model').glob('*.py')}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    before=model_hashes()
    # Inspect the one differing module structurally: the only added dispatch
    # selects cores-only; every pre-existing CIR method remains identical.
    def cir_ast(path):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.ClassDef):
                node.body=[x for x in node.body if not isinstance(x,ast.FunctionDef)
                           or x.name not in ('_run_cores_only','_run_cores_only_legacy_kernel')]
        return ast.dump(tree,include_attributes=False)
    cir_equal=cir_ast(CIR/'model/azilla_cycle_model/exact_events.py')==cir_ast(BASE/'model/azilla_cycle_model/exact_events.py')
    coverage=[]
    for family in ('kings','toroidal','random'):
        path=CIR/'evidence'/f'{family}.json';d=read(path)
        guards={p:Path(p).exists() and sha(p)==h for p,h in d['sha256'].items()}
        changed=[p for p,h in d['source_sha256'].items() if p.startswith('model/') and sha(BASE/p)!=h]
        compatible=(not changed or (changed==['model/azilla_cycle_model/exact_events.py'] and cir_equal))
        coverage.append(dict(mode='cir',family=family,evidence=str(path),evidence_sha256=sha(path),
            existing_checks=d['checks'],geometry=d['geometry'],engines=d['mvms'],
            cycles=d['observed_timing'],accepted_transfers=d['observed_transfers'],
            immutable_input_guards=guards,changed_modules=changed,
            cir_path_ast_unchanged=cir_equal,campaign_scope_source_compatible=compatible,
            status='bounded-evidence-valid' if compatible and all(guards.values()) and all(d['checks'].values()) else 'review-required',
            scope='16K specified-family CIR timing/accepted-transfer/stall differential; not arithmetic or new geometry'))
    # Fresh small VCS logs are replayed through a read-only frozen-model view.
    view=OUT/'checker_view';(view/'scripts').mkdir(parents=True,exist_ok=True)
    for directory,target in [('model',BASE/'model'),('build',ROOT/'build'),('tb',ROOT/'tb')]:
        link=view/directory
        if not link.exists():link.symlink_to(target,target_is_directory=True)
    for name in ('check_core_full_events.py','check_core_iteration_pipeline.py','generate_core_iteration_dataset.py'):
        shutil.copy2(ROOT/'scripts'/name,view/'scripts'/name)
    fresh=OUT/'core_vcs_fresh'
    rtl_sources={str(p):sha(p) for p in [ROOT/'rtl/core.sv',ROOT/'rtl/mvm.sv',ROOT/'rtl/banked_state_sram.sv',
        ROOT/'rtl/dram_weight_streamer.sv',ROOT/'rtl/azilla_floo_router.sv',ROOT/'tb/core_iteration_publication_vcs_tb.sv',
        ROOT/'tb/ramulator_node_frontend.sv',ROOT/'tb/ramulator_dpi.cpp']}
    for stall in (0,7,13):
        for timing in (False,True):
            command=[sys.executable,str(view/'scripts/check_core_full_events.py'),'--root',str(fresh),
                '--stall',str(stall),'--engines','4','--h0-engines','4','--h1-engines','2','--cross-engines','4',
                '--config',str(BASE/'ramulator_128x32.yaml')]+(['--timing-only'] if timing else [])
            tag=f'campaign_replay_stall{stall}_'+('timing' if timing else 'arithmetic')
            with (OUT/(tag+'.log')).open('w') as log:
                result=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=log,stderr=subprocess.STDOUT)
            report=fresh/f"{'wrapper_timing' if timing else 'wrapper'}_stall{stall}.json"
            d=read(report)
            coverage.append(dict(mode='cores-only',family='mixed-three-level-256',evidence=str(report),
                evidence_sha256=sha(report),geometry=dict(mesh_x=2,mesh_y=1,h0_per_h1=2,cores_per_h0=2),
                engines=dict(h0=4,h1=2,cross=4),iterations=2,stall_period=stall,
                arithmetic_evaluated=not timing,cycles=d['model_total'],checks=d['checks'],command=command,
                rtl_log_sha256=sha(fresh/f'stall{stall}.log'),rtl_source_sha256=rtl_sources,
                status='bounded-evidence-valid' if result.returncode==0 and d['matched'] else 'review-required',
                scope='Full production cores-only controller against 256-spin VCS; no large-mesh RTL certificate'))
            save('coverage.json',dict(campaign_snapshot=str(BASE),model_sha256=before,coverage=coverage))
            print('CHECK',tag,d['matched'],flush=True)
            if result.returncode:raise RuntimeError(tag+' failed')
    pairs=[];incomplete=[]
    for campaign in CAMPAIGNS:
        folder=ROOT/'results/paper_validation_20260907'/campaign
        manifest=read(folder/'manifest.json')
        campaign_sources={p:h for p,h in manifest['files'].items() if '/model/azilla_cycle_model/' in p and p.endswith('.py')}
        source_ok=all(sha(BASE/'model/azilla_cycle_model'/Path(p).name)==h for p,h in campaign_sources.items())
        for cir_path in sorted(folder.rglob('cir/metrics_summary.json')):
            core_path=cir_path.parent.parent/'cores-only/metrics_summary.json'
            if not core_path.exists():incomplete.append(str(cir_path.parent.parent));continue
            a,b=read(cir_path),read(core_path)
            ca,cb=read(cir_path.parent/'command.json'),read(core_path.parent/'command.json')
            def argument(cmd,key):return cmd[cmd.index(key)+1] if key in cmd else None
            dataset_a,dataset_b=Path(argument(ca,'--dataset')),Path(argument(cb,'--dataset'))
            ma,mb=Path(argument(ca,'--ramulator-config')),Path(argument(cb,'--ramulator-config'))
            ra={r['system_id']:r for r in a['dram_systems']};rb={r['system_id']:r for r in b['dram_systems']}
            checks=dict(source_model=source_ok,dataset=sha(dataset_a)==sha(dataset_b),memory_config=sha(ma)==sha(mb),
                geometry=a['geometry']==b['geometry'],interconnect=a['interconnect']==b['interconnect'],package=a['package']==b['package'],
                same_systems=ra.keys()==rb.keys(),canonical_owner_twice=ra.keys()==rb.keys() and all(rb[k]['scheduled_jobs']==2*ra[k]['scheduled_jobs'] for k in ra),
                shared_interface_engines=ra.keys()==rb.keys() and all(ra[k]['engines']==rb[k]['engines'] for k in ra),
                same_stored_blocks=a['cores_only_ablation']['logical_weight_blocks_stored']==b['cores_only_ablation']['logical_weight_blocks_stored'],
                core_directed_twice=b['cores_only_ablation']['directed_core_jobs']==2*a['scheduled_jobs']['total'],
                model_full_core=b['cores_only_ablation']['pipeline_contract'].get('model')=='full_single_mvm_iteration_v1')
            for mode,summary in [('cir',a),('cores-only',b)]:
                checks[mode+'_conservation']=summary['noc']['injected_flits']==summary['noc']['ejected_flits'] and all(r['accepted_requests']==r['completed_requests']==32*r['scheduled_jobs'] and r['outstanding_requests']==0 for r in summary['dram_systems'])
            g=a['geometry'];blocks=g['spin_count']//32;h0s=g['mesh_x']*g['mesh_y']*g['h0_per_h1']
            pairs.append(dict(campaign=campaign,point=str(cir_path.parent.parent.relative_to(folder)),checks=checks,
                status='paired-controls-pass' if all(checks.values()) else 'review-required',
                files={str(p):sha(p) for p in (cir_path,core_path,dataset_a,ma)},
                iteration_cycles=dict(cir=a['timing_cycles']['iteration'],cores_only=b['timing_cycles']['iteration']),
                speedup_core_over_cir=b['timing_cycles']['iteration']/a['timing_cycles']['iteration'],
                effective_engines=dict(cir=a['effective_mvm_engines'],cores_only=b['effective_mvm_engines']),
                source_interface_count=len(ra),core_h0_operand_sram_provision_bytes=blocks*4*h0s,
                core_h0_operand_sram_bytes_each=blocks*4,
                pure_compute_placement_ablation=False,
                shared_fetch_two_destination_core_baseline_evaluated=False,
                note='Matched configuration and canonical storage, but combined placement plus symmetric-fetch-reuse comparison; not equal active MVM count, area, energy, or best possible cores-only algorithm.'))
    if before!=model_hashes():raise RuntimeError('campaign source changed')
    save('paired_baselines.json',dict(pairs=pairs,incomplete_pairs=incomplete,
        internal_traffic_scope=dict(exported_noc='H1-mesh injection/ejection/directed links only',
            internal_h0_h1_stall_rows_available=False,
            core_local_accepted_beats='Owner-level aggregate only in core pipeline audit; not an internal per-link stall trace',
            analytical_cir_partial_bytes_per_symmetric_block=256,
            analytical_core_weight_bytes_per_directed_job=1024,
            analytical_volume_not_measured_internal_stalls=True),
        checked_pairs=len(pairs),passed_pairs=sum(p['status']=='paired-controls-pass' for p in pairs)))
    report='''# Validation and paired-baseline audit

This audit does not release a blanket cycle-accuracy or paper-ready certificate.

- CIR: three frozen 16K VCS differentials cover Kings, toroidal, and random placement at 4×4 H1, 2 H0/H1, 16 cores/H0, and 4/2/4 engines. Their only campaign source delta is the new cores-only dispatch; the CIR path AST is unchanged. Exact geometry/family scope remains mandatory.
- Cores-only: fresh 256-spin VCS, two full arithmetic epochs, mixed H0/H1/cross blocks, normal/transpose, one physical MVM/core, state publication/cache fill, completion, and stall periods 0/7/13. Six frozen-campaign timing/arithmetic replays bind evidence directly to campaign sources.
- Paired comparisons require identical datasets, geometry, canonical source interfaces, memory configuration, and link assumptions. CIR performs one symmetric block read; this baseline performs two directed reads at the same canonical owner. This is a mechanism difference despite identical logical weight storage, not a pure compute-placement ablation.
- Active compute counts differ: cores-only uses destination cores; CIR uses hierarchy engines for off-diagonal work. Report both counts. These are not iso-area or equal active-engine comparisons.
- Cores-only provisions global-block-addressable eight-bank operand SRAM at every H0 (4 bytes per global 32-spin block per H0), while filling only required states. Include this storage cost; do not reuse CIR H0-local table capacity/area for the baseline.

## Remaining release criteria

1. Preserve exact snapshot and paired-input hashes; reject incomplete or failed conservation checks.
2. Label larger geometry results as component-validated exact-model projections, not large RTL differentials. No 64K/256K cores-only full RTL certificate follows from the 256-spin fixture.
3. Report one versus two canonical DRAM reads and available compute resources explicitly. A stronger cores-only baseline could reuse/broadcast a canonical block once; that mechanism is not implemented or evaluated here.
4. Keep resource/area/energy claims separate: shared state storage, coefficient storage, bank-port arbitration and packaging costs are not established by timing-only comparisons.
5. Before adopting optimized results, require the separate optimization gate (full/mapped/default-refresh/windows/components). This audit does not bypass it.

## Conclusions supported without a shared-fetch baseline

The reported latency and traffic comparisons quantify these two specified implementations: symmetric hierarchy computation with block-fetch reuse versus endpoint computation with two directed fetches. They do not isolate a CIR compute-placement benefit, establish optimality over all cores-only designs, or demonstrate equal-area/energy superiority. A shared-fetch/two-destination endpoint implementation is an untested stronger alternative and must be acknowledged explicitly.

All timings here remain in cycles. The modeled accelerator/Ramulator ratio is 40 ticks/cycle times 250 ps/tick = 10 ns/cycle (100 MHz), matching tb/ising_mesh_tb.sv's 10 ns clock. A 1 GHz logic-synthesis constraint is not a validated operating frequency or a valid conversion for these results. Memory timing must be rescaled consistently before interpreting a different accelerator clock.

## Targeted bank-conflict closure

A separate fresh four-lane VCS bank component regression compares 42 edges against the frozen CoreStateBank, including six accepted reads, three same-bank stalled lane-cycles, synchronous responses, and read-before-write. See bank_conflict.json. This closes component conflict semantics, not large-core-count integrated RTL coverage.

## Internal-traffic exporter limitation

The current summary/metrics_noc.csv exports describe H1-mesh injection, ejection, and physical directed links; single-H1 64K points consequently contain injection/ejection only. They do not provide per-link accepted/stalled H0/H1-internal traffic rows. The cores-only pipeline audit exposes aggregate accepted local-delivery beats by owner, but not corresponding internal stalled-cycle traces. Do not label inferred internal congestion as measured.

Logical internal payload volume may be derived analytically: a symmetric block produces two 32×32-bit partial vectors (256 bytes total), while each directed core job transports a 32×32 int8 weight block (1,024 bytes). These are logical bytes before topology/path multiplicity or packet overhead, not measured internal-link traffic. A measured internal-congestion/latency claim remains unsupported until explicit internal transfer/stall instrumentation is exported and checked.
'''
    report+=f'\nCompleted paired controls: {sum(p["status"]=="paired-controls-pass" for p in pairs)}/{len(pairs)}. Missing counterparts: {len(incomplete)}. See JSON for per-point checks.\n'
    (OUT/'REPORT.md').write_text(report)
    print('PAIRS',len(pairs),'PASS',sum(p['status']=='paired-controls-pass' for p in pairs),flush=True)


if __name__=='__main__':main()
