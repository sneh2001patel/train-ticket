#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

INPUT=""
OUT_DIR=""
NAMESPACE=""
KIND_CONTAINER=""
TOP_K="3"
MIN_COUNT="20"
DURATION="60"
LOAD_CMD=""
EXCLUDE_REGEX='^(Mysql/|HikariCP/|/actuator|/swagger|/v2/api-docs|/webjars)'
TRACE_INDEX=""
COARSE_LOAD_CMD=""
FINE_LOAD_CMD=""
EXPORT_CMD=""

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
    --export-cmd)
      EXPORT_CMD="$2"
      shift 2
      ;;
    --trace-index)
      TRACE_INDEX="$2"
      shift 2
      ;;
    --exclude-endpoint-regex)
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

mkdir -p "$OUT_DIR"
SELECTION_FILE="$OUT_DIR/selection.json"

if [[ -n "$LOAD_CMD" && -z "$FINE_LOAD_CMD" ]]; then
  FINE_LOAD_CMD="$LOAD_CMD"
fi

if [[ -z "$TRACE_INDEX" ]]; then
  TRACE_INDEX="sw_segment-$(date +%Y%m%d)"
fi

if [[ -z "$COARSE_LOAD_CMD" ]]; then
  COARSE_LOAD_CMD="cd \"$REPO_ROOT/pythia/scripts\" && bash generate_traces.sh"
fi

if [[ -z "$EXPORT_CMD" ]]; then
  EXPORT_CMD="cd \"$REPO_ROOT/pythia/scripts\" && bash export_traces.sh \"$TRACE_INDEX\""
fi

if [[ -z "$FINE_LOAD_CMD" ]]; then
  FINE_LOAD_CMD="cd \"$REPO_ROOT/pythia/scripts\" && bash generate_traces.sh"
fi

if [[ -z "$INPUT" ]]; then
  INPUT="$HOME/export1_${TRACE_INDEX}.jsonl"
  echo "[adaptive-tracer] running coarse workload..."
  bash -lc "$COARSE_LOAD_CMD"

  echo "[adaptive-tracer] exporting fresh coarse traces..."
  bash -lc "$EXPORT_CMD"
fi

if [[ ! -f "$INPUT" ]]; then
  echo "Input trace file not found: $INPUT" >&2
  exit 1
fi

if [[ ! -s "$INPUT" ]]; then
  echo "Input trace file is empty: $INPUT" >&2
  exit 1
fi

echo "[adaptive-tracer] selecting suspicious services from coarse traces..."
python3 "$SCRIPT_DIR/select_targets.py" \
  --input "$INPUT" \
  --output "$SELECTION_FILE" \
  --top-k "$TOP_K" \
  --min-count "$MIN_COUNT" \
  --exclude-endpoint-regex "$EXCLUDE_REGEX"

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
)

if [[ -n "$KIND_CONTAINER" ]]; then
  RECORD_ARGS+=(--kind-container "$KIND_CONTAINER")
fi
if [[ -n "$FINE_LOAD_CMD" ]]; then
  RECORD_ARGS+=(--load-cmd "$FINE_LOAD_CMD")
fi

"${RECORD_ARGS[@]}"
