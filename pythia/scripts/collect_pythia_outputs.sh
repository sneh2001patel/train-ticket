#!/usr/bin/env bash
set -euo pipefail

OUTDIR="${1:-$HOME/pythia_outputs}"
DATE_TAG="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$OUTDIR/$DATE_TAG"

POD="$(kubectl get pods | grep ts-order-service | awk '{print $1}' | head -n 1)"

if [[ -z "${POD}" ]]; then
  echo "Could not find ts-order-service pod"
  exit 1
fi

echo "Using pod: $POD"
echo "Saving outputs to: $OUTDIR/$DATE_TAG"

# 1) Full current order-service log
kubectl logs "$POD" --tail=5000 \
  > "$OUTDIR/$DATE_TAG/pythia_order_service_full.log"

# 2) Only Pythia lines
kubectl logs "$POD" --tail=5000 | grep PYTHIA \
  > "$OUTDIR/$DATE_TAG/pythia_order_service_only.log" || true

# 3) Small sample of Pythia lines
kubectl logs "$POD" --tail=200 | grep PYTHIA \
  > "$OUTDIR/$DATE_TAG/pythia_sample_logs.txt" || true

# 4) Extract service_create latencies
grep "stage=service_create latency_ms=" "$OUTDIR/$DATE_TAG/pythia_order_service_only.log" \
  | sed -E 's/.*latency_ms=([0-9]+).*/\1/' \
  > "$OUTDIR/$DATE_TAG/latencies.txt" || true

# 5) Count delay markers
grep -c "PYTHIA_DELAY" "$OUTDIR/$DATE_TAG/pythia_order_service_only.log" \
  > "$OUTDIR/$DATE_TAG/delay_count.txt" || true

# 6) Collect SkyWalking order-service segments
curl -s "http://127.0.0.1:9200/sw_segment-$(date +%Y%m%d)/_search?pretty" \
  -H 'Content-Type: application/json' \
  -d '{
    "size": 100,
    "_source": [
      "trace_id",
      "segment_id",
      "service_id",
      "service_instance_id",
      "endpoint_name",
      "start_time",
      "end_time",
      "latency",
      "statement",
      "tags"
    ],
    "query": {
      "term": {
        "service_id.keyword": "dHMtb3JkZXItc2VydmljZQ==.1"
      }
    },
    "sort": [
      { "start_time": { "order": "desc" } }
    ]
  }' | jq '.hits.hits[]._source' \
  > "$OUTDIR/$DATE_TAG/skywalking_order_segments.json"

# 7) Quick summary
LAT_COUNT="$(wc -l < "$OUTDIR/$DATE_TAG/latencies.txt" 2>/dev/null || echo 0)"
DELAY_COUNT="$(cat "$OUTDIR/$DATE_TAG/delay_count.txt" 2>/dev/null || echo 0)"

echo
echo "Done."
echo "Files created in: $OUTDIR/$DATE_TAG"
echo "Latency samples: $LAT_COUNT"
echo "Delay markers: $DELAY_COUNT"



