#!/usr/bin/env python3
"""Read-only G81 plateau analysis, including final-state two-cycle checks."""
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'results/gset_robust_tuning_20260908'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((BASE/'manifest.json').read_text())
    d=next(x for x in manifest['datasets'] if x['name']=='gset_g81')
    refpath=BASE/'snapshot/reference.py'
    spec=importlib.util.spec_from_file_location('g81_ref',refpath)
    ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref)
    n,_,r,c,j=ref.load_dataset(Path(d['couplings']))
    assert sha(Path(d['couplings']))==d['couplings_sha256']
    assert sha(Path(d['objective']))==d['objective_sha256']
    on,_,orr,occ,ow=ref.load_dataset(Path(d['objective']));assert n==on
    sources={str(p):sha(p) for p in [BASE/'manifest.json',refpath,Path(d['couplings']),Path(d['objective']),Path(__file__)]}
    rows=[];curves={}
    for stage in ('evaluation','previous_baseline'):
        curves[stage]=[]
        for path in sorted((BASE/stage/'gset_g81').glob('*/seed_*/trajectory.csv')):
            with path.open() as f:t=list(csv.DictReader(f))
            cols={k:np.array([int(x[k]) for x in t]) for k in t[0]}
            cut=cols['cut'];best=np.maximum.accumulate(cut)
            assert len(t)==1001 and np.array_equal(cols['iteration'],np.arange(1001))
            audit=json.loads((path.parent/'audit.json').read_text());p=audit['parameters']
            assert max(cut)==audit['best_cut'] and cut[-1]==audit['final_cut']
            with np.load(path.parent/'states.npz') as states:
                for name,key in [('initial','initial_cut'),('best','best_cut'),('final','final_cut')]:
                    assert ref.cut_value(states[name],orr,occ,ow)==audit[key]
                state=states['final'].copy()
            def coupling(s):return np.bincount(r,weights=j*(s.astype(np.int64)*2-1)[c],minlength=n).astype(np.int64)
            def step(s):return p['a']*(s.astype(np.int64)*2-1)+p['b']*coupling(s)>=0
            next_state=step(state);next2=step(next_state)
            # Gain from flipping ONE spin, evaluated at the final state.
            gains=-(state.astype(np.int64)*2-1)*coupling(state)
            improvements=np.flatnonzero(np.diff(best)>0)+1
            positive=cols['noise'][1:]>0
            last_nonzero=int(np.flatnonzero(positive)[-1]+1) if positive.any() else 0
            active=int(np.count_nonzero(next_state!=state))
            base=p['a']*(state.astype(np.int64)*2-1)+p['b']*coupling(state)
            susceptibility={str(amp):int(np.count_nonzero(((base+amp>=0)!=state)|((base-amp>=0)!=state)))
                            for amp in (0,1,2,3,4,6,8,12)}
            row=dict(stage=stage,seed=audit['seed'],best=int(best[-1]),final=int(cut[-1]),
                final_loss=int(best[-1]-cut[-1]),last_improvement=int(improvements[-1]),
                gain_after_500=int(best[-1]-best[500]),gain_after_750=int(best[-1]-best[750]),
                gain_after_900=int(best[-1]-best[900]),noise_zero_start=last_nonzero+1,
                final_recorded_flips=int(cols['flipped_spins'][-1]),
                period2_observations_last100=int(cols['spin_period_two'][-100:].sum()),
                deterministic_fixed_point=active==0,
                deterministic_two_cycle=active>0 and bool(np.array_equal(next2,state)),
                deterministic_flips=active,positive_single_flip_gains=int(np.count_nonzero(gains>0)),
                largest_single_flip_gain=int(gains.max()),
                sum_positive_single_flip_gains=int(gains[gains>0].sum()),
                susceptibility=susceptibility,parameters=p)
            rows.append(row);curves[stage].append(dict(cut=cut,best=best,flips=cols['flipped_spins']))
            for f in (path,path.parent/'audit.json',path.parent/'states.npz'):sources[str(f)]=sha(f)
    summary={}
    for stage in curves:
        rr=[x for x in rows if x['stage']==stage];assert len(rr)==16
        summary[stage]=dict(seeds=len(rr),last_improvement_min=min(x['last_improvement'] for x in rr),
            last_improvement_max=max(x['last_improvement'] for x in rr),
            mean_gain_after750=float(np.mean([x['gain_after_750'] for x in rr])),
            mean_gain_after900=float(np.mean([x['gain_after_900'] for x in rr])),
            final_loss_max=max(x['final_loss'] for x in rr),
            two_cycles=sum(x['deterministic_two_cycle'] for x in rr),
            fixed_points=sum(x['deterministic_fixed_point'] for x in rr),
            mean_oscillating_spins=float(np.mean([x['deterministic_flips'] for x in rr])),
            max_oscillating_spins=max(x['deterministic_flips'] for x in rr),
            max_single_flip_gain=max(x['largest_single_flip_gain'] for x in rr),
            mean_sum_positive_single_flip_gains=float(np.mean([x['sum_positive_single_flip_gains'] for x in rr])))
    report=dict(status='pass',spins=n,summary=summary,per_seed=rows,source_sha256=sources,
        scope='Saved trajectories and deterministic two-step final-state diagnostic only; no additional tuning or solution postprocessing. Sum of single-flip gains is not a bound on multispin improvement.')
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    flat=[{k:v for k,v in row.items() if not isinstance(v,dict)} for row in rows]
    with (args.output/'per_seed.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(flat[0]));w.writeheader();w.writerows(flat)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(9,3.4),layout='constrained')
    for stage,label in [('evaluation','New settings'),('previous_baseline','Previous settings')]:
        best=np.array([x['best'] for x in curves[stage]])/14060*100
        flips=np.array([x['flips'] for x in curves[stage]])
        line,=axes[0].plot(best.mean(axis=0),label=label)
        axes[0].fill_between(np.arange(1001),best.min(axis=0),best.max(axis=0),color=line.get_color(),alpha=.13)
        axes[1].plot(flips.mean(axis=0),label=label)
    axes[0].set_xlim(400,1000);axes[0].set_ylim(95,97)
    axes[0].set_ylabel('Best-so-far cut / reference (%)')
    axes[0].set_title('G81 plateau (16 fresh seeds)')
    axes[1].set_yscale('symlog',linthresh=1);axes[1].set_ylabel('Mean spins flipped per iteration')
    axes[1].set_title('Residual activity is not cut improvement')
    for ax in axes:ax.set_xlabel('Iteration');ax.grid(alpha=.2);ax.legend(fontsize=8)
    for ext in ('png','pdf'):fig.savefig(args.output/f'g81_plateau.{ext}',dpi=180)
    plt.close(fig)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
