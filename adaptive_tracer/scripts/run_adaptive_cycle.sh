#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT=""
OUT_DIR=""
NAMESPACE=""
KIND_CONTAINER=""
TOP_K="3"
MIN_COUNT="20"
DURATION="60"
LOAD_CMD=""
EXCLUDE_REGEX='^(Mysql/|HikariCP/|/actuator|/swagger|/v2/api-docs|/webjars)'
COARSE_LOAD_CMD=""
FINE_LOAD_CMD=""
COARSE_SOURCE="otel"
OTEL_ENDPOINT=""
OTEL_LOOKBACK_SECONDS=""
COARSE_OUTPUT=""
SERVICE_NAME_ATTRIBUTE="service.name"
COARSE_START_NS=""
COARSE_END_NS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input)
      INPUT="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    --kind-container)
      KIND_CONTAINER="$2"
      shift 2
      ;;
    --top-k)
      TOP_K="$2"
      shift 2
      ;;
    --min-count)
      MIN_COUNT="$2"
      shift 2
      ;;
    --duration)
      DURATION="$2"
      shift 2
      ;;
    --load-cmd)
      LOAD_CMD="$2"
      shift 2
      ;;
    --coarse-load-cmd)
      COARSE_LOAD_CMD="$2"
      shift 2
      ;;
    --fine-load-cmd)
      FINE_LOAD_CMD="$2"
      shift 2
      ;;
    --coarse-source)
      COARSE_SOURCE="$2"
      shift 2
      ;;
    --otel-endpoint)
      OTEL_ENDPOINT="$2"
      shift 2
      ;;
    --otel-lookback-seconds)
      OTEL_LOOKBACK_SECONDS="$2"
      shift 2
      ;;
    --coarse-output)
      COARSE_OUTPUT="$2"
      shift 2
      ;;
    --service-name-attribute)
      SERVICE_NAME_ATTRIBUTE="$2"
      shift 2
      ;;
    --exclude-operation-regex|--exclude-endpoint-regex)
      EXCLUDE_REGEX="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$OUT_DIR" || -z "$NAMESPACE" ]]; then
  echo "--out-dir and --namespace are required" >&2
  exit 1
fi

if [[ "$COARSE_SOURCE" != "otel" ]]; then
  echo "Unsupported --coarse-source '$COARSE_SOURCE'. Only 'otel' is supported." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
SELECTION_FILE="$OUT_DIR/selection.json"

if [[ -n "$LOAD_CMD" && -z "$FINE_LOAD_CMD" ]]; then
  FINE_LOAD_CMD="$LOAD_CMD"
fi

if [[ -z "$OTEL_LOOKBACK_SECONDS" ]]; then
  OTEL_LOOKBACK_SECONDS="$((DURATION + 30))"
fi

if [[ -z "$COARSE_OUTPUT" ]]; then
  COARSE_OUTPUT="$OUT_DIR/coarse_records.jsonl"
fi

if [[ -z "$INPUT" ]]; then
  if [[ -z "$OTEL_ENDPOINT" ]]; then
    echo "--otel-endpoint is required when --input is not provided" >&2
    exit 1
  fi

  if [[ -n "$COARSE_LOAD_CMD" ]]; then
    echo "[adaptive-tracer] running coarse workload..."
    COARSE_START_NS="$(python3 - <<'PY'
import time
print(time.time_ns())
PY
)"
    bash -lc "$COARSE_LOAD_CMD"
    COARSE_END_NS="$(python3 - <<'PY'
import time
print(time.time_ns())
PY
)"
  else
    echo "[adaptive-tracer] no coarse workload command supplied; fetching recent OTel traces only"
  fi

  echo "[adaptive-tracer] fetching fresh coarse traces from OTel..."
  FETCH_ARGS=(
    python3 "$SCRIPT_DIR/fetch_otel_traces.py"
    --endpoint "$OTEL_ENDPOINT"
    --output "$COARSE_OUTPUT"
    --lookback-seconds "$OTEL_LOOKBACK_SECONDS"
    --service-name-attribute "$SERVICE_NAME_ATTRIBUTE"
  )
  if [[ -n "$COARSE_START_NS" ]]; then
    FETCH_ARGS+=(--start-time-unix-nano "$COARSE_START_NS")
  fi
  if [[ -n "$COARSE_END_NS" ]]; then
    FETCH_ARGS+=(--end-time-unix-nano "$COARSE_END_NS")
  fi
  "${FETCH_ARGS[@]}"
  INPUT="$COARSE_OUTPUT"
fi

if [[ ! -f "$INPUT" ]]; then
  echo "Input trace file not found: $INPUT" >&2
  exit 1
fi

if [[ ! -s "$INPUT" ]]; then
  echo "Input trace file is empty: $INPUT" >&2
  exit 1
fi

echo "[adaptive-tracer] selecting suspicious services from normalized coarse traces..."
python3 "$SCRIPT_DIR/select_targets.py" \
  --input "$INPUT" \
  --output "$SELECTION_FILE" \
  --top-k "$TOP_K" \
  --min-count "$MIN_COUNT" \
  --exclude-operation-regex "$EXCLUDE_REGEX"

echo "[adaptive-tracer] selected services:"
python3 - "$SELECTION_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
for service in data.get("selected_services", []):
    print(f"  - {service}")
PY

SELECTED_COUNT="$(
  python3 - "$SELECTION_FILE" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
print(len(data.get("selected_services", [])))
PY
)"

COARSE_RECORD_COUNT="$(
  python3 - "$INPUT" <<'PY'
import sys
count = 0
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    for line in handle:
        if line.strip():
            count += 1
print(count)
PY
)"

if [[ "$SELECTED_COUNT" -eq 0 ]]; then
  echo "No services were selected from coarse traces; aborting before fine tracing." >&2
  exit 1
fi

RECORD_ARGS=(
  bash "$SCRIPT_DIR/record_external.sh"
  --namespace "$NAMESPACE"
  --selection-file "$SELECTION_FILE"
  --out-dir "$OUT_DIR"
  --duration "$DURATION"
  --coarse-source "$COARSE_SOURCE"
  --coarse-input-file "$INPUT"
  --coarse-record-count "$COARSE_RECORD_COUNT"
  --otel-endpoint "$OTEL_ENDPOINT"
  --otel-lookback-seconds "$OTEL_LOOKBACK_SECONDS"
)

if [[ -n "$KIND_CONTAINER" ]]; then
  RECORD_ARGS+=(--kind-container "$KIND_CONTAINER")
fi
if [[ -n "$FINE_LOAD_CMD" ]]; then
  RECORD_ARGS+=(--load-cmd "$FINE_LOAD_CMD")
fi

"${RECORD_ARGS[@]}"
