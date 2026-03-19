#!/bin/bash
set -euo pipefail

INDEX="${1:-$(curl -s "http://127.0.0.1:9200/_cat/indices/sw_segment*?h=index" | sort | tail -n 1)}"
SERVICE_ID="dHMtb3JkZXItc2VydmljZQ==.1"
OUT="$HOME/skywalking_order_segments.json"

echo "Exporting ts-order-service spans from index: $INDEX"
echo "Output file: $OUT"

curl -s -H 'Content-Type: application/json' \
  "http://127.0.0.1:9200/$INDEX/_search?scroll=2m" \
  -d "{
    \"size\": 1000,
    \"query\": {
      \"term\": {
        \"service_id\": \"$SERVICE_ID\"
      }
    }
  }" > /tmp/order_page1.json

SCROLL_ID=$(jq -r '._scroll_id' /tmp/order_page1.json)

jq -c '.hits.hits[]._source' /tmp/order_page1.json > "$OUT"

while true; do
  RESP=$(curl -s -H 'Content-Type: application/json' \
    "http://127.0.0.1:9200/_search/scroll" \
    -d "{\"scroll\":\"2m\",\"scroll_id\":\"$SCROLL_ID\"}")

  COUNT=$(echo "$RESP" | jq '.hits.hits | length')

  if [[ "$COUNT" -eq 0 ]]; then
    break
  fi

  echo "$RESP" | jq -c '.hits.hits[]._source' >> "$OUT"
  SCROLL_ID=$(echo "$RESP" | jq -r '._scroll_id')
done

echo "Done."
wc -l "$OUT"
ls -lh "$OUT"
