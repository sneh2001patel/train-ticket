#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone


def parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve Kubernetes pod containers into host PIDs."
    )
    parser.add_argument("--namespace", required=True, help="Kubernetes namespace")
    parser.add_argument(
        "--services",
        default="",
        help="Comma-separated service names or substrings to match",
    )
    parser.add_argument(
        "--selection-file",
        help="Selection manifest JSON created by select_targets.py",
    )
    parser.add_argument(
        "--kind-container",
        default="",
        help="Optional KIND control-plane container name for docker exec + crictl",
    )
    parser.add_argument(
        "--output",
        help="Optional output JSON path; stdout is always emitted",
    )
    return parser.parse_args()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def run_command(cmd):
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def load_target_services(args):
    selected = []
    if args.services:
        selected.extend([item.strip() for item in args.services.split(",") if item.strip()])
    if args.selection_file:
        with open(args.selection_file, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        selected.extend(data.get("selected_services", []))
    seen = set()
    ordered = []
    for item in selected:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def get_pods(namespace):
    raw = run_command(["kubectl", "get", "pods", "-n", namespace, "-o", "json"])
    if raw is None:
        raise SystemExit("Failed to fetch pods with kubectl")
    return json.loads(raw)


def inspect_pid(container_id, kind_container):
    if kind_container:
        raw = run_command(
            [
                "docker",
                "exec",
                kind_container,
                "crictl",
                "inspect",
                "--output",
                "json",
                container_id,
            ]
        )
        if raw is not None:
            try:
                data = json.loads(raw)
                pid = data.get("info", {}).get("pid")
                if pid:
                    return int(pid), "kind-crictl"
            except (ValueError, TypeError):
                pass

    raw = run_command(["crictl", "inspect", "--output", "json", container_id])
    if raw is not None:
        try:
            data = json.loads(raw)
            pid = data.get("info", {}).get("pid")
            if pid:
                return int(pid), "host-crictl"
        except (ValueError, TypeError):
            pass

    return None, None


def normalize_container_id(container_id):
    if not container_id:
        return None
    if "://" in container_id:
        return container_id.split("://", 1)[1]
    return container_id


def pod_matches(pod, targets):
    if not targets:
        return True

    pod_name = pod.get("metadata", {}).get("name", "").lower()
    labels = pod.get("metadata", {}).get("labels", {}) or {}
    label_values = " ".join(str(v).lower() for v in labels.values())

    for target in targets:
        wanted = target.lower()
        if wanted in pod_name or wanted in label_values:
            return True
    return False


def build_targets(namespace, targets, kind_container):
    pod_data = get_pods(namespace)
    resolved = []

    for pod in pod_data.get("items", []):
        if pod.get("status", {}).get("phase") != "Running":
            continue
        if not pod_matches(pod, targets):
            continue

        pod_name = pod.get("metadata", {}).get("name")
        node_name = pod.get("spec", {}).get("nodeName")
        labels = pod.get("metadata", {}).get("labels", {}) or {}
        statuses = pod.get("status", {}).get("containerStatuses", []) or []

        for status in statuses:
            container_id = normalize_container_id(status.get("containerID"))
            if not container_id:
                continue
            pid, method = inspect_pid(container_id, kind_container)
            if not pid:
                continue

            guessed_service = (
                labels.get("app")
                or labels.get("app.kubernetes.io/name")
                or status.get("name")
                or pod_name
            )

            resolved.append(
                {
                    "service": guessed_service,
                    "pod": pod_name,
                    "container": status.get("name"),
                    "container_id": container_id,
                    "pid": pid,
                    "node": node_name,
                    "labels": labels,
                    "resolution_method": method,
                }
            )

    resolved.sort(key=lambda item: (item["service"], item["pod"], item["container"]))
    return resolved


def main():
    args = parse_args()
    targets = load_target_services(args)
    resolved = build_targets(args.namespace, targets, args.kind_container)

    payload = {
        "generated_at": utc_now(),
        "namespace": args.namespace,
        "requested_services": targets,
        "resolved_targets": resolved,
    }

    text = json.dumps(payload, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")

    print(text)
    if not resolved:
        sys.exit(2)


if __name__ == "__main__":
    main()
