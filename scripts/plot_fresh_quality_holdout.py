#!/usr/bin/env python3
"""Audit completed holdouts before producing descriptive, non-optimality plots."""
import argparse,csv,hashlib,json,time
from pathlib import Path
import numpy as np

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--wait',action='store_true');a=ap.parse_args();out=a.output.resolve()
    while not (out/'complete.json').exists():
        if (out/'failure.json').exists() or list(out.glob('*_failure.json')):raise RuntimeError('quality failure present; no figures produced')
        if not a.wait:raise RuntimeError('campaign incomplete; no figures produced')
        time.sleep(30)
    manifest=json.loads((out/'manifest.json').read_text());done=json.loads((out/'complete.json').read_text());results=json.loads((out/'results.json').read_text())
    expected_trials=len(manifest['datasets'])*len(manifest['solver_seeds'])
    assert done['status']=='complete' and done['checks']=='pass'
    assert len(results)==done['matched_trials']==manifest['trial_count']==expected_trials
    assert all(sha(p)==v for p,v in manifest['files'].items())
    figures=out/'figures';figures.mkdir(exist_ok=False);rows=[];curves={}
    for d in manifest['datasets']:
        assert sha(d['couplings'])==d['couplings_sha256'] and sha(d['objective'])==d['objective_sha256']
        with Path(d['couplings']).open() as f:
            n,_=map(int,f.readline().split());edges=np.loadtxt(f,dtype=np.int64)
        r=edges[:,0]-1;c=edges[:,1]-1;w=-edges[:,2];keep=r<c;r,c,w=r[keep],c[keep],w[keep]
        trials=[t for t in results if t['dataset']==d['name']]
        assert sorted(t['seed'] for t in trials)==manifest['solver_seeds']
        group='Gset' if d['family']=='Gset' else d['family']+' d'+str(d['degree'])
        for t in trials:
            directory=out/'trials'/d['name']/f"seed_{t['seed']}"
            trajectory=list(csv.DictReader((directory/'ising/trajectory.csv').open()))
            values=np.array([int(v['cut']) for v in trajectory]);assert len(values)==1001
            assert int(values.max())==t['best_cut'] and int(values[-1])==t['final_cut'] and int(np.argmax(values))==t['best_iteration']
            state=np.load(directory/'ising/states.npz')
            for key,expected in [('best',t['best_cut']),('final',t['final_cut']),('initial',t['initial_cut'])]:
                assert state[key].shape==(n,)
                assert int(w[state[key][r]!=state[key][c]].sum(dtype=np.int64))==expected
            assert t['best_cut']<=int(w[w>0].sum())
            cpu=np.loadtxt(directory/'cpu/final.txt',dtype=np.int64)
            assert int(w[cpu[r]!=cpu[c]].sum())==t['local_search_cut']
            assert np.array_equal(state['initial'].astype(np.int64)*2-1,np.loadtxt(directory/'cpu/initial.txt',dtype=np.int64))
            curves.setdefault(group,[]).append(values/t['local_search_cut'])
        vertices=d['spins']
        if d['family']=='Gset':
            source_manifest=Path(d['original_source']).parent.parent/'manifest.json'
            entry=next(v for v in json.loads(source_manifest.read_text())['datasets'] if v['name']==d['name'])
            assert entry['sha256']==d['original_sha256']
            vertices=entry['problem_vertices']
        rows.append(dict(dataset=d['name'],family=d['family'],allocated_spins=d['spins'],problem_vertices=vertices,degree=d.get('degree'),graph_seed=d.get('graph_seed'),seeds=len(trials),
            mean_best_cut=float(np.mean([t['best_cut'] for t in trials])),mean_local_search_cut=float(np.mean([t['local_search_cut'] for t in trials])),
            mean_ratio_to_local_search=float(np.mean([t['ratio_to_local_search'] for t in trials])),
            median_best_iteration=float(np.median([t['best_iteration'] for t in trials])),
            zero_final_fraction=float(np.mean([t['final_cut']==0 for t in trials])),scope=d['scope']))
    with (out/'holdout_summary.csv').open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    upper=max(1.15,max(t['ratio_to_local_search'] for t in results)*1.05)
    fig,axes=plt.subplots(1,3,figsize=(15,4),gridspec_kw={'width_ratios':[7,8,3]})
    for ax,family in zip(axes,['Gset','uniform','community']):
        entries=[d for d in manifest['datasets'] if d['family']==family]
        groups={}
        for d in entries:
            label=d['name'].replace('gset_','').upper() if family=='Gset' else f"{d['spins']//1024}K d{d['degree']}"
            groups.setdefault(label,[]).extend(t['ratio_to_local_search'] for t in results if t['dataset']==d['name'])
        labels=list(groups);means=np.array([np.mean(groups[k]) for k in labels]);lo=np.array([min(groups[k]) for k in labels]);hi=np.array([max(groups[k]) for k in labels])
        ax.bar(range(len(labels)),means,color='#416c9c');ax.errorbar(range(len(labels)),means,yerr=[means-lo,hi-means],fmt='none',ecolor='black',capsize=2)
        ax.axhline(1,color='black',linestyle='--',linewidth=.8);ax.set_xticks(range(len(labels)),labels,rotation=45,ha='right');ax.set_title(family);ax.set_ylim(0,upper);ax.grid(axis='y',alpha=.2)
    axes[0].set_ylabel('Best cut / matched local-search cut');fig.tight_layout();fig.savefig(figures/'heldout_quality_vs_local_search.pdf');fig.savefig(figures/'heldout_quality_vs_local_search.png',dpi=220);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for group,values in curves.items():
        array=np.array(values)
        for ax,data in zip(axes,[np.maximum.accumulate(array,axis=1),array]):
            ax.plot(np.median(data,axis=0),label=group);ax.fill_between(np.arange(1001),np.percentile(data,25,axis=0),np.percentile(data,75,axis=0),alpha=.12)
    for ax,title in zip(axes,['Best-so-far quality','Current-state quality']):
        ax.set_title(title);ax.set_xlabel('Iteration');ax.set_ylabel('Cut / matched local-search cut');ax.grid(alpha=.2)
    axes[1].legend(fontsize=8);fig.tight_layout();fig.savefig(figures/'heldout_quality_trajectories.pdf');fig.savefig(figures/'heldout_quality_trajectories.png',dpi=220);plt.close(fig)
    worst=min(rows,key=lambda r:r['mean_ratio_to_local_search'])
    (out/'REPORT.md').write_text('# Frozen-schedule held-out solution quality\n\n'
        f"{expected_trials} matched trials across {len(manifest['datasets'])} datasets, {len(manifest['solver_seeds'])} solver seeds per graph,1000 iterations. "
        f"Training-frozen schedule: {manifest['schedule']}. No parameter changes during evaluation.\n\n"
        'Generated graphs are fresh instances; Gset retests were excluded from current parameter training but had been inspected in earlier experiments. '
        'The matched CPU baseline is sequential positive-gain local search, not an optimum or state-of-the-art comparator. Gset inputs retain isolated padding spins; the summary distinguishes actual problem vertices from allocated spins. These are algorithmic-reference quality results, not hardware runtime or large RTL validation.\n\n'
        f"Lowest mean paired best-cut/local-search ratio: {worst['dataset']} = {worst['mean_ratio_to_local_search']:.4f}; final-zero fraction {worst['zero_final_fraction']:.3f}. All deteriorations and collapses are retained.\n\n"
        'Bar whiskers show the observed minimum–maximum seed/instance range, not confidence intervals. Trajectory lines show descriptive medians with25–75% bands; best-so-far and current-state cuts are both shown.\n\n'
        'Post-run audit rechecked every1001-point trajectory, independently recomputed initial/best/final objectives from saved spins, recomputed CPU final cuts, verified matched initial states, all input/source hashes and seed completeness.\n')
    (out/'figure_audit.json').write_text(json.dumps(dict(status='pass',trials=expected_trials,datasets=len(manifest['datasets']),independent_saved_state_objectives=True,
        figures={str(p):sha(p) for p in figures.iterdir()},reference='local search, not known optimum'),indent=2))
    print('Audited and plotted complete holdout',out,flush=True)
if __name__=='__main__':main()
