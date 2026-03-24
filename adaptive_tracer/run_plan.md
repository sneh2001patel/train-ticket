There are two good ways to test it right now.

**Fast local test**
This verifies the new coarse-selection path without needing a live OTel backend.

```bash
cd /home/sneh_2001patel/train-ticket/adaptive_tracer/scripts

tmpdir=$(mktemp -d)

cat > "$tmpdir/coarse_records.jsonl" <<'EOF'
{"trace_id":"t1","span_id":"s1","service":"svc-a","operation":"GET /a","span_kind":"SERVER","start_time_unix_nano":1,"end_time_unix_nano":10000001,"latency_ms":10}
{"trace_id":"t2","span_id":"s2","service":"svc-a","operation":"GET /a","span_kind":"SERVER","start_time_unix_nano":1,"end_time_unix_nano":12000001,"latency_ms":12}
{"trace_id":"t3","span_id":"s3","service":"svc-b","operation":"GET /b","span_kind":"SERVER","start_time_unix_nano":1,"end_time_unix_nano":4000001,"latency_ms":4}
{"trace_id":"t4","span_id":"s4","service":"svc-b","operation":"GET /b","span_kind":"SERVER","start_time_unix_nano":1,"end_time_unix_nano":5000001,"latency_ms":5}
EOF

python3 select_targets.py \
  --input "$tmpdir/coarse_records.jsonl" \
  --output "$tmpdir/selection.json" \
  --top-k 1 \
  --min-count 2

cat "$tmpdir/selection.json"
```

If that works, the refactored selector is fine.

**Test the OTel fetcher**
If you already have a JSON file that looks like OTel `resourceSpans`, point the fetcher at it:

```bash
python3 fetch_otel_traces.py \
  --endpoint /path/to/otel_payload.json \
  --output /tmp/coarse_records.jsonl \
  --lookback-seconds 90
```

Then inspect:

```bash
head -n 5 /tmp/coarse_records.jsonl
```

**Full end-to-end run**
This is the real workflow, but it needs:
- a live Kubernetes namespace
- `kubectl` access
- `strace`
- an OTel-compatible HTTP endpoint that returns trace JSON
- a load command

Example:

```bash
cd /home/sneh_2001patel/train-ticket/adaptive_tracer/scripts

bash run_adaptive_cycle.sh \
  --namespace train-ticket \
  --kind-container train-ticket-control-plane \
  --out-dir ../results/cycle_otel_test \
  --coarse-source otel \
  --otel-endpoint http://YOUR-TRACE-ENDPOINT \
  --coarse-load-cmd 'bash /path/to/run_coarse_load.sh' \
  --fine-load-cmd 'bash /path/to/run_fine_load.sh' \
  --duration 60 \
  --top-k 3
```

Then check:

```bash
cat ../results/cycle_otel_test/selection.json
cat ../results/cycle_otel_test/trace_meta.json
ls ../results/cycle_otel_test/strace.log.*
```

**Important caveat**
`--otel-endpoint` is not a raw OTLP ingest receiver. Right now it must be an endpoint that returns JSON trace data when queried, or a local JSON file path. So `http://127.0.0.1:4318/v1/traces` will only work if something there actually responds with trace JSON for reads.

**Recommended order**
1. Run the local `select_targets.py` test.
2. Run `fetch_otel_traces.py` against a local/sample OTel JSON file.
3. Run the full `run_adaptive_cycle.sh` flow once you have a real trace-query endpoint.

If you want, I can help you wire this to the actual tracing backend you have running and give you the exact command for your environment.