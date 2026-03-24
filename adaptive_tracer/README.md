# Adaptive Tracer

This directory contains an OpenTelemetry-first adaptive tracing workflow for
Kubernetes microservices.

The design keeps fine tracing external while making coarse tracing
backend-neutral inside `adaptive_tracer`:

- coarse traces are fetched from an OTel-compatible JSON endpoint
- spans are normalized into a tool-owned JSONL schema
- suspicious services are selected automatically from latency variance
- fine tracing stays external by attaching `strace` to selected services
- no application-specific `PYTHIA_FINE_TRACE` logs are required

## Workflow

1. Run a coarse workload so the tracing backend sees fresh requests.
2. Fetch coarse spans from an OTel-compatible endpoint into normalized JSONL.
3. Rank suspicious services with `select_targets.py`.
4. Resolve Kubernetes container PIDs and attach `strace` only to the selected
   services.
5. Run a fine tracing workload while `strace` is attached.

## Files

- `scripts/fetch_otel_traces.py`
  Fetches OTel JSON and writes normalized coarse records.
- `scripts/select_targets.py`
  Ranks services from normalized coarse records and writes a selection manifest.
- `scripts/resolve_pids.py`
  Resolves Kubernetes pods and container IDs into host PIDs.
- `scripts/record_external.sh`
  Attaches `strace` to the selected services without modifying application code.
- `scripts/run_adaptive_cycle.sh`
  Orchestrates coarse load, OTel fetch, selection, and targeted recording in one
  command.

## Normalized coarse record shape

Each normalized JSONL record contains:

- `trace_id`
- `span_id`
- `parent_span_id`
- `service`
- `operation`
- `span_kind`
- `start_time_unix_nano`
- `end_time_unix_nano`
- `latency_ms`
- `status_code`
- `http_method`
- `http_route`
- `http_target`
- `rpc_service`
- `db_system`
- `db_operation`
- `raw_attributes`

The selector prefers request-entry spans (`SERVER` and `CONSUMER`) for each
service and only falls back to other span kinds when a service has no entry
spans in the coarse window.

## Example

```bash
cd adaptive_tracer/scripts

python3 fetch_otel_traces.py \
  --endpoint http://127.0.0.1:4318/v1/traces \
  --output ../results/coarse_records.jsonl \
  --lookback-seconds 90

python3 select_targets.py \
  --input ../results/coarse_records.jsonl \
  --output ../results/selection.json \
  --top-k 3

bash record_external.sh \
  --namespace train-ticket \
  --kind-container train-ticket-control-plane \
  --selection-file ../results/selection.json \
  --out-dir ../results/run_01 \
  --duration 120
```

Or end to end:

```bash
bash run_adaptive_cycle.sh \
  --namespace train-ticket \
  --kind-container train-ticket-control-plane \
  --out-dir ../results/cycle_otel \
  --coarse-source otel \
  --otel-endpoint http://127.0.0.1:4318/v1/traces \
  --coarse-load-cmd 'bash /path/to/run_coarse_load.sh' \
  --fine-load-cmd 'bash /path/to/run_fine_load.sh'
```

If you already have normalized coarse records, skip the fetch stage:

```bash
bash run_adaptive_cycle.sh \
  --namespace train-ticket \
  --kind-container train-ticket-control-plane \
  --out-dir ../results/cycle_from_file \
  --input ../results/coarse_records.jsonl
```

## OTel endpoint contract

`fetch_otel_traces.py` accepts:

- a local JSON file path
- a `file://` JSON file URL
- an HTTP endpoint that returns JSON on `GET` or `POST`

For HTTP endpoints, the script sends or appends:

- `start_time_unix_nano`
- `end_time_unix_nano`
- `lookback_seconds`

The response may be:

- an OTLP JSON payload with `resourceSpans`
- an object with `data.resourceSpans`
- an object with `spans`
- a raw array of span objects

## Assumptions

- `kubectl` can read the target namespace.
- `strace` is installed on the host where Kubernetes containers run.
- For KIND, the control-plane container is reachable with `docker exec`.
- For non-KIND setups, host `crictl inspect` should work.
- Coarse traces are produced elsewhere, typically by OpenTelemetry
  auto-instrumentation or another OTel-compatible source.

## Output

Each run writes:

- `coarse_records.jsonl` with normalized coarse spans
- `selection.json` with ranked suspicious services
- `targets.json` with resolved pods, containers, and PIDs
- `trace_meta.json` with runtime metadata
- `strace.log.<pid>` files from `strace -ff`

## Notes

- Fine tracing in `adaptive_tracer` means external syscall/thread capture, not
  application log instrumentation.
- The quality of selection still depends on the quality and coverage of the
  coarse OTel spans.
