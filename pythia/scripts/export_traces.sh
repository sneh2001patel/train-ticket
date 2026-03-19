#!/bin/bash

INDEX=${1:-sw_segment-20260304}
OUT="$HOME/export1_${INDEX}.jsonl"

echo "Exporting traces from index: $INDEX"

curl -s -H 'Content-Type: application/json' \
"http://127.0.0.1:9200/$INDEX/_search?scroll=2m" \
-d '{"size":1000,"query":{"match_all":{}}}' > /tmp/page1.json

SCROLL_ID=$(jq -r '._scroll_id' /tmp/page1.json)

jq -c '.hits.hits[]._source' /tmp/page1.json > "$OUT"

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

echo "Export complete"
echo "Saved file: $OUT"

wc -l "$OUT"
ls -lh "$OUT"
