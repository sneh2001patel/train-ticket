#!/bin/bash
set -euo pipefail

NAMESPACE="train-ticket"
POD=$(kubectl -n $NAMESPACE get pods | grep ts-order-service | awk '{print $1}' | head -n 1)

OUTDIR="${1:-$HOME/pythia_logs}"
mkdir -p "$OUTDIR"

echo "Using pod: $POD"
echo "Saving logs to: $OUTDIR"

FULL_LOG="$OUTDIR/pythia_order_service_fault_run.log"
BASELINE_LOG="$OUTDIR/pythia_order_service_baseline_only.log"
FAULT_LOG="$OUTDIR/pythia_order_service_fault_only.log"
SAMPLE_LOG="$OUTDIR/pythia_sample_logs.txt"

# --------------------------------------------------
# 1. Get all Pythia logs (full run)
# --------------------------------------------------
kubectl -n $NAMESPACE logs "$POD" --tail=20000 | grep PYTHIA > "$FULL_LOG" || true

echo "[✓] Full run log created"

# --------------------------------------------------
# 2. Baseline (no delay)
# --------------------------------------------------
grep -v PYTHIA_DELAY "$FULL_LOG" > "$BASELINE_LOG" || true

echo "[✓] Baseline log created"

# --------------------------------------------------
# 3. Fault-only (only delay events)
# --------------------------------------------------
grep PYTHIA_DELAY "$FULL_LOG" > "$FAULT_LOG" || true

echo "[✓] Fault log created"

# --------------------------------------------------
# 4. Sample logs (first 50 lines)
# --------------------------------------------------
head -n 50 "$FULL_LOG" > "$SAMPLE_LOG"

echo "[✓] Sample log created"

# --------------------------------------------------
# Summary
# --------------------------------------------------
echo ""
echo "Summary:"
wc -l "$FULL_LOG" "$BASELINE_LOG" "$FAULT_LOG" "$SAMPLE_LOG"

echo ""
echo "Files:"
ls -lh "$OUTDIR"
