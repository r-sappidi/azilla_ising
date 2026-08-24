#!/usr/bin/env python3
"""Compile an Ising dataset or mapped schedule into router-replay traffic."""

import argparse
import heapq
from collections import defaultdict
from pathlib import Path


SPINS_PER_BLOCK = 32
BLOCKS_PER_H0 = 32
NOC_STATE = 0
NOC_PARTIAL = 1
NOC_EPOCH_DONE = 2


def distance(a, b, width):
    return abs(a % width - b % width) + abs(a // width - b // width)


def allocate_pair_owners(node_count, width):
    pair_count = node_count * (node_count - 1) // 2
    capacities = [pair_count // node_count] * node_count
    extras = pair_count % node_count
    ranked = sorted(range(node_count),
                    key=lambda n: (sum(distance(n, o, width)
                                       for o in range(node_count)), n))
    for node in ranked[:extras]:
        capacities[node] += 1
    loads = [0] * node_count
    owners = {}
    pairs = [(a, b) for a in range(node_count)
             for b in range(a + 1, node_count)]
    pairs.sort(key=lambda pair: (-distance(pair[0], pair[1], width),
                                 pair[0], pair[1]))
    for a, b in pairs:
        candidates = [node for node in range(node_count)
                      if loads[node] < capacities[node]]
        owner = min(candidates, key=lambda node: (
            max(distance(node, a, width), distance(node, b, width)),
            distance(node, a, width) + distance(node, b, width),
            loads[node] / capacities[node], node))
        owners[(a, b)] = owner
        loads[owner] += 1
    return owners


def dataset_block_pairs(path):
    active = set()
    with path.open() as source:
        header = source.readline().split()
        if len(header) != 2:
            raise ValueError(f"invalid dataset header in {path}")
        spins = int(header[0])
        for line_number, line in enumerate(source, 2):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 3:
                raise ValueError(f"invalid record at {path}:{line_number}")
            a = (int(fields[0]) - 1) // SPINS_PER_BLOCK
            b = (int(fields[1]) - 1) // SPINS_PER_BLOCK
            if a != b:
                active.add(tuple(sorted((a, b))))
    return spins, sorted(active)


def mapped_pairs(path):
    pairs = []
    with path.open() as source:
        for line_number, line in enumerate(source, 1):
            fields = line.split()
            if not fields or fields[0].startswith("#"):
                continue
            if len(fields) != 3:
                raise ValueError(f"invalid schedule at {path}:{line_number}")
            pairs.append(tuple(map(int, fields)))
    return pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--schedule", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mesh-x", type=int, required=True)
    parser.add_argument("--mesh-y", type=int, required=True)
    parser.add_argument("--h0-per-h1", type=int, required=True)
    parser.add_argument("--cross-engines", type=int, default=16)
    parser.add_argument("--block-service-cycles", type=int, default=64,
                        help="Ramulator+streamer+MVM service time per block")
    args = parser.parse_args()

    if min(args.mesh_x, args.mesh_y, args.h0_per_h1,
           args.cross_engines, args.block_service_cycles) <= 0:
        parser.error("geometry, engine count, and service latency must be positive")
    node_count = args.mesh_x * args.mesh_y
    blocks_per_h1 = args.h0_per_h1 * BLOCKS_PER_H0
    expected_spins = node_count * blocks_per_h1 * SPINS_PER_BLOCK
    spins, active_pairs = dataset_block_pairs(args.dataset)
    if spins != expected_spins:
        parser.error(f"dataset has {spins} spins; geometry requires {expected_spins}")

    owners = allocate_pair_owners(node_count, args.mesh_x)
    if args.schedule:
        scheduled = mapped_pairs(args.schedule)
    else:
        scheduled = []
        for block_a, block_b in active_pairs:
            h1_a = block_a // blocks_per_h1
            h1_b = block_b // blocks_per_h1
            owner = -1 if h1_a == h1_b else owners[tuple(sorted((h1_a, h1_b)))]
            scheduled.append((block_a, block_b, owner))

    cross_jobs = []
    publications = set()
    h0_jobs = 0
    h1_jobs = 0
    for block_a, block_b, requested_owner in scheduled:
        h0_a, h0_b = block_a // BLOCKS_PER_H0, block_b // BLOCKS_PER_H0
        h1_a, h1_b = block_a // blocks_per_h1, block_b // blocks_per_h1
        if h0_a == h0_b:
            h0_jobs += 1
        elif h1_a == h1_b:
            h1_jobs += 1
        else:
            owner = requested_owner
            if owner < 0:
                owner = owners[tuple(sorted((h1_a, h1_b)))]
            if owner >= node_count:
                parser.error(f"owner {owner} outside {node_count}-node mesh")
            cross_jobs.append((block_a, block_b, owner))
            publications.add((block_a, owner))
            publications.add((block_b, owner))

    # phase, release, source, destination, packet type, block ID, flit count
    records = []
    per_source_release = defaultdict(int)
    for block, owner in sorted(publications):
        source = block // blocks_per_h1
        release = per_source_release[source]
        per_source_release[source] += 1
        records.append((0, release, source, owner, NOC_STATE, block, 1))

    engines = [[0] * args.cross_engines for _ in range(node_count)]
    heapq.heapify(engines[0])
    for node in range(1, node_count):
        heapq.heapify(engines[node])
    for block_a, block_b, owner in cross_jobs:
        available = heapq.heappop(engines[owner])
        completion = available + args.block_service_cycles
        heapq.heappush(engines[owner], completion)
        for block in (block_a, block_b):
            destination = block // blocks_per_h1
            records.append((1, completion, owner, destination,
                            NOC_PARTIAL, block, 4))

    for node in range(node_count):
        records.append((2, 0, node, node, NOC_EPOCH_DONE, 0, 1))

    records.sort(key=lambda row: (row[0], row[1], row[2], row[4], row[5]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as output:
        output.write(f"{args.mesh_x} {args.mesh_y} 2\n")
        for record in records:
            output.write(" ".join(map(str, record)) + "\n")

    print(f"wrote {len(records)} packets to {args.output}")
    print(f"geometry: h1={node_count} h0_per_h1={args.h0_per_h1} "
          f"spins={expected_spins}")
    print(f"scheduled blocks: h0={h0_jobs} h1={h1_jobs} "
          f"cross={len(cross_jobs)} state_publications={len(publications)}")


if __name__ == "__main__":
    main()
