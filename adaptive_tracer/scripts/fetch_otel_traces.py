#!/usr/bin/env python3
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import error, parse, request


SPAN_KIND_MAP = {
    0: "UNSPECIFIED",
    1: "INTERNAL",
    2: "SERVER",
    3: "CLIENT",
    4: "PRODUCER",
    5: "CONSUMER",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch coarse traces from an OTel-compatible JSON endpoint and normalize them."
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="HTTP endpoint returning OTel JSON payloads, file:// URL, or local JSON file path",
    )
    parser.add_argument("--output", required=True, help="Where to write normalized JSONL records")
    parser.add_argument(
        "--lookback-seconds",
        type=int,
        default=90,
        help="Trace lookback window to request when explicit timestamps are not supplied",
    )
    parser.add_argument(
        "--service-name-attribute",
        default="service.name",
        help="Resource or span attribute to use as the canonical service name",
    )
    parser.add_argument(
        "--start-time-unix-nano",
        type=int,
        help="Inclusive start time for the coarse trace window in unix nanoseconds",
    )
    parser.add_argument(
        "--end-time-unix-nano",
        type=int,
        help="Exclusive end time for the coarse trace window in unix nanoseconds",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="HTTP timeout when querying a remote OTel endpoint",
    )
    return parser.parse_args()


def now_unix_nano() -> int:
    return time.time_ns()


def parse_any_value(raw: Dict[str, Any]) -> Any:
    if not isinstance(raw, dict):
        return raw
    for key in (
        "stringValue",
        "boolValue",
        "intValue",
        "doubleValue",
        "bytesValue",
    ):
        if key in raw:
            return raw[key]
    if "arrayValue" in raw:
        values = raw.get("arrayValue", {}).get("values", [])
        return [parse_any_value(value) for value in values]
    if "kvlistValue" in raw:
        values = raw.get("kvlistValue", {}).get("values", [])
        return {
            item.get("key"): parse_any_value(item.get("value", {}))
            for item in values
            if item.get("key")
        }
    return raw


def attributes_to_dict(items: Any) -> Dict[str, Any]:
    if isinstance(items, dict):
        return {str(key): value for key, value in items.items()}
    if not isinstance(items, list):
        return {}

    result = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not key:
            continue
        result[str(key)] = parse_any_value(item.get("value", {}))
    return result


def normalize_span_kind(raw_kind: Any) -> str:
    if isinstance(raw_kind, str):
        kind = raw_kind.strip().upper()
        for prefix in ("SPAN_KIND_", "SPAN_"):
            if kind.startswith(prefix):
                kind = kind[len(prefix):]
        if kind:
            return kind
    if isinstance(raw_kind, int):
        return SPAN_KIND_MAP.get(raw_kind, "UNSPECIFIED")
    return "UNSPECIFIED"


def safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_local_payload(endpoint: str) -> Optional[Any]:
    if endpoint.startswith("file://"):
        path = Path(parse.urlparse(endpoint).path)
    else:
        path = Path(endpoint)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def add_query_params(endpoint: str, params: Dict[str, Any]) -> str:
    parsed = parse.urlparse(endpoint)
    existing = parse.parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        existing[key] = [str(value)]
    return parse.urlunparse(
        parsed._replace(query=parse.urlencode(existing, doseq=True))
    )


def http_json_get(url: str, timeout_seconds: int) -> Any:
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset("utf-8")
        return json.loads(response.read().decode(charset))


def http_json_post(url: str, payload: Dict[str, Any], timeout_seconds: int) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset("utf-8")
        return json.loads(response.read().decode(charset))


def fetch_payload(
    endpoint: str,
    start_time_unix_nano: int,
    end_time_unix_nano: int,
    lookback_seconds: int,
    timeout_seconds: int,
) -> Any:
    local_payload = load_local_payload(endpoint)
    if local_payload is not None:
        return local_payload

    query_params = {
        "start_time_unix_nano": start_time_unix_nano,
        "end_time_unix_nano": end_time_unix_nano,
        "lookback_seconds": lookback_seconds,
    }
    query_url = add_query_params(endpoint, query_params)
    try:
        return http_json_get(query_url, timeout_seconds)
    except error.HTTPError as exc:
        if exc.code not in {400, 404, 405, 415, 501}:
            raise
    except error.URLError:
        pass

    return http_json_post(endpoint, query_params, timeout_seconds)


def iterate_otlp_spans(payload: Any) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    if isinstance(payload, dict):
        if "resourceSpans" in payload:
            resource_spans = payload.get("resourceSpans") or []
        elif "data" in payload and isinstance(payload["data"], dict) and "resourceSpans" in payload["data"]:
            resource_spans = payload["data"].get("resourceSpans") or []
        else:
            resource_spans = None

        if resource_spans is not None:
            for resource_span in resource_spans:
                resource = resource_span.get("resource", {}) or {}
                resource_attrs = attributes_to_dict(resource.get("attributes", []))
                scope_spans = resource_span.get("scopeSpans")
                if scope_spans is None:
                    scope_spans = resource_span.get("instrumentationLibrarySpans", [])
                for scope_span in scope_spans or []:
                    for span in scope_span.get("spans", []) or []:
                        yield span, resource_attrs
            return

        spans = payload.get("spans")
        if isinstance(spans, list):
            shared_resource_attrs = attributes_to_dict(payload.get("resourceAttributes", []))
            for span in spans:
                yield span, shared_resource_attrs
            return

    if isinstance(payload, list):
        for span in payload:
            yield span, {}


