#!/usr/bin/env python3
"""Read-only matched v3 audit and provisional, consistently populated figures."""
import argparse
import json
from pathlib import Path
import run_paper_completion_audits as base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    base.OUT = a.output
    complete, rows, noc, dram, nodes, inputs = base.collect()
    summaries = {}
    for name in inputs:
        if name.endswith('metrics_summary.json'):
            p = Path(name)
            d = base.read(p)
            dataset = p.parent.name if d['execution_mode'] == 'cores-only' else p.parent.parent.name
            summaries[dataset, d['execution_mode']] = d
    pairs, measurements = [], []
    for name in base.NAMES:
        if not all((name, mode) in summaries for mode in ('cir', 'cores-only')):
            pairs.append(dict(dataset=name, status='pending'))
            continue
        c, e = (summaries[name, mode] for mode in ('cir', 'cores-only'))
        checks = {key: c[key] == e[key] for key in ('geometry', 'interconnect', 'package')}
        def sources(d):
            return {(s['level'], s['node'], s['index']):
                    (s['scheduled_jobs'], s['accepted_requests'], s['engines'])
                    for s in d['dram_systems']}
        checks['same_source_jobs_requests_lanes'] = sources(c) == sources(e)
        checks['single_fetch_policy'] = e['cores_only_ablation']['pipeline_contract']['fetch_policy'] == 'single_fetch_two_slot_cir_routes_v3'
        pairs.append(dict(dataset=name, status='pass' if all(checks.values()) else 'fail', checks=checks,
                          iteration_speedup=e['timing_cycles']['iteration']/c['timing_cycles']['iteration']))
        for mode, d in [('cir', c), ('cores-only', e)]:
            systems = d['dram_systems']
            accepted = sum(s['accepted_requests'] for s in systems)
            rejected = sum(s['rejected_requests'] for s in systems)
            measurements.append(dict(dataset=name, mode=mode,
                iteration_cycles=d['timing_cycles']['iteration'],
                injected_flits=d['noc']['injected_flits'],
                physical_link_flits=d['noc']['physical_link_flits'],
                canonical_jobs=sum(s['scheduled_jobs'] for s in systems),
                accepted_requests=accepted, rejected_requests=rejected,
                rejection_fraction=rejected/(accepted+rejected) if accepted+rejected else 0,
                average_memory_latency_cycles=sum(s['average_latency_cycles']*s['completed_requests'] for s in systems)/accepted if accepted else 0,
                injection_stalls=d['noc']['injection_stalls'],
                link_stalls=d['noc']['link_stalls'], accuracy=d['accuracy']))
    report = dict(status='pass' if all(p['status'] != 'fail' for p in pairs) else 'fail',
                  complete_matrix=complete, pairs=pairs, files=inputs,
                  scope='Matched-input and conservation audit; not additional RTL certification.',
                  paper_ready=False)
    base.save(a.output/'audit.json', report)
    assert report['status'] == 'pass', 'Matched-pair audit failed; no figures emitted'
    base.csvwrite(a.output/'matched_measurements.csv', measurements)
    base.csvwrite(a.output/'matched_noc.csv', noc)
    base.csvwrite(a.output/'matched_dram.csv', dram)
    base.csvwrite(a.output/'matched_nodes.csv', nodes)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    labels = ['Torus\n64K', 'Community\n64K', 'Uniform\n64K', 'Torus\n128K', 'Community\n128K', 'Uniform\n128K']
    lookup = {(r['dataset'], r['mode']): r for r in measurements}
    specs = [('latency', [('iteration_cycles','Iteration cycles',True)]),
             ('communication', [('injected_flits','Top-level NoC injected flits',True),('physical_link_flits','Inter-H1 physical-link flits',True)]),
             ('memory', [('accepted_requests','Accepted DRAM requests',True),('average_memory_latency_cycles','Mean DRAM latency (cycles)',False)])]
    for stem, metrics in specs:
        fig, axes = plt.subplots(1, len(metrics), figsize=(8*len(metrics), 4.3), squeeze=False)
        for ax, (key, label, log) in zip(axes[0], metrics):
            for j, (mode, color, title) in enumerate([('cir','#3274a1','CIR'),('cores-only','#e1812c','Cores-only')]):
                xx, yy = [], []
                for i, name in enumerate(base.NAMES):
                    if (name, mode) not in lookup: continue
                    value = lookup[name, mode][key]
                    x = i + (j-.5)*.36
                    if value == 0:
                        ax.text(x, .025, '0', ha='center', va='bottom', transform=ax.get_xaxis_transform(), color=color)
                    else: xx.append(x); yy.append(value)
                ax.bar(xx, yy, width=.34, color=color, label=title)
            for i, name in enumerate(base.NAMES):
                if (name, 'cores-only') not in lookup:
                    ax.text(i, .35, 'Pending', rotation=90, ha='center', transform=ax.get_xaxis_transform(), color='0.4')
            if log: ax.set_yscale('log')
            ax.set_xlim(-.6, 5.6)
            ax.set_xticks(range(6), labels); ax.set_ylabel(label)
            ax.grid(axis='y', alpha=.2); ax.set_axisbelow(True); ax.legend()
        fig.tight_layout()
        for ext in ('pdf','png'): fig.savefig(a.output/f'{stem}.{ext}', dpi=180)
        plt.close(fig)
    (a.output/'README.md').write_text(
        '# Matched exact-event results\n\n'
        'All figures use the same five complete dataset/size pairs; the sixth pair is pending. '
        'No calibrated results or imputed measurements are included. 64K is one H1; 128K is two H1s. '
        'Zero physical-link traffic at 64K means no inter-H1 links, not zero internal communication.\n\n'
        'Requests are measured transaction counts, not bytes. Rejection counts include retries. '
        'Mean DRAM latency is completion-weighted. Stall counts are recorded events, not an additive latency breakdown. '
        'Equal per-source reads confirm matched hierarchy memory demand; CIR changes computation and delivery payload. '
        'These checks do not certify large geometries against RTL. Figures remain provisional until the matrix completes.\n')
    print(json.dumps(report['pairs'], indent=2))


if __name__ == '__main__':
    main()
