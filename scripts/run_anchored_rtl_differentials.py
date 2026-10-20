#!/usr/bin/env python3
"""RTL (VCS) versus C++ exact-event differentials for CIR with H0-anchored
symmetric ownership (``--execution-mode cir-h0-anchored``), accumulate mode.

For every selected case this script
  1. writes the dataset (``tb/datasets/g256_smoke.txt`` or one of the
     generated block-occupancy families of scripts/check_cpp_exact_model.py)
     into ``<out>/sim_root/tb/datasets``;
  2. runs ``build/cpp_exact_flat/azilla_exact`` with ``--transfer-trace``,
     ``--metrics-prefix`` and ``--dump-anchored-schedule`` (the owner
     assignment the testbench consumes);
  3. builds (once per configuration, ``nice -n 5``, one job) and runs the
     anchored VCS testbench ``tb/ising_mesh_anchored_tb.sv`` with the RTL set
     of rtl/experimental/anchored (see docs/anchored_rtl.md) and the NoC event
     dump; the run is functional unless ``--timing-only`` (the testbench
     then also checks the next-state vector against its golden model);
  4. compares, with zero tolerance, initialization/iteration/total cycles,
     every accepted NoC transfer tuple (cycle, scope, node, direction,
     packet_type, source_id, epoch, block_id, dest_x, dest_y, last) and the
     aggregate NoC flit/stall totals, following
     scripts/check_cpp_exact_vcs_trace.py.

Outputs under ``<out>``: ``cases/<case>/{cpp,rtl}/`` with commands, logs,
wall times and hashes, ``cases/<case>/comparison.json``, ``summary.json`` and
``REPORT.md``.  Runs are sequential (the machine is shared).  Evidence for the
listed small geometries only.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_exact_event_model import rtl_transfers  # noqa: E402
from check_16k_cycle_model import rtl_noc_totals  # noqa: E402
from check_cpp_exact_model import generate_dataset  # noqa: E402

BINARY = ROOT / 'build/cpp_exact_flat/azilla_exact'
LIBRARY = ROOT / 'build/cycle_model_ramulator/libazilla_ramulator.so'
YAML = ROOT / 'tb/ramulator_128x32.yaml'
COMPILER_ROOT = Path('/home/rsappidi/miniforge3/envs/azilla/bin')
RAMULATOR_DIR = ROOT / 'third_party/ramulator2'

# Compile order of the anchored build.  Files under rtl/experimental/anchored
# replace the base file of the same module name.
RTL_SOURCES = [
    'rtl/ising_pkg.sv', 'rtl/j_block_sram.sv', 'rtl/banked_state_sram.sv', 'rtl/mvm.sv',
    'rtl/symmetric_mvm.sv', 'rtl/core.sv', 'rtl/hierarchy_node.sv', 'rtl/h0_adapter.sv',
    'rtl/experimental/anchored/h0_uplink.sv',
    'rtl/experimental/anchored/uplink_injection_arbiter.sv',
    'rtl/experimental/anchored/h0_tile.sv',
    'rtl/experimental/anchored/h1_child_adapter.sv',
    'rtl/experimental/anchored/h1_tile.sv',
    'rtl/h1_noc_adapter.sv', 'rtl/cross_h1_node.sv', 'rtl/azilla_floo_router.sv',
    'rtl/experimental/anchored/top_node.sv',
    'rtl/experimental/anchored/mesh_h1_tile.sv',
    'rtl/experimental/anchored/ising_mesh.sv',
    'rtl/dram_weight_streamer.sv', 'tb/ramulator_dpi_bridge.sv', 'tb/ramulator_node_frontend.sv',
    'tb/ising_mesh_anchored_tb.sv', 'tb/ramulator_dpi.cpp',
]
MODEL_SOURCES = sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'model/cpp_exact').glob('*.[ch]pp'))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def cases():
    """(name, dataset, geometry (x, y, h0, cores), engines (h0, h1, cross))."""
    c = []
    c.append(dict(name='smoke256_2x1_h2c2_e1', dataset='g256_smoke.txt', geometry=(2, 1, 2, 2), engines=(1, 1, 1)))
    c.append(dict(name='smoke256_2x1_h2c2_e2', dataset='g256_smoke.txt', geometry=(2, 1, 2, 2), engines=(2, 1, 1)))
    c.append(dict(name='dense512_2x1_h2c4_e1', family='dense', geometry=(2, 1, 2, 4), engines=(1, 1, 1)))
    c.append(dict(name='dense512_2x1_h2c4_e2', family='dense', geometry=(2, 1, 2, 4), engines=(2, 2, 4)))
    c.append(dict(name='dense512_2x1_h2c4_e4', family='dense', geometry=(2, 1, 2, 4), engines=(4, 2, 4)))
    c.append(dict(name='mod3_512_2x1_h2c4_e1', family='mod3', geometry=(2, 1, 2, 4), engines=(1, 1, 1)))
    c.append(dict(name='mod3_512_2x1_h2c4_e4', family='mod3', geometry=(2, 1, 2, 4), engines=(4, 2, 4)))
    c.append(dict(name='dense512_2x2_h1c4_e2', family='dense', geometry=(2, 2, 1, 4), engines=(2, 2, 4)))
    c.append(dict(name='dense512_2x2_h1c4_e4', family='dense', geometry=(2, 2, 1, 4), engines=(4, 2, 4)))
    c.append(dict(name='sparse2048_4x4_h2c2_e1', family='sparse', geometry=(4, 4, 2, 2), engines=(1, 1, 1)))
    c.append(dict(name='sparse2048_4x4_h2c2_e2', family='sparse', geometry=(4, 4, 2, 2), engines=(2, 2, 4)))
    # Additional coverage: one-packet uplink FIFOs (flush backpressure), the
    # uneven/sparse families, a 4x2 mesh, the timing-only compile path and
    # the FIFO (non-accumulate) uplink mode.  Per-case keys override the
    # command-line --uplink-packets / --accumulate / --timing-only.
    c.append(dict(name='dense512_2x1_h2c4_e4_p1', family='dense', geometry=(2, 1, 2, 4), engines=(4, 2, 4), packets=1))
    c.append(dict(name='dense512_2x2_h1c4_e4_p1', family='dense', geometry=(2, 2, 1, 4), engines=(4, 2, 4), packets=1))
    c.append(dict(name='mod3_512_2x1_h2c4_e4_p1', family='mod3', geometry=(2, 1, 2, 4), engines=(4, 2, 4), packets=1))
    c.append(dict(name='uneven512_2x1_h2c4_e4', family='uneven', geometry=(2, 1, 2, 4), engines=(4, 2, 4)))
    c.append(dict(name='sparse512_2x1_h2c4_e2', family='sparse', geometry=(2, 1, 2, 4), engines=(2, 2, 4)))
    c.append(dict(name='dense1024_4x2_h1c4_e4', family='dense', geometry=(4, 2, 1, 4), engines=(4, 2, 4)))
    c.append(dict(name='dense512_2x1_h2c4_e4_timingonly', family='dense', geometry=(2, 1, 2, 4), engines=(4, 2, 4),
                  timing_only=True))
    c.append(dict(name='smoke256_2x1_h2c2_e1_fifo', dataset='g256_smoke.txt', geometry=(2, 1, 2, 2), engines=(1, 1, 1),
                  accumulate=False))
    c.append(dict(name='dense512_2x1_h2c4_e4_fifo', family='dense', geometry=(2, 1, 2, 4), engines=(4, 2, 4),
                  accumulate=False))
    # One H0 (global H0 1) owns no block at all: exercises the zero-fill start
    # (state_fill_done_i together with iter_start) and an immediately-done node.
    c.append(dict(name='idleh0_256_2x1_h2c2_e1', family='idle-h0', geometry=(2, 1, 2, 2), engines=(1, 1, 1)))
    # Nominal geometries, fully dense, header-only dataset (the C++ side takes
    # --dense-all-blocks; the testbench takes every job from the dumped
    # schedule), timing-only, MT Ramulator bridge for the C++ side.  Not part
    # of the default list: run with --only.
    c.append(dict(name='dense65536_1x1_h8c256_e4', dataset_header=65536, geometry=(1, 1, 8, 256), engines=(4, 2, 4),
                  timing_only=True, dense_all_blocks=True, threads=1, default=False))
    c.append(dict(name='dense131072_2x1_h8c256_e4', dataset_header=131072, geometry=(2, 1, 8, 256), engines=(4, 2, 4),
                  timing_only=True, dense_all_blocks=True, threads=1, default=False))
    # Reduced reproductions of the nominal-scale case: the H0 state fill
    # (distinct sources per H0, 128/512 cycles) is longer than the
    # publication phase, so fill, dispatch and node start overlap as at 64K.
    c.append(dict(name='dense4096_1x1_h2c64_e4', dataset_header=4096, geometry=(1, 1, 2, 64), engines=(4, 2, 4),
                  timing_only=True, dense_all_blocks=True, threads=1))
    c.append(dict(name='dense16384_1x1_h2c256_e4', dataset_header=16384, geometry=(1, 1, 2, 256), engines=(4, 2, 4),
                  timing_only=True, dense_all_blocks=True, threads=1, default=False))
    return c


def generate_idle_h0_dataset(blocks, cores_per_h0, path):
    """Dense occupancy over every block except those of global H0 1."""
    skip = set(range(cores_per_h0, 2 * cores_per_h0))
    pairs = [(a, b) for a in range(blocks) for b in range(a + 1, blocks) if a not in skip and b not in skip]
    with path.open('w') as handle:
        handle.write(f'{blocks * 32} 0\n')
        for a, b in pairs:
            u, v = a * 32 + 1, b * 32 + 1
            handle.write(f'{u} {v} -1\n{v} {u} -1\n')
    return len(pairs)


def config_name(case, accumulate, packets, timing_only):
    # (dense_all_blocks cases get their own VCS build: SKIP_ZERO_BLOCKS differs)
    x, y, h, cc = case['geometry']
    e0, e1, e2 = case['engines']
    dense = '_dense' if case.get('dense_all_blocks') else ''
    return f'{x}x{y}_h0{h}_c{cc}_e{e0}-{e1}-{e2}_f4_r1_i1_t{int(timing_only)}_a{int(accumulate)}_p{packets}{dense}'


def vcs_command(case, build_dir, accumulate, packets, timing_only, vcs_home):
    x, y, h, cc = case['geometry']
    e0, e1, e2 = case['engines']
    pvalue = [('MESH_X_COUNT', x), ('MESH_Y_COUNT', y), ('H0_COUNT', h), ('CORES_PER_H0', cc),
              ('H0_MVM_COUNT', e0), ('H1_MVM_COUNT', e1), ('CROSS_MVM_COUNT', e2), ('FIFO_DEPTH', 4),
              ('SKIP_ZERO_BLOCKS', 0 if case.get('dense_all_blocks') else 1), ('USE_RAMULATOR', 1), ('MEM_PIN_COUNT', 128), ('MEM_PIN_GBPS', 32),
              ('MEM_LANES', 16), ('ITERATION_COUNT', 1), ('UPLINK_PACKETS', packets),
              ('ACCUMULATE', int(accumulate))]
    return [str(Path(vcs_home) / 'bin/vcs'), '-full64', '-sverilog', '-j', '1',
            '-cpp', str(COMPILER_ROOT / 'x86_64-conda-linux-gnu-g++'),
            '-cc', str(COMPILER_ROOT / 'x86_64-conda-linux-gnu-gcc'),
            '-assert', 'svaext', '-assert', 'disable_cover', '-timescale=1ns/1ps',
            *(['+define+AZILLA_TIMING_ONLY'] if timing_only else []),
            '-top', 'ising_mesh_anchored_tb', f'-Mdir={build_dir / "csrc"}', '-o', str(build_dir / 'simv'),
            *[f'-pvalue+ising_mesh_anchored_tb.{k}={v}' for k, v in pvalue],
            '-CFLAGS', f'-std=c++20 -I{RAMULATOR_DIR / "src"}',
            '-LDFLAGS', f'-no-pie -L{RAMULATOR_DIR} -lramulator -Wl,-rpath,{RAMULATOR_DIR}',
            '-f', 'rtl/floo_router_files.f', *RTL_SOURCES]


def run_logged(command, log_path, cwd, env=None, nice=5):
    full = ['nice', '-n', str(nice), '/usr/bin/time', '-v', '-o', str(log_path) + '.time', *command]
    start = time.perf_counter()
    with Path(log_path).open('w') as log:
        proc = subprocess.run(full, cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT)
    return proc.returncode, time.perf_counter() - start


def parse_time(path):
    text = Path(path).read_text() if Path(path).exists() else ''
    wall = re.search(r'Elapsed \(wall clock\) time.*?: (.*)', text)
    rss = re.search(r'Maximum resident set size \(kbytes\): (\d+)', text)
    seconds = None
    if wall:
        parts = wall.group(1).strip().split(':')
        seconds = sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))
    return dict(wall_seconds=seconds, max_rss_kb=int(rss.group(1)) if rss else None)


def cpp_transfers(path):
    return rtl_transfers(Path(path))  # same schema as the RTL event CSV


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--output', type=Path, default=ROOT / 'results/anchored_rtl_20261015')
    ap.add_argument('--only', nargs='*', help='case names to run (default: all)')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--timing-only', action='store_true', help='compile with AZILLA_TIMING_ONLY (skip the golden state check)')
    ap.add_argument('--accumulate', type=int, default=1)
    ap.add_argument('--uplink-packets', type=int, default=4)
    ap.add_argument('--vcs-home', default=os.environ.get('VCS_HOME', '/home/hwpeng/vcs'))
    ap.add_argument('--rebuild', action='store_true', help='rebuild the VCS binary even if one exists')
    ap.add_argument('--skip-report', action='store_true')
    ap.add_argument('--nice', type=int, default=5, help='nice level for the C++ and VCS processes (default 5)')
    a = ap.parse_args()
    selected = [c for c in cases() if (a.only is None and c.get('default', True)) or (a.only is not None and c['name'] in a.only)]
    if a.list:
        for c in selected:
            print(c['name'], c['geometry'], c['engines'])
        return
    out = a.output
    sim_root = out / 'sim_root'
    (sim_root / 'tb/datasets').mkdir(parents=True, exist_ok=True)
    if not (sim_root / 'tb/ramulator_128x32.yaml').exists():
        shutil.copy(YAML, sim_root / 'tb/ramulator_128x32.yaml')
    (out / 'cases').mkdir(exist_ok=True)
    (out / 'build').mkdir(exist_ok=True)
    summary_path = out / 'summary.json'
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else dict(cases={})
    env = dict(os.environ, VCS_HOME=a.vcs_home, VCS_ARCH_OVERRIDE='linux', VCS_TARGET_ARCH='linux64',
               PYTHONDONTWRITEBYTECODE='1')
    source_hashes = {p: sha256(ROOT / p) for p in RTL_SOURCES + MODEL_SOURCES + ['rtl/floo_router_files.f']}
    source_hashes['binary'] = sha256(BINARY)
    source_hashes['library'] = sha256(LIBRARY)
    source_hashes['yaml'] = sha256(YAML)

    for case in selected:
        name = case['name']
        x, y, h, cc = case['geometry']
        e0, e1, e2 = case['engines']
        blocks = x * y * h * cc
        case_dir = out / 'cases' / name
        cpp_dir, rtl_dir = case_dir / 'cpp', case_dir / 'rtl'
        for d in (cpp_dir, rtl_dir):
            d.mkdir(parents=True, exist_ok=True)
        accumulate = bool(case.get('accumulate', bool(a.accumulate)))
        packets = int(case.get('packets', a.uplink_packets))
        timing_only = bool(case.get('timing_only', a.timing_only))
        entry = dict(case={k: v for k, v in case.items()}, accumulate=accumulate, uplink_packets=packets,
                     timing_only=timing_only, status='unknown', checks={})
        print(f'=== {name} geometry={case["geometry"]} engines={case["engines"]}', flush=True)

        # 1. dataset
        if 'dataset_header' in case:
            dataset_name = f'dense_{case["dataset_header"]}_header.txt'
            (sim_root / 'tb/datasets' / dataset_name).write_text(f'{case["dataset_header"]} 0\n')
        elif 'dataset' in case:
            dataset_name = case['dataset']
            shutil.copy(ROOT / 'tb/datasets' / dataset_name, sim_root / 'tb/datasets' / dataset_name)
        else:
            dataset_name = f'{case["family"]}_{blocks * 32}.txt'
            if case['family'] == 'idle-h0':
                generate_idle_h0_dataset(blocks, cc, sim_root / 'tb/datasets' / dataset_name)
            else:
                generate_dataset(case['family'], blocks, sim_root / 'tb/datasets' / dataset_name)
        dataset = sim_root / 'tb/datasets' / dataset_name
        entry['dataset'] = dict(name=dataset_name, path=str(dataset), sha256=sha256(dataset))

        # 2. C++ model
        schedule = cpp_dir / 'schedule.txt'
        cpp_cmd = [str(BINARY), 'simulate-exact-events', '--execution-mode', 'cir-h0-anchored',
                   '--anchored-accumulate', str(int(accumulate)), '--anchored-uplink-packets', str(packets),
                   '--dataset', str(dataset), '--mesh-x', str(x), '--mesh-y', str(y), '--h0-per-h1', str(h),
                   '--cores-per-h0', str(cc), '--h0-mvms', str(e0), '--h1-mvms', str(e1), '--cross-mvms', str(e2),
                   '--ramulator-library', str(LIBRARY), '--ramulator-config', str(YAML),
                   '--metrics-prefix', str(cpp_dir / 'metrics'), '--transfer-trace', str(cpp_dir / 'transfers.csv'),
                   '--dump-anchored-schedule', str(schedule)]
        if case.get('dense_all_blocks'):
            cpp_cmd += ['--dense-all-blocks', '1']
        if case.get('threads', 1) > 1:
            cpp_cmd[cpp_cmd.index('--ramulator-library') + 1] = str(ROOT / 'build/cpp_exact/libazilla_ramulator_mt.so')
            cpp_cmd += ['--dram-threads', str(case['threads'])]
        (cpp_dir / 'command.json').write_text(json.dumps(cpp_cmd, indent=2) + '\n')
        rc, wall = run_logged(cpp_cmd, cpp_dir / 'run.log', ROOT, nice=a.nice)
        entry['cpp'] = dict(returncode=rc, harness_wall_seconds=round(wall, 3), **parse_time(cpp_dir / 'run.log.time'))
        entry['checks']['cpp_exit'] = rc == 0
        if rc != 0:
            entry['status'] = 'fail'
            summary['cases'][name] = entry
            summary_path.write_text(json.dumps(summary, indent=2) + '\n')
            print('FAIL (cpp)', name, flush=True)
            continue
        model = json.loads((cpp_dir / 'metrics_summary.json').read_text())
        t = model['timing_cycles']
        model_timing = (int(t['initialization']), int(t['iteration']), int(t['total']))
        n = model['noc']
        model_noc = (int(n['injected_flits']), int(n['ejected_flits']), int(n['physical_link_flits']),
                     int(n['injection_stalls']), int(n['ejection_stalls']), int(n['link_stalls']))
        entry['model'] = dict(timing=model_timing, noc=model_noc, jobs=model.get('scheduled_jobs'),
                              anchored=model.get('anchored'), schedule_sha256=sha256(schedule))

        # 3. VCS build (cached per configuration) and run
        config = config_name(case, accumulate, packets, timing_only)
        build_dir = out / 'build' / config
        binary = build_dir / 'simv'
        build_record = build_dir / 'build.json'
        need_build = a.rebuild or not binary.exists()
        if not need_build and build_record.exists():
            rec = json.loads(build_record.read_text())
            need_build = any(rec.get('source_sha256', {}).get(p) != source_hashes[p] for p in RTL_SOURCES)
        if need_build:
            if build_dir.exists():
                shutil.rmtree(build_dir)
            build_dir.mkdir(parents=True)
            cmd = vcs_command(case, build_dir, accumulate, packets, timing_only, a.vcs_home)
            (build_dir / 'build.command.json').write_text(json.dumps(cmd, indent=2) + '\n')
            rc, wall = run_logged(cmd, build_dir / 'build.log', ROOT, env=env, nice=a.nice)
            build_record.write_text(json.dumps(dict(
                config=config, returncode=rc, harness_wall_seconds=round(wall, 3),
                **parse_time(build_dir / 'build.log.time'),
                source_sha256={p: source_hashes[p] for p in RTL_SOURCES}), indent=2) + '\n')
            if rc != 0 or not binary.exists():
                entry['checks']['vcs_build'] = False
                entry['status'] = 'fail'
                summary['cases'][name] = entry
                summary_path.write_text(json.dumps(summary, indent=2) + '\n')
                print('FAIL (vcs build)', name, flush=True)
                continue
        entry['checks']['vcs_build'] = True
        entry['build'] = json.loads(build_record.read_text())
        entry['build']['simv_sha256'] = sha256(binary)

        rtl_log = rtl_dir / 'rtl.log'
        events = rtl_dir / 'events.csv'
        noc = rtl_dir / 'noc.csv'
        internal = rtl_dir / 'internal.csv'
        for stale in (rtl_log, events, noc, internal):
            if stale.exists():
                stale.unlink()
        rtl_cmd = [str(binary), f'+DATASET={dataset_name}', f'+ANCHORED_SCHEDULE={schedule}',
                   f'+NOC_STATS_FILE={noc}', f'+NOC_EVENT_FILE={events}', f'+INTERNAL_STATS_FILE={internal}']
        (rtl_dir / 'command.json').write_text(json.dumps(rtl_cmd, indent=2) + '\n')
        rc, wall = run_logged(rtl_cmd, rtl_log, sim_root, env=env, nice=a.nice)
        entry['rtl'] = dict(returncode=rc, harness_wall_seconds=round(wall, 3), **parse_time(str(rtl_log) + '.time'))
        log = rtl_log.read_text(errors='replace') if rtl_log.exists() else ''
        init = re.search(r'initialization complete at cycle (\d+)', log)
        total = re.search(r'total_cycles=(\d+)', log)
        rtl_pass = bool(re.search(r'^PASS: 1 iteration', log, re.M))
        rtl_errors = bool(re.search(r'Error|Fatal|mismatch', log))
        entry['checks']['rtl_exit'] = rc == 0 and rtl_pass and not rtl_errors
        entry['checks']['rtl_state_check'] = (not timing_only) and bool(re.search(r'iteration=0 PASS', log))
        if timing_only:
            entry['checks'].pop('rtl_state_check')
        rtl_timing = rtl_noc = None
        if init and total:
            rtl_timing = (int(init[1]), int(total[1]) - int(init[1]), int(total[1]))
        if noc.exists():
            rtl_noc = rtl_noc_totals(noc)
        rtl_jobs = re.search(r'compiled concurrent schedule h0=(\d+) h1=(\d+) cross=(\d+)', log)
        entry['rtl_timing'] = rtl_timing
        entry['rtl_noc'] = rtl_noc
        entry['rtl_jobs'] = [int(g) for g in rtl_jobs.groups()] if rtl_jobs else None

        # 4. compare
        entry['checks']['timing'] = rtl_timing == model_timing
        entry['checks']['noc'] = rtl_noc == model_noc
        expected = rtl_transfers(events) if events.exists() else []
        observed = cpp_transfers(cpp_dir / 'transfers.csv')
        entry['checks']['transfers'] = bool(expected) and expected == observed
        entry['transfers'] = dict(rtl=len(expected), model=len(observed))
        if expected != observed:
            i = next((i for i, (p, q) in enumerate(zip(expected, observed)) if p != q), min(len(expected), len(observed)))
            entry['first_difference'] = dict(index=i, rtl=expected[i:i + 1], model=observed[i:i + 1])
        mj = model.get('scheduled_jobs') or {}
        entry['checks']['jobs'] = entry['rtl_jobs'] is not None and \
            entry['rtl_jobs'] == [int(mj.get('h0', -1)), int(mj.get('h1', -1)), int(mj.get('cross', -1))]
        entry['checks']['sources_unchanged'] = all(sha256(ROOT / p) == v for p, v in source_hashes.items()
                                                  if p not in ('binary', 'library', 'yaml')) and \
            sha256(BINARY) == source_hashes['binary']
        entry['status'] = 'pass' if all(entry['checks'].values()) else 'fail'
        entry['source_sha256'] = source_hashes
        for f in (events, noc, internal, cpp_dir / 'transfers.csv', cpp_dir / 'metrics_summary.json', rtl_log):
            if f.exists():
                entry.setdefault('artifact_sha256', {})[str(f.relative_to(out))] = sha256(f)
        (case_dir / 'comparison.json').write_text(json.dumps(entry, indent=2, default=str) + '\n')
        summary['cases'][name] = entry
        summary_path.write_text(json.dumps(summary, indent=2, default=str) + '\n')
        print(entry['status'].upper(), name, 'rtl', rtl_timing, 'model', model_timing, 'transfers',
              entry['transfers'], 'checks', {k: v for k, v in entry['checks'].items() if not v}, flush=True)

    if not a.skip_report:
        write_report(out, summary)


def write_report(out, summary):
    lines = ['# Anchored CIR RTL versus C++ exact-event model: differential results', '',
             'Generated by scripts/run_anchored_rtl_differentials.py (tables only; see the',
             'hand-written sections below the marker for method and scope).', '',
             '<!-- table:start -->',
             '| case | geometry (x,y,h0,cores) | spins | engines h0/h1/cross | mode | jobs | RTL init/iter/total | model init/iter/total | transfers RTL/model | NoC totals equal | state check | status | VCS build s | VCS run s | C++ s |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for name, e in summary['cases'].items():
        g = e['case']['geometry']
        eng = e['case']['engines']
        spins = g[0] * g[1] * g[2] * g[3] * 32
        rt = '/'.join(str(v) for v in e['rtl_timing']) if e.get('rtl_timing') else '-'
        mt = '/'.join(str(v) for v in e['model']['timing']) if e.get('model') else '-'
        tr = f"{e['transfers']['rtl']}/{e['transfers']['model']}" if e.get('transfers') else '-'
        checks = e.get('checks', {})
        mode = ('acc' if e.get('accumulate') else 'fifo') + f"/p{e.get('uplink_packets')}"
        lines.append(f"| {name} | {g[0]},{g[1]},{g[2]},{g[3]} | {spins} | {eng[0]}/{eng[1]}/{eng[2]} | {mode} | "
                     f"{(e.get('rtl_jobs') or ['-'])[0]} | {rt} | {mt} | {tr} | {checks.get('noc')} | "
                     f"{checks.get('rtl_state_check', 'timing-only')} | {e['status']} | "
                     f"{(e.get('build') or {}).get('wall_seconds', '-')} | {(e.get('rtl') or {}).get('wall_seconds', '-')} | "
                     f"{(e.get('cpp') or {}).get('wall_seconds', '-')} |")
    lines.append('<!-- table:end -->')
    report = out / 'REPORT.md'
    if report.exists():
        text = report.read_text()
        if '<!-- table:start -->' in text and '<!-- table:end -->' in text:
            head = text[:text.index('<!-- table:start -->')]
            tail = text[text.index('<!-- table:end -->') + len('<!-- table:end -->'):]
            table = '\n'.join(lines[lines.index('<!-- table:start -->'):])
            report.write_text(head + table + tail)
            return
    report.write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