def extract_status_code(span: Dict[str, Any], merged_attrs: Dict[str, Any]) -> Any:
    status = span.get("status")
    if isinstance(status, dict):
        if "code" in status:
            return status.get("code")
        if "statusCode" in status:
            return status.get("statusCode")
    for key in ("http.status_code", "status.code", "otel.status_code"):
        if key in merged_attrs:
            return merged_attrs[key]
    return None


def normalize_record(
    span: Dict[str, Any],
    resource_attrs: Dict[str, Any],
    service_name_attribute: str,
) -> Optional[Dict[str, Any]]:
    span_attrs = attributes_to_dict(span.get("attributes", []))
    merged_attrs = dict(resource_attrs)
    merged_attrs.update(span_attrs)

    start_time = safe_int(span.get("startTimeUnixNano") or span.get("start_time_unix_nano"))
    end_time = safe_int(span.get("endTimeUnixNano") or span.get("end_time_unix_nano"))
    if start_time is None or end_time is None or end_time < start_time:
        return None

    service = (
        merged_attrs.get(service_name_attribute)
        or resource_attrs.get(service_name_attribute)
        or merged_attrs.get("service.name")
        or span.get("serviceName")
        or span.get("service")
        or "UNKNOWN_SERVICE"
    )

    status_code = extract_status_code(span, merged_attrs)
    http_method = merged_attrs.get("http.method")
    http_route = merged_attrs.get("http.route") or merged_attrs.get("url.path")
    http_target = merged_attrs.get("http.target") or merged_attrs.get("http.url")

    return {
        "trace_id": span.get("traceId") or span.get("trace_id"),
        "span_id": span.get("spanId") or span.get("span_id"),
        "parent_span_id": span.get("parentSpanId") or span.get("parent_span_id"),
        "service": str(service),
        "operation": span.get("name") or span.get("operation") or "UNKNOWN_OPERATION",
        "span_kind": normalize_span_kind(span.get("kind") or span.get("spanKind")),
        "start_time_unix_nano": start_time,
        "end_time_unix_nano": end_time,
        "latency_ms": round((end_time - start_time) / 1_000_000.0, 6),
        "status_code": status_code,
        "http_method": http_method,
        "http_route": http_route,
        "http_target": http_target,
        "rpc_service": merged_attrs.get("rpc.service"),
        "db_system": merged_attrs.get("db.system"),
        "db_operation": merged_attrs.get("db.operation"),
        "raw_attributes": merged_attrs,
    }


def normalize_otel_payload(
    payload: Any,
    service_name_attribute: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    normalized = []
    stats = {
        "spans_seen": 0,
        "records_normalized": 0,
        "spans_skipped_missing_timestamps": 0,
    }
    for span, resource_attrs in iterate_otlp_spans(payload):
        stats["spans_seen"] += 1
        record = normalize_record(span, resource_attrs, service_name_attribute)
        if record is None:
            stats["spans_skipped_missing_timestamps"] += 1
            continue
        normalized.append(record)
        stats["records_normalized"] += 1
    return normalized, stats


def select_ranking_records(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    by_service = defaultdict(list)
    for record in records:
        by_service[record["service"]].append(record)

    selected = []
    dropped = 0
    for service_records in by_service.values():
        entry_records = [
            record
            for record in service_records
            if record.get("span_kind") in {"SERVER", "CONSUMER"}
        ]
        chosen = entry_records or service_records
        dropped += max(0, len(service_records) - len(chosen))
        selected.extend(chosen)
    return selected, dropped


def write_jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")


def main():
    args = parse_args()
    end_time_unix_nano = args.end_time_unix_nano or now_unix_nano()
    start_time_unix_nano = args.start_time_unix_nano or (
        end_time_unix_nano - (args.lookback_seconds * 1_000_000_000)
    )

    payload = fetch_payload(
        endpoint=args.endpoint,
        start_time_unix_nano=start_time_unix_nano,
        end_time_unix_nano=end_time_unix_nano,
        lookback_seconds=args.lookback_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    normalized_records, stats = normalize_otel_payload(
        payload=payload,
        service_name_attribute=args.service_name_attribute,
    )
    ranking_records, dropped_non_entry = select_ranking_records(normalized_records)
    stats["records_written"] = len(ranking_records)
    stats["records_filtered_non_entry"] = dropped_non_entry

    output_path = Path(args.output)
    write_jsonl(output_path, ranking_records)

    print(f"Fetched spans: {stats['spans_seen']}")
    print(f"Normalized records: {stats['records_normalized']}")
    print(f"Skipped missing timestamps: {stats['spans_skipped_missing_timestamps']}")
    print(f"Filtered non-entry spans: {stats['records_filtered_non_entry']}")
    print(f"Wrote records: {stats['records_written']}")
    print(f"Saved output: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Failed to fetch or normalize OTel traces: {exc}", file=sys.stderr)
        raise
