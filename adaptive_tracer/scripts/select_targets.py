#!/usr/bin/env python3
import argparse
import base64
import json
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Rank suspicious services from coarse trace JSONL."
    )
    parser.add_argument("--input", required=True, help="Path to JSONL trace export")
    parser.add_argument(
        "--output",
        required=True,
        help="Where to write the JSON selection manifest",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of services to select for fine tracing",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=20,
        help="Minimum samples required before a service can be selected",
    )
    parser.add_argument(
        "--exclude-endpoint-regex",
        default=r"^(Mysql/|HikariCP/|/actuator|/swagger|/v2/api-docs|/webjars)",
        help="Regex for noisy coarse endpoints to exclude from scoring",
    )
    return parser.parse_args()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def decode_service_name(raw):
    if not raw:
        return "UNKNOWN_SERVICE"
    if "." in raw:
        candidate = raw.split(".", 1)[0]
    else:
        candidate = raw
    try:
        decoded = base64.b64decode(candidate).decode("utf-8")
        if decoded:
            return decoded
    except Exception:
        pass
    return raw


def extract_service(row):
    return decode_service_name(
        row.get("service")
        or row.get("serviceCode")
        or row.get("service_code")
        or row.get("service_id")
        or row.get("serviceId")
    )


def extract_endpoint(row):
    return (
        row.get("endpoint")
        or row.get("endpointName")
        or row.get("endpoint_name")
        or "UNKNOWN_ENDPOINT"
    )


def mean(values):
    return sum(values) / len(values) if values else 0.0


def variance(values):
    if len(values) < 2:
        return 0.0
    return statistics.pvariance(values)


def stddev(values):
    if len(values) < 2:
        return 0.0
    return statistics.pstdev(values)


def cov(values):
    avg = mean(values)
    if avg == 0:
        return 0.0
    return stddev(values) / avg


def summarize(values):
    if not values:
        return {
            "count": 0,
            "p50_latency": 0.0,
            "p95_latency": 0.0,
            "p99_latency": 0.0,
            "mean_latency": 0.0,
            "variance": 0.0,
            "stddev": 0.0,
            "cov": 0.0,
            "max_latency": 0.0,
        }
    sorted_values = sorted(values)
    return {
        "count": len(values),
        "p50_latency": round(percentile(sorted_values, 0.50), 6),
        "p95_latency": round(percentile(sorted_values, 0.95), 6),
        "p99_latency": round(percentile(sorted_values, 0.99), 6),
        "mean_latency": round(mean(values), 6),
        "variance": round(variance(values), 6),
        "stddev": round(stddev(values), 6),
        "cov": round(cov(values), 6),
        "max_latency": round(max(values), 6),
    }


def percentile(sorted_values, q):
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])

    index = (len(sorted_values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(sorted_values[lower])

    lower_value = sorted_values[lower]
    upper_value = sorted_values[upper]
    weight = index - lower
    return lower_value + (upper_value - lower_value) * weight


def score_service(summary, baseline):
    count_weight = math.log10(summary["count"] + 1)
    mean_ratio = summary["mean_latency"] / baseline["mean_latency"]
    p95_ratio = summary["p95_latency"] / baseline["p95_latency"]
    p99_ratio = summary["p99_latency"] / baseline["p99_latency"]

    # Favor sustained shifts (mean/p95/p99) while still rewarding instability.
    combined_signal = (
        (mean_ratio * 0.40)
        + (p95_ratio * 0.30)
        + (p99_ratio * 0.20)
        + (summary["cov"] * 0.10)
    )
    return round(combined_signal * count_weight, 6)


def endpoint_score(summary, baseline):
    count_weight = math.log10(summary["count"] + 1)
    mean_ratio = summary["mean_latency"] / baseline["mean_latency"]
    p95_ratio = summary["p95_latency"] / baseline["p95_latency"]
    return round(((mean_ratio * 0.55) + (p95_ratio * 0.35) + (summary["cov"] * 0.10)) * count_weight, 6)


def load_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main():
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    exclude_re = re.compile(args.exclude_endpoint_regex)

    rows = load_rows(input_path)
    service_latencies = defaultdict(list)
    endpoint_latencies = defaultdict(list)
    skipped_rows = 0
    endpoint_min_count = max(5, min(args.min_count, 25))

    for row in rows:
        latency = safe_float(row.get("latency"))
        if latency is None:
            skipped_rows += 1
            continue

        service = extract_service(row)
        endpoint = extract_endpoint(row)
        if exclude_re.search(endpoint):
            continue

        service_latencies[service].append(latency)
        endpoint_latencies[(service, endpoint)].append(latency)

    baseline_summary = summarize(
        [latency for latencies in service_latencies.values() for latency in latencies]
    )
    baseline = {
        "mean_latency": max(baseline_summary["mean_latency"], 1e-9),
        "p95_latency": max(baseline_summary["p95_latency"], 1e-9),
        "p99_latency": max(baseline_summary["p99_latency"], 1e-9),
    }

    ranked = []
    for service, latencies in service_latencies.items():
        summary = summarize(latencies)
        if summary["count"] < args.min_count:
            continue

        top_endpoint = None
        top_endpoint_summary = None
        top_endpoint_score = None
        fallback_endpoint = None
        fallback_endpoint_summary = None
        fallback_endpoint_score = None
        for (endpoint_service, endpoint_name), endpoint_values in endpoint_latencies.items():
            if endpoint_service != service:
                continue
            candidate = summarize(endpoint_values)
            candidate_score = endpoint_score(candidate, baseline)
            if fallback_endpoint_score is None or candidate_score > fallback_endpoint_score:
                fallback_endpoint = endpoint_name
                fallback_endpoint_summary = candidate
                fallback_endpoint_score = candidate_score
            if candidate["count"] < endpoint_min_count:
                continue
            if top_endpoint_score is None or candidate_score > top_endpoint_score:
                top_endpoint = endpoint_name
                top_endpoint_summary = candidate
                top_endpoint_score = candidate_score

        if top_endpoint is None:
            top_endpoint = fallback_endpoint
            top_endpoint_summary = fallback_endpoint_summary
            top_endpoint_score = fallback_endpoint_score

        entry = {
            "service": service,
            "score": score_service(summary, baseline),
            **summary,
            "top_endpoint": top_endpoint,
            "top_endpoint_score": top_endpoint_score or 0.0,
            "top_endpoint_summary": top_endpoint_summary or summarize([]),
        }
        ranked.append(entry)

    ranked.sort(
        key=lambda item: (item["score"], item["cov"], item["variance"], item["count"]),
        reverse=True,
    )

    selected = ranked[: args.top_k]
    manifest = {
        "generated_at": utc_now(),
        "input_path": str(input_path),
        "selection_strategy": {
            "top_k": args.top_k,
            "min_count": args.min_count,
            "exclude_endpoint_regex": args.exclude_endpoint_regex,
        },
        "global_baseline": baseline_summary,
        "row_stats": {
            "rows_loaded": len(rows),
            "rows_skipped_invalid_latency": skipped_rows,
            "services_considered": len(service_latencies),
            "services_ranked": len(ranked),
        },
        "selected_services": [item["service"] for item in selected],
        "ranked_services": ranked,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    print(f"Loaded rows: {len(rows)}")
    print(f"Ranked services: {len(ranked)}")
    print(f"Selected services: {', '.join(manifest['selected_services']) or 'none'}")
    print(f"Saved manifest: {output_path}")


if __name__ == "__main__":
    main()
