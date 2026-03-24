#!/usr/bin/env python3
import argparse
import json
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ENTRY_SPAN_KINDS = {"SERVER", "CONSUMER"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Rank suspicious services from normalized coarse trace JSONL."
    )
    parser.add_argument("--input", required=True, help="Path to normalized coarse JSONL")
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
        "--exclude-operation-regex",
        default=r"^(Mysql/|HikariCP/|/actuator|/swagger|/v2/api-docs|/webjars)",
        help="Regex for noisy operations to exclude from scoring",
    )
    parser.add_argument(
        "--exclude-endpoint-regex",
        dest="exclude_operation_regex",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_span_kind(value):
    text = str(value or "UNSPECIFIED").upper()
    for prefix in ("SPAN_KIND_", "SPAN_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def load_rows(path):
    rows = []
    invalid_json_rows = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                invalid_json_rows += 1
    return rows, invalid_json_rows


def extract_operation(row):
    return (
        row.get("http_route")
        or row.get("operation")
        or row.get("http_target")
        or row.get("rpc_service")
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


def score_service(summary, baseline):
    count_weight = math.log10(summary["count"] + 1)
    mean_ratio = summary["mean_latency"] / baseline["mean_latency"]
    p95_ratio = summary["p95_latency"] / baseline["p95_latency"]
    p99_ratio = summary["p99_latency"] / baseline["p99_latency"]

    combined_signal = (
        (mean_ratio * 0.40)
        + (p95_ratio * 0.30)
        + (p99_ratio * 0.20)
        + (summary["cov"] * 0.10)
    )
    return round(combined_signal * count_weight, 6)


def score_operation(summary, baseline):
    count_weight = math.log10(summary["count"] + 1)
    mean_ratio = summary["mean_latency"] / baseline["mean_latency"]
    p95_ratio = summary["p95_latency"] / baseline["p95_latency"]
    return round(
        ((mean_ratio * 0.55) + (p95_ratio * 0.35) + (summary["cov"] * 0.10))
        * count_weight,
        6,
    )


def select_service_rows(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["service"]].append(row)

    selected = {}
    filtered_non_entry = 0
    for service, service_rows in grouped.items():
        entry_rows = [
            row for row in service_rows if str(row.get("span_kind", "")).upper() in ENTRY_SPAN_KINDS
        ]
        chosen_rows = entry_rows or service_rows
        filtered_non_entry += max(0, len(service_rows) - len(chosen_rows))
        selected[service] = chosen_rows
    return selected, filtered_non_entry


def main():
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    exclude_re = re.compile(args.exclude_operation_regex)

    rows, invalid_json_rows = load_rows(input_path)
    normalized_rows = []
    skipped_rows = 0
    skipped_missing_service = 0
    endpoint_min_count = max(5, min(args.min_count, 25))

    for row in rows:
        latency = safe_float(row.get("latency_ms"))
        if latency is None:
            skipped_rows += 1
            continue

        service = row.get("service")
        if not service:
            skipped_missing_service += 1
            continue

        normalized_rows.append(
            {
                "service": str(service),
                "operation": extract_operation(row),
                "latency_ms": latency,
                "span_kind": normalize_span_kind(row.get("span_kind")),
            }
        )

    service_rows, filtered_non_entry = select_service_rows(normalized_rows)

    service_latencies = defaultdict(list)
    operation_latencies = defaultdict(list)
    filtered_operations = 0

    for service, rows_for_service in service_rows.items():
        for row in rows_for_service:
            operation = row["operation"]
            if exclude_re.search(operation):
                filtered_operations += 1
                continue
            service_latencies[service].append(row["latency_ms"])
            operation_latencies[(service, operation)].append(row["latency_ms"])

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

        top_operation = None
        top_operation_summary = None
        top_operation_score = None
        fallback_operation = None
        fallback_operation_summary = None
        fallback_operation_score = None

        for (operation_service, operation_name), operation_values in operation_latencies.items():
            if operation_service != service:
                continue
            candidate = summarize(operation_values)
            candidate_score = score_operation(candidate, baseline)
            if (
                fallback_operation_score is None
                or candidate_score > fallback_operation_score
            ):
                fallback_operation = operation_name
                fallback_operation_summary = candidate
                fallback_operation_score = candidate_score
            if candidate["count"] < endpoint_min_count:
                continue
            if top_operation_score is None or candidate_score > top_operation_score:
                top_operation = operation_name
                top_operation_summary = candidate
                top_operation_score = candidate_score

        if top_operation is None:
            top_operation = fallback_operation
            top_operation_summary = fallback_operation_summary
            top_operation_score = fallback_operation_score

        entry = {
            "service": service,
            "score": score_service(summary, baseline),
            **summary,
            "top_endpoint": top_operation,
            "top_endpoint_score": top_operation_score or 0.0,
            "top_endpoint_summary": top_operation_summary or summarize([]),
            "top_operation": top_operation,
            "top_operation_score": top_operation_score or 0.0,
            "top_operation_summary": top_operation_summary or summarize([]),
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
            "exclude_operation_regex": args.exclude_operation_regex,
        },
        "global_baseline": baseline_summary,
        "row_stats": {
            "rows_loaded": len(rows),
            "rows_invalid_json": invalid_json_rows,
            "rows_skipped_invalid_latency": skipped_rows,
            "rows_skipped_missing_service": skipped_missing_service,
            "rows_filtered_non_entry_spans": filtered_non_entry,
            "rows_filtered_noisy_operations": filtered_operations,
            "services_considered": len(service_rows),
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
