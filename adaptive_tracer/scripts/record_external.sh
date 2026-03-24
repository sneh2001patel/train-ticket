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
TRACE_CMD=()
LOAD_EXIT_CODE=0
COARSE_SOURCE=""
COARSE_INPUT_FILE=""
COARSE_RECORD_COUNT="0"
OTEL_ENDPOINT=""
OTEL_LOOKBACK_SECONDS=""

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
    --coarse-source)
      COARSE_SOURCE="$2"
      shift 2
      ;;
    --coarse-input-file)
      COARSE_INPUT_FILE="$2"
      shift 2
      ;;
    --coarse-record-count)
      COARSE_RECORD_COUNT="$2"
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

if [[ -n "$SELECTION_FILE" ]]; then
  REQUESTED_COUNT="$(
    python3 - "$TARGETS_JSON" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
print(len(data.get("requested_services", [])))
PY
  )"
  if [[ "$REQUESTED_COUNT" -eq 0 ]]; then
    echo "Selection file did not contain any services; refusing to trace the full namespace." >&2
    exit 1
  fi
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
    if docker exec "$KIND_CONTAINER" sh -lc "kill -0 $pid" >/dev/null 2>&1; then
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

TRACE_TARGET_IDS=()
for pid in "${LIVE_PIDS[@]}"; do
  if [[ -n "$KIND_CONTAINER_PID" ]]; then
    mapfile -t TASK_IDS < <(
      docker exec "$KIND_CONTAINER" sh -lc "ls /proc/$pid/task 2>/dev/null | sort -n"
    )
  else
    mapfile -t TASK_IDS < <(ls "/proc/$pid/task" 2>/dev/null | sort -n)
  fi

  if [[ ${#TASK_IDS[@]} -eq 0 ]]; then
    TASK_IDS=("$pid")
  fi

  for task_id in "${TASK_IDS[@]}"; do
    TRACE_TARGET_IDS+=("$task_id")
  done
done

if [[ ${#TRACE_TARGET_IDS[@]} -eq 0 ]]; then
  echo "No traceable thread IDs resolved from live PIDs." >&2
  exit 1
fi

P_ARGS=()
for target_id in "${TRACE_TARGET_IDS[@]}"; do
  P_ARGS+=(-p "$target_id")
done

START_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

python3 - "$TARGETS_JSON" "$TRACE_META" "$START_TS" "$DURATION" "$LOAD_CMD" "${#TRACE_TARGET_IDS[@]}" "$COARSE_SOURCE" "$COARSE_INPUT_FILE" "$COARSE_RECORD_COUNT" "$OTEL_ENDPOINT" "$OTEL_LOOKBACK_SECONDS" <<'PY'
import json
import sys

(
    targets_path,
    meta_path,
    start_ts,
    duration,
    load_cmd,
    resolved_thread_count,
    coarse_source,
    coarse_input_file,
    coarse_record_count,
    otel_endpoint,
    otel_lookback_seconds,
) = sys.argv[1:]
with open(targets_path, "r", encoding="utf-8") as handle:
    targets = json.load(handle)

meta = {
    "start_time": start_ts,
    "duration_seconds": int(duration),
    "load_cmd": load_cmd,
    "trace_status": "starting",
    "namespace": targets.get("namespace"),
    "requested_services": targets.get("requested_services", []),
    "selected_services": targets.get("requested_services", []),
    "resolved_target_count": len(targets.get("resolved_targets", [])),
    "resolved_thread_count": int(resolved_thread_count),
    "resolved_targets": targets.get("resolved_targets", []),
    "coarse_source": coarse_source or None,
    "coarse_input_file": coarse_input_file or None,
    "coarse_record_count": int(coarse_record_count or 0),
    "selection_input_path": coarse_input_file or None,
    "otel_endpoint": otel_endpoint or None,
    "otel_lookback_seconds": int(otel_lookback_seconds or 0),
}

with open(meta_path, "w", encoding="utf-8") as handle:
    json.dump(meta, handle, indent=2)
PY

TRACE_CMD=(
  strace
  -ff
  -tt
  -T
  -s 256
  -yy
  -e trace=network,desc,read,write,futex,clone,execve,epoll_wait,epoll_ctl,poll,ppoll,select,accept,accept4,openat,close
  -o "$OUT_DIR/strace.log"
  "${P_ARGS[@]}"
)

echo "[adaptive-tracer] attaching strace to ${#LIVE_PIDS[@]} target processes..."
if [[ -n "$KIND_CONTAINER_PID" ]]; then
  echo "[adaptive-tracer] entering KIND PID namespace via host PID $KIND_CONTAINER_PID"
  sudo nsenter -t "$KIND_CONTAINER_PID" -p -- \
    "${TRACE_CMD[@]}" &
else
  sudo "${TRACE_CMD[@]}" &
fi
STRACE_PID=$!

cleanup() {
  sudo kill "$STRACE_PID" 2>/dev/null || true
  wait "$STRACE_PID" 2>/dev/null || true

  sudo chown "$(id -u):$(id -g)" "$OUT_DIR"/strace.log.* 2>/dev/null || true

  END_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  python3 - "$TRACE_META" "$END_TS" "$LOAD_EXIT_CODE" "$OUT_DIR" <<'PY'
import json
import os
from pathlib import Path
import sys

meta_path, end_ts, load_exit_code, out_dir = sys.argv[1:]
with open(meta_path, "r", encoding="utf-8") as handle:
    meta = json.load(handle)

artifact_stats = []
for path in sorted(Path(out_dir).glob("strace.log.*")):
    line_count = 0
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line_count, _ in enumerate(handle, start=1):
            pass
    artifact_stats.append(
        {
            "file": path.name,
            "size_bytes": path.stat().st_size,
            "line_count": line_count,
        }
    )

meta["end_time"] = end_ts
meta["load_exit_code"] = int(load_exit_code)
meta["trace_status"] = "complete"
meta["trace_artifacts"] = artifact_stats
meta["trace_artifact_count"] = len(artifact_stats)
meta["trace_artifact_nonempty_count"] = sum(
    1 for item in artifact_stats if item["line_count"] > 1 or item["size_bytes"] > 80
)
with open(meta_path, "w", encoding="utf-8") as handle:
    json.dump(meta, handle, indent=2)
PY
}

trap cleanup EXIT

sleep 2
if ! kill -0 "$STRACE_PID" >/dev/null 2>&1; then
  echo "strace exited before the trace window started." >&2
  exit 1
fi

TRACE_START_EPOCH="$(date +%s)"
TRACE_END_EPOCH=$((TRACE_START_EPOCH + DURATION))

if [[ -n "$LOAD_CMD" ]]; then
  echo "[adaptive-tracer] running load command..."
  set +e
  timeout --signal TERM --kill-after=5 "${DURATION}s" bash -lc "$LOAD_CMD"
  LOAD_EXIT_CODE=$?
  set -e
  if [[ "$LOAD_EXIT_CODE" -eq 124 ]]; then
    echo "[adaptive-tracer] load command reached the trace window timeout"
  elif [[ "$LOAD_EXIT_CODE" -ne 0 ]]; then
    echo "[adaptive-tracer] load command exited with status $LOAD_EXIT_CODE" >&2
  fi
else
  echo "[adaptive-tracer] no load command supplied; sleeping for ${DURATION}s"
  LOAD_EXIT_CODE=0
fi

NOW_EPOCH="$(date +%s)"
if (( NOW_EPOCH < TRACE_END_EPOCH )); then
  REMAINING_SECONDS=$((TRACE_END_EPOCH - NOW_EPOCH))
  echo "[adaptive-tracer] keeping strace attached for ${REMAINING_SECONDS}s more"
  sleep "$REMAINING_SECONDS"
fi

echo "[adaptive-tracer] recording complete"
