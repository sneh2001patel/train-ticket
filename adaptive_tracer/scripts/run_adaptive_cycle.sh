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
EXCLUDE_REGEX='^(Mysql/|/actuator|/swagger|/v2/api-docs|/webjars)'

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

if [[ -z "$INPUT" || -z "$OUT_DIR" || -z "$NAMESPACE" ]]; then
  echo "--input, --out-dir, and --namespace are required" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
SELECTION_FILE="$OUT_DIR/selection.json"

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
if [[ -n "$LOAD_CMD" ]]; then
  RECORD_ARGS+=(--load-cmd "$LOAD_CMD")
fi

"${RECORD_ARGS[@]}"
