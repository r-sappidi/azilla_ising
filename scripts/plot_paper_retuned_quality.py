#!/usr/bin/env python3
"""Paper layout for the completed, independently audited retuned quality set."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'results/retuned_quality_v2_20260908'
OUT = ROOT / 'plots/paper/retuned_quality_v2'
GSET_REFERENCES = dict(gset_g21=931, gset_g32=1410, gset_g35=7687,
                       gset_g37=7691, gset_g47=6657, gset_g70=9594,
                       gset_g81=14060)
REFERENCE_SOURCES = [
    'https://leria-info.univ-angers.fr/~jinkao.hao/papers/MaHaoAOR2016.pdf',
    'https://leria-info.univ-angers.fr/~jinkao.hao/papers/GoudetGoeffonHaoCOR2024.pdf',
    'https://arxiv.org/pdf/2505.18508']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    audit = json.loads((SRC / 'figure_audit.json').read_text())
    assert audit['status'] == 'pass' and audit['trials'] == 172
    assert all(sha(Path(p)) == h for p, h in audit['figures'].items())
    manifest = json.loads((SRC / 'manifest.json').read_text())
    trials = json.loads((SRC / 'results.json').read_text())
    assert len(trials) == 172
    assert all(sha(Path(p)) == h for p, h in manifest['files'].items())
    for d in manifest['datasets']:
        assert sha(Path(d['couplings'])) == d['couplings_sha256']
        assert sha(Path(d['objective'])) == d['objective_sha256']
        assert sorted(t['seed'] for t in trials if t['dataset'] == d['name']) == manifest['solver_seeds']
    plt.rcParams.update({'font.size': 8, 'axes.titlesize': 9, 'pdf.fonttype': 42})
    fig, axes = plt.subplots(1, 2, figsize=(7, 2.65), layout='constrained')
    exported = []
    sizes = [1024, 4096, 16384, 65536, 131072, 262144]
    for family, degree, label, color, marker in [
        ('uniform', 16, 'Uniform d16', '#2166ac', 'o'),
        ('uniform', 32, 'Uniform d32', '#b35806', 's'),
        ('community', 16, 'Community d16', '#1b7837', '^')]:
        means, lows, highs = [], [], []
        for size in sizes:
            names = {d['name'] for d in manifest['datasets'] if d['family'] == family and d.get('degree') == degree and d['spins'] == size}
            values = [t['ratio_to_local_search'] for t in trials if t['dataset'] in names]
            assert len(names) == 2 and len(values) == 8
            means.append(float(np.mean(values))); lows.append(min(values)); highs.append(max(values))
            exported.append(dict(group=label, spins=size, mean=means[-1], minimum=lows[-1], maximum=highs[-1], trials=8))
        axes[0].plot(range(6), means, label=label, color=color, marker=marker, markersize=3.5)
        axes[0].fill_between(range(6), lows, highs, color=color, alpha=.12)
    axes[0].set_xticks(range(6), ['1K', '4K', '16K', '64K', '128K', '256K'])
    axes[0].set_xlabel('Spins'); axes[0].set_ylabel('Best cut / local-search cut')
    axes[0].set_title('(a) Fresh synthetic graphs')
    axes[0].set_ylim(.97, 1.045)
    axes[0].legend(loc='lower right', fontsize=7, frameon=False)
    names = [d['name'] for d in manifest['datasets'] if d['family'] == 'Gset']
    means, lows, highs = [], [], []
    for name in names:
        cuts = [t['best_cut'] for t in trials if t['dataset'] == name]
        values = [cut/GSET_REFERENCES[name] for cut in cuts]
        assert len(values) == 4
        means.append(float(np.mean(values))); lows.append(min(values)); highs.append(max(values))
        exported.append(dict(group=name, mean=means[-1], minimum=lows[-1], maximum=highs[-1], trials=4,
                             reference_cut=GSET_REFERENCES[name], cuts=cuts,
                             normalization='published-best-known-reference'))
    means, lows, highs = (100*np.array(v) for v in (means, lows, highs))
    axes[1].errorbar(means, range(len(names)), xerr=[means-lows, highs-means],
                     fmt='o', color='#416c9c', markersize=3.5, ecolor='#416c9c',
                     capsize=2, linewidth=1)
    axes[1].scatter(highs, range(len(names)), marker='D', s=17, color='#b35806', zorder=4)
    for i, best in enumerate(highs):
        axes[1].annotate(f'{best:.2f}%', (best, i), xytext=(5,0),
                         textcoords='offset points', va='center', fontsize=8)
    axes[1].set_yticks(range(len(names)), [n.replace('gset_', '').upper() for n in names])
    axes[1].set_ylim(len(names)-.4,-.6)
    axes[1].set_xlim(90,100.4)
    axes[1].set_xticks([90,92,94,96,98,100], ['90%','92%','94%','96%','98%','100%'])
    axes[1].set_title('(b) Gset: fixed schedule')
    axes[1].set_xlabel('Cut / published best-known cut (zoomed)')
    axes[1].axvline(100, color='black', linestyle='--', linewidth=.7)
    axes[0].axhline(1, color='black', linestyle='--', linewidth=.7)
    axes[0].grid(axis='y', alpha=.2)
    axes[1].grid(axis='x', alpha=.2)
    for ax in axes: ax.set_axisbelow(True)
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ('pdf', 'png'):
        fig.savefig(OUT / f'solution_quality.{ext}', dpi=240)
    plt.close(fig)
    (OUT / 'provenance.json').write_text(json.dumps(dict(
        inputs={str(SRC / n): sha(SRC / n) for n in ('manifest.json', 'results.json', 'figure_audit.json')},
        script_sha256=sha(Path(__file__)), points=exported,
        reference_sources=REFERENCE_SOURCES, reference_cuts=GSET_REFERENCES,
        scope='Fixed-schedule algorithmic reference. Synthetic: local search. Gset: declared published best-known cuts, not proven optima.'), indent=2) + '\n')

if __name__ == '__main__':
    main()
