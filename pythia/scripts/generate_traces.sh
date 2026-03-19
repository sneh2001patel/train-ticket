#!/bin/bash

BASE="http://127.0.0.1:8080"

TOTAL=1000
USERNAME="fdse_microservice"
PASSWORD="111111"

echo "Generating $TOTAL workflows..."

for ((i=1;i<=TOTAL;i++))
do
  echo "Workflow $i"

  # login
  TOKEN=$(curl -s -X POST "$BASE/api/v1/users/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" \
    | jq -r '.data.token')

  # search train
  SEARCH=$(curl -s "$BASE/api/v1/travelservice/trips/left?startingPlace=Shanghai&endPlace=Beijing&departureTime=2026-03-10")

  TRIP_ID=$(echo $SEARCH | jq -r '.data[0].tripId')

  # book ticket
  ORDER=$(curl -s -X POST "$BASE/api/v1/orderservice/order" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
      \"accountId\":\"$USERNAME\",
      \"contactsId\":\"1\",
      \"tripId\":\"$TRIP_ID\",
      \"seatType\":2,
      \"date\":\"2026-03-10\"
    }")

  ORDER_ID=$(echo $ORDER | jq -r '.data.id')

  # pay
  curl -s -X POST "$BASE/api/v1/inside_pay_service/pay" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"orderId\":\"$ORDER_ID\"}" > /dev/null

  # cancel
  curl -s -X POST "$BASE/api/v1/cancelservice/cancel/$ORDER_ID" \
    -H "Authorization: Bearer $TOKEN" > /dev/null

done

echo "Done generating workflows."







