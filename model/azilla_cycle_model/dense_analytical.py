"""Dense v3 work/traffic equations and resource bounds, not a timing simulator.

One iteration, contiguous active blocks, 32 spins/block, direct XY mesh,
canonical shared fetch, two-slot cores-only forwarding. No fitted constants.
Only CIR has a provisional steady-state latency estimate; resource lower
bounds must never be plotted as achieved latency or divided into speedups.
"""
from collections import Counter
from dataclasses import asdict
from math import ceil, isqrt

from .scheduler import allocate_h1_pairs
from .workload import Geometry


def choose2(n):
    return n * (n - 1) // 2


def fixed_h1_geometry(spins):
    nodes = max(1, ceil(spins / 65536))
    y = isqrt(nodes)
    while nodes % y:
        y -= 1
    return Geometry(nodes // y, y, 8, 256)


def xy_path(g, source, destination):
    """Directed edges, X before Y, matching FlooRouter.route."""
    x, y = source % g.mesh_x, source // g.mesh_x
    dx, dy = destination % g.mesh_x, destination // g.mesh_x
    while x != dx:
        nx = x + (1 if dx > x else -1)
        yield (y * g.mesh_x + x, y * g.mesh_x + nx)
        x = nx
    while y != dy:
        ny = y + (1 if dy > y else -1)
        yield (y * g.mesh_x + x, ny * g.mesh_x + x)
        y = ny


def dense_model(spins, mode="cir", geometry=None, engines=(4, 2, 4)):
    if spins <= 0 or spins % 32:
        raise ValueError("positive, block-aligned spin count required")
    if mode not in ("cir", "cores-only"):
        raise ValueError("only CIR and canonical shared-fetch v3 cores-only supported")
    g = geometry or fixed_h1_geometry(spins)
    if min(g.mesh_x, g.mesh_y, g.h0_per_h1, g.cores_per_h0, *engines) <= 0:
        raise ValueError("positive geometry/engine counts required")
    b = spins // 32
    if b > g.total_blocks:
        raise ValueError("active spins exceed allocated geometry")
    node_b = [max(0, min(g.blocks_per_h1, b - n*g.blocks_per_h1))
              for n in range(g.node_count)]
    h0_b = [[max(0, min(g.cores_per_h0, nb - h*g.cores_per_h0))
             for h in range(g.h0_per_h1)] for nb in node_b]
    jobs = {"h0": [choose2(c) for row in h0_b for c in row],
            "h1": [choose2(nb)-sum(choose2(c) for c in row)
                   for nb, row in zip(node_b, h0_b)],
            "cross": [0]*g.node_count}
    owners = allocate_h1_pairs(g)
    # Counts of flits on source/destination flows, not individual jobs/flits.
    flows = {k: Counter() for k in ("state", "partial", "weight", "epoch_done")}
    publication_groups = set()
    for a in range(g.node_count):
        for d in range(a+1, g.node_count):
            j = node_b[a]*node_b[d]
            if not j:
                continue
            owner = owners[a, d]
            jobs["cross"][owner] += j
            if mode == "cir":
                publication_groups.update(((a, owner), (d, owner)))
            else:
                publication_groups.update(((a, d), (d, a)))
            kind, size = ("partial", 4) if mode == "cir" else ("weight", 32)
            for destination in (a, d):
                flows[kind][owner, destination] += size*j
    # Deduplicate BEFORE counting: a source's states can serve many pairs
    # at the same owner. CIR also injects owner-local publications.
    for source, destination in publication_groups:
        flows["state"][source, destination] += node_b[source]
    for n in range(g.node_count):
        flows["epoch_done"][n, n] = 1
    injected, ejected, links, hops = Counter(), Counter(), Counter(), Counter()
    per_kind = {}
    for kind, entries in flows.items():
        per_kind[kind] = sum(entries.values())
        for (s, d), count in entries.items():
            injected[s] += count
            ejected[d] += count
            path = list(xy_path(g, s, d))
            hops[len(path)] += count
            for edge in path:
                links[edge] += count
    jtotal = choose2(b)
    assert sum(map(sum, jobs.values())) == jtotal
    # Minimum aggregate service demand; phases are NOT assumed additive.
    bounds = {"mesh_injection": max(injected.values(), default=0),
              "mesh_ejection": max(ejected.values(), default=0),
              "directed_link": max(links.values(), default=0)}
    for level, e in zip(("h0", "h1", "cross"), engines):
        # 32 row arithmetic operations in CIR; 64 outgoing weight beats in
        # cores-only, one beat/lane/cycle. Excludes positive control overhead.
        work = 32 if mode == "cir" else 64
        bounds[level+"_service"] = work*max((ceil(j/e) for j in jobs[level]), default=0)
    if mode == "cores-only":
        bounds["core_arithmetic"] = 32*(b-1)
        # H1-local and cross traffic share each child's one-beat input.
        bounds["h1_child_delivery"] = max(
            (32*c*(b-c) for row in h0_b for c in row), default=0)
    # Ideal steady-state CIR period: 38 cycles with distinct state banks;
    # 39 if both operands share a bank. Dense per-resource conflict fraction
    # is derived from block index residues, not fitted to latency results.
    same = {"h0": [], "h1": [], "cross": [0]*g.node_count}
    residues = []
    for n, row in enumerate(h0_b):
        hres = []
        for h, count in enumerate(row):
            base = n*g.blocks_per_h1+h*g.cores_per_h0
            r = [count//8]*8
            for k in range(count % 8):
                r[(base+k) % 8] += 1
            hres.append(r)
            same["h0"].append(sum(choose2(v) for v in r))
        nr = [sum(r[k] for r in hres) for k in range(8)]
        residues.append(nr)
        same["h1"].append(sum(choose2(v) for v in nr)
                          - sum(sum(choose2(v) for v in r) for r in hres))
    for a in range(g.node_count):
        for d in range(a+1, g.node_count):
            same["cross"][owners[a, d]] += sum(x*y for x, y in zip(residues[a], residues[d]))
    estimate = None
    if mode == "cir":
        service = [ceil((38*j+s)/e)
                   for level, e in zip(("h0", "h1", "cross"), engines)
                   for j, s in zip(jobs[level], same[level])]
        estimate = max(*service, bounds["mesh_injection"],
                       bounds["mesh_ejection"], bounds["directed_link"])
    return dict(schema="dense-analytical-v3-1", spins=spins, mode=mode,
                geometry=asdict(g), engines=list(engines), allocated_spins=g.spin_count,
                active_blocks=b, canonical_jobs=jtotal,
                jobs={k: sum(v) for k, v in jobs.items()}, jobs_per_resource=jobs,
                same_bank_jobs_per_resource=same,
                canonical_weight_bytes=1024*jtotal,
                diagonal_weight_storage_bytes=1024*b,
                diagonal_storage_scope="full 32x32 SRAM allocation per active core; not canonical DRAM jobs",
                dram_row_requests=32*jtotal,
                hierarchy_result_or_weight_payload_bytes=jtotal*(256 if mode == "cir" else 2048),
                traffic_by_type=per_kind, injected_flits=sum(injected.values()),
                physical_link_flits=sum(links.values()),
                directed_links={f"{s}->{d}": v for (s,d),v in sorted(links.items())},
                injection_by_node=dict(injected), ejection_by_node=dict(ejected),
                hop_histogram=dict(hops), initialization_cycles=3+33*g.total_blocks,
                iteration_lower_bound_cycles=max(bounds.values()), resource_bounds=bounds,
                cir_steady_state_estimate_cycles=estimate,
                timing_scope="analytical projection; lower bounds are not measured latency; CIR estimate omits startup, DRAM and contention penalties")
