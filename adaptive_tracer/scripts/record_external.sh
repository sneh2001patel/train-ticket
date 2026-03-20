#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NAMESPACE=""
SERVICES=""
SELECTION_FILE=""
KIND_CONTAINER=""
OUT_DIR=""
DURATION="60"
LOAD_CMD=""
KIND_CONTAINER_PID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    --services)
      SERVICES="$2"
      shift 2
      ;;
    --selection-file)
      SELECTION_FILE="$2"
      shift 2
      ;;
    --kind-container)
      KIND_CONTAINER="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
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
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$NAMESPACE" ]]; then
  echo "--namespace is required" >&2
  exit 1
fi

if [[ -z "$OUT_DIR" ]]; then
  echo "--out-dir is required" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
TARGETS_JSON="$OUT_DIR/targets.json"
TRACE_META="$OUT_DIR/trace_meta.json"

if ! command -v strace >/dev/null 2>&1; then
  echo "strace is required but was not found in PATH." >&2
  exit 1
fi

echo "[adaptive-tracer] resolving target PIDs..."

RESOLVE_ARGS=(python3 "$SCRIPT_DIR/resolve_pids.py" --namespace "$NAMESPACE" --output "$TARGETS_JSON")
if [[ -n "$SERVICES" ]]; then
  RESOLVE_ARGS+=(--services "$SERVICES")
fi
if [[ -n "$SELECTION_FILE" ]]; then
  RESOLVE_ARGS+=(--selection-file "$SELECTION_FILE")
fi
if [[ -n "$KIND_CONTAINER" ]]; then
  RESOLVE_ARGS+=(--kind-container "$KIND_CONTAINER")
fi

"${RESOLVE_ARGS[@]}" >/dev/null

mapfile -t TRACE_PIDS < <(
  python3 - "$TARGETS_JSON" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
for item in data.get("resolved_targets", []):
    print(item["pid"])
PY
)

if [[ ${#TRACE_PIDS[@]} -eq 0 ]]; then
  echo "No PIDs resolved for tracing." >&2
  exit 1
fi

if [[ -n "$KIND_CONTAINER" ]]; then
  KIND_CONTAINER_PID="$(docker inspect --format '{{.State.Pid}}' "$KIND_CONTAINER")"
  if [[ -z "$KIND_CONTAINER_PID" || "$KIND_CONTAINER_PID" == "0" ]]; then
    echo "Could not resolve host PID for KIND container $KIND_CONTAINER." >&2
    exit 1
  fi
fi

LIVE_PIDS=()
for pid in "${TRACE_PIDS[@]}"; do
  if [[ -n "$KIND_CONTAINER_PID" ]]; then
    if sudo nsenter -t "$KIND_CONTAINER_PID" -p -- bash -lc "kill -0 $pid" >/dev/null 2>&1; then
      LIVE_PIDS+=("$pid")
    else
      echo "[adaptive-tracer] skipping dead KIND-namespace PID $pid"
    fi
  else
    if kill -0 "$pid" >/dev/null 2>&1; then
      LIVE_PIDS+=("$pid")
    else
      echo "[adaptive-tracer] skipping dead host PID $pid"
    fi
  fi
done

if [[ ${#LIVE_PIDS[@]} -eq 0 ]]; then
  echo "No live PIDs available for tracing after validation." >&2
  exit 1
fi

P_ARGS=()
for pid in "${LIVE_PIDS[@]}"; do
  P_ARGS+=(-p "$pid")
done

START_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

python3 - "$TARGETS_JSON" "$TRACE_META" "$START_TS" "$DURATION" "$LOAD_CMD" <<'PY'
import json
import sys

targets_path, meta_path, start_ts, duration, load_cmd = sys.argv[1:]
with open(targets_path, "r", encoding="utf-8") as handle:
    targets = json.load(handle)

meta = {
    "start_time": start_ts,
    "duration_seconds": int(duration),
    "load_cmd": load_cmd,
    "namespace": targets.get("namespace"),
    "requested_services": targets.get("requested_services", []),
    "resolved_target_count": len(targets.get("resolved_targets", [])),
    "resolved_targets": targets.get("resolved_targets", []),
}

with open(meta_path, "w", encoding="utf-8") as handle:
    json.dump(meta, handle, indent=2)
PY

echo "[adaptive-tracer] attaching strace to ${#LIVE_PIDS[@]} target processes..."
if [[ -n "$KIND_CONTAINER_PID" ]]; then
  echo "[adaptive-tracer] entering KIND PID namespace via host PID $KIND_CONTAINER_PID"
  sudo nsenter -t "$KIND_CONTAINER_PID" -p -- \
    strace -ff -tt -T \
      -e trace=network,read,write,futex,clone,execve \
      -o "$OUT_DIR/strace.log" \
      "${P_ARGS[@]}" &
else
  sudo strace -ff -tt -T \
    -e trace=network,read,write,futex,clone,execve \
    -o "$OUT_DIR/strace.log" \
    "${P_ARGS[@]}" &
fi
STRACE_PID=$!

cleanup() {
  sudo kill "$STRACE_PID" 2>/dev/null || true
  wait "$STRACE_PID" 2>/dev/null || true

  END_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  python3 - "$TRACE_META" "$END_TS" <<'PY'
import json
import sys

meta_path, end_ts = sys.argv[1:]
with open(meta_path, "r", encoding="utf-8") as handle:
    meta = json.load(handle)
meta["end_time"] = end_ts
with open(meta_path, "w", encoding="utf-8") as handle:
    json.dump(meta, handle, indent=2)
PY
}

trap cleanup EXIT

sleep 2

if [[ -n "$LOAD_CMD" ]]; then
  echo "[adaptive-tracer] running load command..."
  bash -lc "$LOAD_CMD"
else
  echo "[adaptive-tracer] no load command supplied; sleeping for ${DURATION}s"
  sleep "$DURATION"
fi

echo "[adaptive-tracer] recording complete"
