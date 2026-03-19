#!/usr/bin/env bash



# Baseline
kubectl -n train-ticket set env deploy/ts-order-service PYTHIA_FINE_TRACE=true PYTHIA_INJECT_DELAY=false
kubectl -n train-ticket rollout status deploy/ts-order-service
./generate_traces.sh
./collect_pythia_outputs.sh


# Fault
# kubectl -n train-ticket set env deploy/ts-order-service PYTHIA_FINE_TRACE=true PYTHIA_INJECT_DELAY=true
# kubectl -n train-ticket rollout status deploy/ts-order-service
# ./generate_traces.sh
# ./collect_pythia_outputs.sh
