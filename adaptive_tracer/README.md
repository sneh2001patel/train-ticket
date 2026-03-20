# Adaptive Tracer

This directory contains an external adaptive tracing workflow for Kubernetes
microservices.

The design is intentionally different from the earlier `pythia-lite`
instrumentation:

- coarse tracing stays outside the application code
- suspicious services are selected automatically from coarse trace variance
- fine tracing is enabled externally by attaching to the selected processes
- the same workflow can be reused across services instead of hardcoding
  `ts-order-service`

## Workflow

1. Export coarse traces from your tracing backend into JSONL.
   SkyWalking segment exports work out of the box.
2. Run `select_targets.py` to score services by latency variance.
3. Run `record_external.sh` to resolve Kubernetes container PIDs and attach
   `strace` only to the selected services.
4. Optionally wrap both steps with `run_adaptive_cycle.sh`.

## Files

- `scripts/select_targets.py`
  Ranks services from coarse traces and writes a selection manifest.
- `scripts/resolve_pids.py`
  Resolves Kubernetes pods and container IDs into host PIDs.
- `scripts/record_external.sh`
  Attaches `strace` to the selected services without modifying application code.
- `scripts/run_adaptive_cycle.sh`
  Orchestrates selection plus targeted recording in one command.

## Example

```bash
cd adaptive_tracer/scripts

python3 select_targets.py \
  --input ../../pythia/data/export1_sw_segment-20260310.jsonl \
  --output ../results/selection.json \
  --top-k 3

bash record_external.sh \
  --namespace train-ticket \
  --kind-container train-ticket-control-plane \
  --selection-file ../results/selection.json \
  --out-dir ../results/run_01 \
  --duration 120
```

Or end to end:

```bash
bash run_adaptive_cycle.sh \
  --input ../../pythia/data/export1_sw_segment-20260310.jsonl \
  --namespace train-ticket \
  --kind-container train-ticket-control-plane \
  --out-dir ../results/cycle_01 \
  --top-k 3 \
  --duration 120
```

## Assumptions

- `kubectl` can read the target namespace.
- `strace` is installed on the host where Kubernetes containers run.
- For KIND, the control-plane container is reachable with `docker exec`.
- For non-KIND setups, host `crictl inspect` should work.

## Output

Each run writes:

- `selection.json` with ranked suspicious services
- `targets.json` with resolved pods, containers, and PIDs
- `trace_meta.json` with runtime metadata
- `strace.log.<pid>` files from `strace -ff`

## Notes

- This tool is service-agnostic, but the quality of the initial selection still
  depends on the coarse trace source.
- The default endpoint filter excludes common noise like `Mysql/...` and
  actuator paths so service ranking is less biased by internal spans.
