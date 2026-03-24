import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_otel_traces  # noqa: E402


class FetchOtelTracesTests(unittest.TestCase):
    def test_normalizes_otlp_payload_and_filters_to_entry_spans(self):
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "ts-order-service"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "trace-1",
                                    "spanId": "span-server",
                                    "parentSpanId": "",
                                    "name": "POST /order",
                                    "kind": 2,
                                    "startTimeUnixNano": "1000000000",
                                    "endTimeUnixNano": "1015000000",
                                    "attributes": [
                                        {"key": "http.method", "value": {"stringValue": "POST"}},
                                        {"key": "http.route", "value": {"stringValue": "/api/v1/orderservice/order"}},
                                    ],
                                    "status": {"code": "STATUS_CODE_OK"},
                                },
                                {
                                    "traceId": "trace-1",
                                    "spanId": "span-internal",
                                    "parentSpanId": "span-server",
                                    "name": "save order",
                                    "kind": 1,
                                    "startTimeUnixNano": "1015000000",
                                    "endTimeUnixNano": "1020000000",
                                    "attributes": [],
                                },
                            ]
                        }
                    ],
                },
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "ts-route-service"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "trace-2",
                                    "spanId": "span-only-internal",
                                    "name": "recompute route cache",
                                    "kind": 1,
                                    "startTimeUnixNano": "2000000000",
                                    "endTimeUnixNano": "2009000000",
                                    "attributes": [],
                                }
                            ]
                        }
                    ],
                },
            ]
        }

        normalized, stats = fetch_otel_traces.normalize_otel_payload(
            payload, "service.name"
        )
        ranking_records, dropped = fetch_otel_traces.select_ranking_records(normalized)

        self.assertEqual(stats["records_normalized"], 3)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(ranking_records), 2)

        by_service = {record["service"]: record for record in ranking_records}
        self.assertEqual(by_service["ts-order-service"]["span_kind"], "SERVER")
        self.assertEqual(
            by_service["ts-order-service"]["http_route"],
            "/api/v1/orderservice/order",
        )
        self.assertAlmostEqual(by_service["ts-order-service"]["latency_ms"], 15.0)
        self.assertEqual(by_service["ts-route-service"]["span_kind"], "INTERNAL")

    def test_skips_spans_missing_timestamps(self):
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "ts-basic-service"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "trace-3",
                                    "spanId": "missing-times",
                                    "name": "broken span",
                                    "kind": 2,
                                    "attributes": [],
                                }
                            ]
                        }
                    ],
                }
            ]
        }

        normalized, stats = fetch_otel_traces.normalize_otel_payload(
            payload, "service.name"
        )
        self.assertEqual(normalized, [])
        self.assertEqual(stats["spans_seen"], 1)
        self.assertEqual(stats["spans_skipped_missing_timestamps"], 1)


class SelectTargetsTests(unittest.TestCase):
    def test_select_targets_prefers_http_route_and_entry_spans(self):
        selector = SCRIPTS_DIR / "select_targets.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "coarse_records.jsonl"
            output_path = Path(temp_dir) / "selection.json"

            records = [
                {
                    "trace_id": "t1",
                    "span_id": "s1",
                    "service": "ts-order-service",
                    "operation": "POST /order",
                    "span_kind": "SERVER",
                    "start_time_unix_nano": 1,
                    "end_time_unix_nano": 101_000_000,
                    "latency_ms": 100,
                    "http_route": "/api/v1/orderservice/order",
                },
                {
                    "trace_id": "t2",
                    "span_id": "s2",
                    "service": "ts-order-service",
                    "operation": "POST /order",
                    "span_kind": "SERVER",
                    "start_time_unix_nano": 1,
                    "end_time_unix_nano": 121_000_000,
                    "latency_ms": 120,
                    "http_route": "/api/v1/orderservice/order",
                },
                {
                    "trace_id": "t3",
                    "span_id": "s3",
                    "service": "ts-order-service",
                    "operation": "internal save",
                    "span_kind": "INTERNAL",
                    "start_time_unix_nano": 1,
                    "end_time_unix_nano": 401_000_000,
                    "latency_ms": 400,
                },
                {
                    "trace_id": "t4",
                    "span_id": "s4",
                    "service": "ts-basic-service",
                    "operation": "GET /routes",
                    "span_kind": "SERVER",
                    "start_time_unix_nano": 1,
                    "end_time_unix_nano": 21_000_000,
                    "latency_ms": 20,
                    "http_route": "/api/v1/travelservice/trips/left",
                },
                {
                    "trace_id": "t5",
                    "span_id": "s5",
                    "service": "ts-basic-service",
                    "operation": "GET /routes",
                    "span_kind": "SERVER",
                    "start_time_unix_nano": 1,
                    "end_time_unix_nano": 23_000_000,
                    "latency_ms": 22,
                    "http_route": "/api/v1/travelservice/trips/left",
                },
            ]

            with open(input_path, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record))
                    handle.write("\n")

            subprocess.run(
                [
                    "python3",
                    str(selector),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--top-k",
                    "1",
                    "--min-count",
                    "2",
                ],
                check=True,
            )

            with open(output_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)

            self.assertEqual(manifest["selected_services"], ["ts-order-service"])
            ranked = manifest["ranked_services"][0]
            self.assertEqual(ranked["service"], "ts-order-service")
            self.assertEqual(ranked["top_operation"], "/api/v1/orderservice/order")
            self.assertEqual(ranked["top_endpoint"], "/api/v1/orderservice/order")
            self.assertEqual(
                manifest["row_stats"]["rows_filtered_non_entry_spans"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
