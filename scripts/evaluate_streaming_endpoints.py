#!/usr/bin/env python3
"""Isolated RTL-first streaming gate. Never changes production launchers/model.

Compile alternate core/MVM source files, retaining identical baseline TBs,
Ramulator, forwarding lanes, and network. Fail closed on arithmetic mismatch.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('/home/rsappidi/miniforge3/envs/azilla/bin')


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def command(args, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w') as log:
        subprocess.run(['/usr/bin/time', '-v', '-o', str(output.with_suffix('.time')),
                        *map(str, args)], cwd=ROOT, stdout=log,
                       stderr=subprocess.STDOUT, check=True)


def build(out, mode, top, params=()):
    out.mkdir(parents=True, exist_ok=True)
    rtl = ROOT/'rtl' if mode == 'buffered' else ROOT/'rtl/experimental/streaming'
    sources = ['rtl/ising_pkg.sv', 'rtl/j_block_sram.sv',
               str(rtl/'mvm.sv'), str(rtl/'core.sv')]
    args = [os.environ.get('VCS_HOME', '/home/hwpeng/vcs')+'/bin/vcs',
            '-full64', '-sverilog', '-j', '1', '-timescale=1ns/1ps',
            '-cpp', str(COMPILER/'x86_64-conda-linux-gnu-g++'),
            '-cc', str(COMPILER/'x86_64-conda-linux-gnu-gcc'),
            '-top', top, '-Mdir='+str(out/'csrc'), '-o', str(out/'simv'),
            '-assert', 'svaext', '-assert', 'disable_cover']
    args += [f'-pvalue+{top}.{k}={v}' for k,v in params]
    if top in ('shared_fetch_publication_vcs_tb', 'shared_fetch_two_h1_tb', 'shared_fetch_single_h1_tb'):
        args += ['-f', 'rtl/floo_router_files.f', '-CFLAGS',
                 f'-std=c++20 -I{ROOT}/third_party/ramulator2/src', '-LDFLAGS',
                 f'-no-pie -L{ROOT}/third_party/ramulator2 -lramulator -Wl,-rpath,{ROOT}/third_party/ramulator2']
        sources += ['rtl/azilla_floo_router.sv', 'rtl/banked_state_sram.sv',
                    'rtl/dram_weight_streamer.sv', 'rtl/shared_fetch_replay.sv',
                    'rtl/shared_fetch_replay_double.sv', 'tb/ramulator_dpi_bridge.sv',
                    'tb/ramulator_node_frontend.sv', 'tb/ramulator_dpi.cpp']
        if top in ('shared_fetch_two_h1_tb', 'shared_fetch_single_h1_tb'):
            args += ['+define+AZILLA_TIMING_ONLY']
    else:
        args += ['-LDFLAGS', '-no-pie']
    sources += [f'tb/{top}.sv']
    hashes = {s: hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources}
    save(out/'sources.json', hashes)
    save(out/'command.json', args+sources)
    command(args+sources, out/'build.log')
    return out/'simv'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args(); out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ.update(VCS_ARCH_OVERRIDE='linux', VCS_TARGET_ARCH='linux64')
    os.environ.setdefault('VCS_HOME', '/home/hwpeng/vcs')
    report = dict(status='running', stage='component', started=time.time(), cases=[])
    save(out/'status.json', report)
    try:
        components = {}
        for mode in ('buffered', 'streaming'):
            binary = build(out/'component'/mode, mode, 'core_shared_mvm_vcs_tb')
            log = out/'component'/mode/'run.log'
            command([binary], log)
            text = log.read_text()
            assert 'PASS CORE_SHARED_MVM' in text
            components[mode] = re.findall(r'CORE_SHARED_ITER iteration=(\d+) cycle=\d+ state=(\w+) expected=(\w+)', text)
        assert components['buffered'] == components['streaming']
        report['component_arithmetic'] = 'pass: 3 iterations, signed extrema, transpose, gaps, nonzero noise'
        report['stage'] = 'shared_fetch_rtl'
        save(out/'status.json', report)
        dataset = out/'dataset.txt'
        command([sys.executable, 'scripts/generate_core_iteration_dataset.py', dataset], out/'dataset.log')
        for jobs in (2, 8, 32):
            bins = {mode: build(out/f'jobs{jobs}'/mode, mode,
                    'shared_fetch_publication_vcs_tb', [('ENGINES',4), ('H0_ENGINES',4),
                    ('H1_ENGINES',2), ('CROSS_ENGINES',4), ('DOUBLE_BUFFER',1),
                    ('JOBS_PER_SOURCE',jobs)]) for mode in ('buffered','streaming')}
            for stall in (0,7,13):
                rows = {}
                for mode,binary in bins.items():
                    log = out/f'jobs{jobs}'/mode/f'stall{stall}.log'
                    command([binary, f'+DATASET={dataset}', f'+STALL_PERIOD={stall}'], log)
                    text = log.read_text()
                    match = re.search(r'PASS CORE_ITERATION_PUBLICATION cycles=(\d+)', text)
                    assert match, log
                    command([sys.executable, 'scripts/check_shared_fetch_arithmetic.py', log, dataset],
                            log.with_suffix('.audit.log'))
                    states = re.findall(r'CORE_RESULT iteration=(\d+) core=(\d+) state=(\w+)', text)
                    rows[mode] = dict(cycles=int(match[1]), states=states)
                assert rows['buffered']['states'] == rows['streaming']['states']
                report['cases'].append(dict(jobs_per_source=jobs, stall_period=stall,
                    buffered_cycles=rows['buffered']['cycles'], streaming_cycles=rows['streaming']['cycles'],
                    total_speedup=rows['buffered']['cycles']/rows['streaming']['cycles'],
                    arithmetic='independent reference and matched state trajectories pass',
                    scope='256 spins; two iterations; repeated-block stress if jobs > 2'))
                save(out/'status.json', report)
        report['status'] = 'rtl_gate_complete'
        report['stage'] = 'await_representative_workload_evaluation'
        report['finished'] = time.time()
        save(out/'status.json', report)
    except BaseException as e:
        report.update(status='failed', error=repr(e), finished=time.time())
        save(out/'status.json', report)
        raise


if __name__ == '__main__':
    main()
