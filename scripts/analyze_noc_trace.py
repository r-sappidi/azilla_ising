#!/usr/bin/env python3
"""Summarize Azilla NoC interval and event CSV traces."""

import argparse
import csv
from collections import defaultdict, deque
from pathlib import Path


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, fieldnames, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def analyze_timeline(path, output_prefix):
    rows = read_csv(path)
    link_rows = [row for row in rows if row["scope"] == "link"]
    write_csv(
        output_prefix.with_name(output_prefix.name + "_link_timeseries.csv"),
        rows[0].keys() if rows else [],
        link_rows,
    )

    totals = defaultdict(lambda: defaultdict(int))
    peaks = defaultdict(float)
    for row in rows:
        key = (row["scope"], row["node"], row["x"], row["y"], row["direction"])
        for field in ("offered", "accepted", "stall_cycles", "packets",
                      "state_flits", "partial_flits", "epoch_done_flits",
                      "other_flits"):
            totals[key][field] += int(row[field])
        peaks[key] = max(peaks[key], float(row["utilization"]))

    hotspot_rows = []
    for key, values in totals.items():
        offered = values["offered"]
        hotspot_rows.append({
            "scope": key[0], "node": key[1], "x": key[2], "y": key[3],
            "direction": key[4], **values,
            "backpressure": values["stall_cycles"] / offered if offered else 0.0,
            "peak_interval_utilization": peaks[key],
        })
    hotspot_rows.sort(key=lambda row: (row["stall_cycles"], row["accepted"]),
                      reverse=True)
    fields = ["scope", "node", "x", "y", "direction", "offered",
              "accepted", "stall_cycles", "packets", "state_flits",
              "partial_flits", "epoch_done_flits", "other_flits",
              "backpressure", "peak_interval_utilization"]
    write_csv(output_prefix.with_name(output_prefix.name + "_hotspots.csv"),
              fields, hotspot_rows)
    return hotspot_rows


def packet_key(row):
    return (row["packet_type"], row["source_id"], row["epoch"],
            row["block_id"], row["dest_x"], row["dest_y"], row["last"])


def analyze_events(path, output_prefix):
    rows = read_csv(path)
    active_stalls = {}
    stall_rows = []
    injections = defaultdict(deque)
    latency_rows = []
    for row in rows:
        event = row["event"].replace(" ", "")
        cycle = int(row["cycle"])
        resource = (row["scope"], row["node"], row["direction"])
        if event == "stall_begin":
            active_stalls[resource] = cycle
        elif event == "stall_end" and resource in active_stalls:
            start = active_stalls.pop(resource)
            stall_rows.append({"scope": resource[0], "node": resource[1],
                               "direction": resource[2], "start_cycle": start,
                               "end_cycle": cycle, "duration": cycle - start})
        elif event == "accept" and row["scope"] == "inject":
            injections[packet_key(row)].append(cycle)
        elif event == "accept" and row["scope"] == "eject":
            key = packet_key(row)
            if injections[key]:
                start = injections[key].popleft()
                latency_rows.append({
                    "packet_type": row["packet_type"],
                    "source_id": row["source_id"], "epoch": row["epoch"],
                    "block_id": row["block_id"], "dest_x": row["dest_x"],
                    "dest_y": row["dest_y"], "last": row["last"],
                    "inject_cycle": start, "eject_cycle": cycle,
                    "latency_cycles": cycle - start,
                })

    stall_rows.sort(key=lambda row: row["duration"], reverse=True)
    write_csv(output_prefix.with_name(output_prefix.name + "_stall_episodes.csv"),
              ["scope", "node", "direction", "start_cycle", "end_cycle",
               "duration"], stall_rows)
    latency_fields = ["packet_type", "source_id", "epoch", "block_id",
                      "dest_x", "dest_y", "last", "inject_cycle",
                      "eject_cycle", "latency_cycles"]
    write_csv(output_prefix.with_name(output_prefix.name + "_flit_latencies.csv"),
              latency_fields, latency_rows)
    return stall_rows, latency_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--output-prefix", type=Path, default=Path("noc_analysis"))
    args = parser.parse_args()

    hotspots = analyze_timeline(args.timeline, args.output_prefix)
    links = [row for row in hotspots if row["scope"] == "link"]
    print(f"Analyzed {len(links)} directed physical links")
    if links:
        hottest = max(links, key=lambda row: row["accepted"])
        pressured = max(links, key=lambda row: row["stall_cycles"])
        print("Busiest link: node {node} {direction}, {accepted} flits, "
              "peak interval utilization {peak_interval_utilization:.1%}".format(**hottest))
        print("Most stalled link: node {node} {direction}, {stall_cycles} cycles, "
              "backpressure {backpressure:.1%}".format(**pressured))
    if args.events:
        stalls, latencies = analyze_events(args.events, args.output_prefix)
        print(f"Recorded {len(stalls)} completed stall episodes and "
              f"{len(latencies)} matched flit latencies")
        if latencies:
            values = sorted(row["latency_cycles"] for row in latencies)
            print(f"Flit latency: mean={sum(values)/len(values):.2f}, "
                  f"p95={values[int(0.95*(len(values)-1))]}, max={values[-1]} cycles")


if __name__ == "__main__":
    main()
