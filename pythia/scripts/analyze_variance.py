#!/usr/bin/env python3
import json
import math
import sys
from collections import defaultdict
from pathlib import Path


def mean(values):
    return sum(values) / len(values) if values else 0.0


def variance(values):
    if not values:
        return 0.0
    m = mean(values)
    return sum((x - m) ** 2 for x in values) / len(values)


def stddev(values):
    return math.sqrt(variance(values))


def cov(values):
    m = mean(values)
    if m == 0:
        return 0.0
    return stddev(values) / m


def load_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def get_latency(row):
    try:
        return float(row.get("latency", 0))
    except Exception:
        return 0.0


def get_group_key(row):
    service = (
        row.get("serviceCode")
        or row.get("service_code")
        or row.get("service_id")
        or "UNKNOWN_SERVICE"
    )
    endpoint = row.get("endpointName") or row.get("endpoint_name") or "UNKNOWN_ENDPOINT"
    span_type = row.get("type") or "UNKNOWN_TYPE"
    layer = row.get("layer") or "UNKNOWN_LAYER"
    return (service, endpoint, span_type, layer)


def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_variance.py <input.jsonl> [output.csv]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        sys.exit(1)

    output_path = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path("../results/variance_report.csv")
    )

    rows = load_jsonl(input_path)
    groups = defaultdict(list)

    for row in rows:
        key = get_group_key(row)
        groups[key].append(get_latency(row))

    results = []
    for (service, endpoint, span_type, layer), latencies in groups.items():
        count = len(latencies)
        avg = mean(latencies)
        var = variance(latencies)
        sd = stddev(latencies)
        cv = cov(latencies)
        results.append(
            {
                "service": service,
                "endpoint": endpoint,
                "type": span_type,
                "layer": layer,
                "count": count,
                "mean_latency": round(avg, 6),
                "variance": round(var, 6),
                "stddev": round(sd, 6),
                "cov": round(cv, 6),
            }
        )

    results.sort(key=lambda x: (x["cov"], x["variance"], x["count"]), reverse=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("service,endpoint,type,layer,count,mean_latency,variance,stddev,cov\n")
        for r in results:
            f.write(
                f'"{r["service"]}","{r["endpoint"]}","{r["type"]}","{r["layer"]}",'
                f'{r["count"]},{r["mean_latency"]},{r["variance"]},{r["stddev"]},{r["cov"]}\n'
            )

    print(f"Loaded rows: {len(rows)}")
    print(f"Unique groups: {len(results)}")
    print(f"Saved report: {output_path}")
    print("\nTop 10 groups by CoV:\n")
    for r in results[:10]:
        print(
            f'{r["service"]} | {r["endpoint"]} | {r["type"]} | {r["layer"]} | '
            f'count={r["count"]} mean={r["mean_latency"]} var={r["variance"]} cov={r["cov"]}'
        )


if __name__ == "__main__":
    main()
