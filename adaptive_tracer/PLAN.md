# Refactor Plan: OpenTelemetry-First `adaptive_tracer`

## Summary
Refactor `adaptive_tracer` so the coarse phase no longer depends on SkyWalking exports or `pythia` scripts. The new design makes OpenTelemetry the only supported coarse-trace source in v1, with `adaptive_tracer` consuming OTel span data directly, normalizing it internally, and continuing to use the existing external fine-tracing path (`resolve_pids.py` + `record_external.sh`) unchanged except for metadata updates.

The goal is to preserve the current adaptive workflow shape:
1. generate load
2. collect coarse traces
3. rank suspicious services
4. externally attach fine tracing to selected services

but replace the current SkyWalking-specific coarse implementation with an OTel-native adapter layer.

## Key Changes
### 1. Introduce an explicit coarse-trace adapter boundary
Create a coarse-ingestion module in `adaptive_tracer` that separates:
- trace acquisition from OTel
- span normalization into a tool-owned internal record format
- service ranking/selection

The adapter contract should produce normalized records with these fields:
- `trace_id`
- `span_id`
- `parent_span_id` if present
- `service`
- `operation`
- `span_kind`
- `start_time_unix_nano`
- `end_time_unix_nano`
- `latency_ms`
- `status_code`
- `http_method` if present
- `http_route` if present
- `http_target` if present
- `rpc_service` if present
- `db_system` if present
- `db_operation` if present
- `raw_attributes` as a passthrough map for future rules

This schema is internal to `adaptive_tracer`; selection should stop depending on backend-specific field names like SkyWalking’s `endpointName` and base64-encoded service identifiers.

### 2. Replace backend-specific selection parsing with normalized selection input
Refactor `select_targets.py` so its main logic operates only on normalized coarse records, not backend exports.

Implementation requirements:
- remove SkyWalking-specific service decoding logic
- replace `extract_service()` and `extract_endpoint()` with normalized-field readers
- define endpoint/operation resolution precedence:
  1. `http_route`
  2. `operation`
  3. `http_target`
  4. `rpc_service`
  5. `"UNKNOWN_ENDPOINT"`
- continue scoring by service-level latency shift plus variability
- continue ranking top endpoint per service, but compute it from normalized operation labels
- keep the current noise filtering idea, but rename it from endpoint filtering to operation filtering

The selection script should still support file-based execution for reproducibility, but the file is now a normalized coarse-record export owned by `adaptive_tracer`, not a SkyWalking dump.

### 3. Add an OpenTelemetry direct-ingestion coarse adapter
Add a new script/module that pulls coarse spans directly from an OTel-compatible source and converts them into normalized records.

For v1, the plan should assume this ingestion path:
- `adaptive_tracer` queries an OTel backend endpoint directly
- the backend returns spans for a bounded time window or recent trace batch
- the adapter transforms returned spans into the normalized internal schema
- the normalized records are written to an intermediate JSONL artifact in the run output directory

Because you chose `OTLP direct`, the implementation should be decision-complete as follows:
- the ingestion code accepts an OTLP-compatible endpoint as a required coarse-source setting
- it supports a trace lookback window rather than backend-specific index names
- it fetches spans for the coarse run window only
- it filters to server and consumer/request-entry spans by default for service ranking
- it ignores internal spans for service ranking unless no entry spans are available for a service
- it computes `latency_ms` from span start/end timestamps, never from backend-specific convenience fields

New CLI inputs for the orchestration layer:
- `--coarse-source otel`
- `--otel-endpoint <url>`
- `--otel-lookback-seconds <n>`
- `--coarse-output <path>` optional, default under `out-dir`
- `--service-name-attribute <key>` optional, default `service.name`

Defaults:
- `--coarse-source` defaults to `otel`
- lookback defaults to slightly longer than the coarse workload duration so late-arriving spans are still captured
- only one coarse source is supported in v1

### 4. Refactor `run_adaptive_cycle.sh` around source-agnostic orchestration
Update orchestration so it no longer shells into `pythia/scripts/export_traces.sh` or assumes `sw_segment-*`.

Implementation behavior:
- keep `--coarse-load-cmd` and `--fine-load-cmd`
- remove `--trace-index`
- remove the default `pythia` export command path
- after coarse load finishes, invoke the new OTel ingestion script to write normalized coarse JSONL into `OUT_DIR`
- pass that normalized file into `select_targets.py`
- keep the fine-tracing stage unchanged after service selection

New output artifacts in each run:
- `coarse_records.jsonl`
- `selection.json`
- `targets.json`
- `trace_meta.json`
- `strace.log.*`

`trace_meta.json` should be expanded to record:
- coarse source type
- OTel endpoint used
- coarse lookback window
- coarse record count
- selection input file path
- selected services

### 5. Keep fine tracing external and unchanged in principle
Do not change the current PID resolution and `strace` attachment model except where metadata and interface naming need cleanup.

Required changes:
- update docs/comments so “fine tracing” clearly means external syscall/thread capture
- remove references implying that app-level `PYTHIA_FINE_TRACE` logs are part of the adaptive tracer design
- keep `resolve_pids.py` and `record_external.sh` as backend-agnostic components
- ensure the plan treats app instrumentation as out of scope for `adaptive_tracer`

## Public Interfaces / CLI Changes
The refactor changes the orchestration interface as follows.

Add:
- `--coarse-source`
- `--otel-endpoint`
- `--otel-lookback-seconds`
- `--coarse-output`
- `--service-name-attribute`

Remove:
- `--trace-index`

Behavior changes:
- `--input` now means a normalized coarse-record JSONL file, not a SkyWalking export
- if `--input` is omitted, the tool performs coarse load and then fetches coarse spans from the configured OTel source
- the default end-to-end path is OTel-only

## Test Plan
### Unit / parser tests
- normalize OTel spans with HTTP attributes into correct `service`, `operation`, and `latency_ms`
- normalize non-HTTP spans without crashing and with sensible fallback operation labels
- ignore malformed spans or spans missing timestamps while reporting skipped counts
- ensure selection no longer depends on SkyWalking service decoding

### Selection behavior tests
- rank a service higher when its mean/p95/p99 latency shifts above baseline
- keep noise filters from selecting DB/internal-only operations when request-entry spans exist
- choose the highest-scoring operation as `top_endpoint`/top operation per service
- return zero selections cleanly when coarse data is insufficient

### Orchestration tests
- end-to-end dry run with provided normalized input file skips OTel fetch and proceeds to selection
- end-to-end run with omitted `--input` performs coarse load, fetches OTel data, writes `coarse_records.jsonl`, and selects targets
- run metadata records the new coarse-source fields
- fine-tracing stage still resolves PIDs and prepares `strace` attachment using selected services

### Acceptance scenarios
- a microservice deployment with OTel auto-instrumentation and no SkyWalking can complete the full adaptive cycle
- selected suspicious services flow unchanged into external fine tracing
- no `pythia/scripts/export_traces.sh` or `sw_segment-*` assumptions remain in the adaptive path
- README examples reflect the new OTel-first workflow only

## Assumptions and Defaults
- SkyWalking compatibility is intentionally dropped in this refactor.
- OpenTelemetry auto-instrumentation is the default assumed source of coarse traces for services that do not already expose tracing.
- `adaptive_tracer` owns a normalized intermediate JSONL format even though ingestion is OTel-direct; this keeps selection reproducible and testable.
- Service ranking is based on request-entry spans by default, not arbitrary internal spans.
- The existing external fine-tracing subsystem remains the v1 fine-grained mechanism; no app-specific `PYTHIA_FINE_TRACE` behavior is part of the new design.
