#!/usr/bin/env python3
"""GPU baseline for one dense discrete-SB step: int8 J (N x N) times a
+-1 spin vector, then the dSB position/momentum update.  Reports time per
step and achieved weight-stream bandwidth, which is what a dense Ising step
is bound by on a GPU.  Run on any CUDA machine with PyTorch:

    python3 scripts/gpu_dsb_step_benchmark.py --sizes 16384 32768 65536

Notes for comparison with the AZilla model: this kernel reads the full
N x N matrix (N^2 bytes) per step, as a row-slab dSB implementation does;
the anchored accumulate design reads N^2/2 (each off-diagonal block once).
The GEMV is done as int8 x int8 -> int32 through torch.matmul on a
(N, N) x (N, 1) problem; on Hopper/Ampere this runs on tensor cores but is
memory-bound, so the reported bandwidth is the figure of merit.  Also
reports a plain fp16 GEMV for implementations that store J in half
precision (2x the bytes).
"""
import argparse, json, time, sys

import torch


def dsb_step(J, x, p, dt, a, c0):
    # Discrete SB (Goto et al. 2021): momentum update uses sign(x).
    s = torch.sign(x).to(J.dtype)
    h = (J @ s).to(torch.float32).squeeze(1)          # int32 accumulate
    p = p + dt * ((-1.0 + a) * x + c0 * h)
    x = x + dt * p
    wall = x.abs() > 1.0
    x = torch.where(wall, torch.sign(x), x)
    p = torch.where(wall, torch.zeros_like(p), p)
    return x, p


def bench(n, dtype, iters, device):
    torch.manual_seed(0)
    J = torch.randint(-127, 128, (n, n), dtype=torch.int8, device=device)
    if dtype == 'fp16':
        J = J.to(torch.float16)
    x = (torch.rand(n, device=device) - 0.5) * 0.1
    p = torch.zeros(n, device=device)
    dt, a, c0 = 0.5, 0.5, 1.0 / (n ** 0.5)
    for _ in range(3):
        x, p = dsb_step(J, x, p, dt, a, c0)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        x, p = dsb_step(J, x, p, dt, a, c0)
    torch.cuda.synchronize()
    step = (time.perf_counter() - t0) / iters
    bytes_per_step = n * n * J.element_size()
    return dict(spins=n, dtype=dtype, step_ms=step * 1e3, weight_bytes=bytes_per_step,
                achieved_TBps=bytes_per_step / step / 1e12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sizes', type=int, nargs='+', default=[16384, 32768, 65536])
    ap.add_argument('--iters', type=int, default=20)
    ap.add_argument('--dtypes', nargs='+', default=['int8', 'fp16'])
    ap.add_argument('--output', default=None)
    args = ap.parse_args()
    if not torch.cuda.is_available():
        sys.exit('no CUDA device')
    dev = torch.device('cuda')
    info = dict(gpu=torch.cuda.get_device_name(0), torch=torch.__version__,
                memory_GB=torch.cuda.get_device_properties(0).total_memory / 1e9, results=[])
    print(info['gpu'], f"{info['memory_GB']:.0f} GB")
    for n in args.sizes:
        for dt in args.dtypes:
            need = n * n * (1 if dt == 'int8' else 2)
            if need > 0.9 * info['memory_GB'] * 1e9:
                print(f'skip {n} {dt}: needs {need/1e9:.1f} GB'); continue
            r = bench(n, dt, args.iters, dev)
            info['results'].append(r)
            print(f"N={n:6d} {dt}: {r['step_ms']:.3f} ms/step, {r['achieved_TBps']:.2f} TB/s", flush=True)
            torch.cuda.empty_cache()
    if args.output:
        json.dump(info, open(args.output, 'w'), indent=1)


if __name__ == '__main__':
    main()
