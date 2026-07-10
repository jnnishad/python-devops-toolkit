"""GitOps drift detection: diff the manifest checked into Git against
what's actually running in the cluster.

A naive `diff` between a YAML file and `kubectl get -o yaml` is useless —
the live object accumulates a `status` block, a `resourceVersion`,
`managedFields`, defaulted fields the API server filled in, and an
`kubectl.kubernetes.io/last-applied-configuration` annotation, all of
which show up as "drift" even when nothing actually changed. This module
normalizes both sides down to the fields a human actually authors before
diffing, so the report only surfaces real, actionable drift (someone ran
`kubectl edit`, a controller mutated something it shouldn't have, etc).

`normalize_manifest` / `diff_manifests` are pure and unit tested against
plain dicts. `check_namespace_drift` is the kubectl-backed live wrapper.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

_SERVER_SET_METADATA_FIELDS = {
    "creationTimestamp",
    "resourceVersion",
    "uid",
    "generation",
    "selfLink",
    "managedFields",
}

_SERVER_SET_ANNOTATIONS = {
    "kubectl.kubernetes.io/last-applied-configuration",
    "deployment.kubernetes.io/revision",
}

_TOP_LEVEL_IGNORE = {"status"}


def normalize_manifest(manifest: dict) -> dict:
    """Strip fields the API server sets/mutates so a desired manifest and
    a live object can be compared on their actual intent.
    """
    normalized = {k: v for k, v in manifest.items() if k not in _TOP_LEVEL_IGNORE}

    metadata = dict(normalized.get("metadata", {}))
    for field_name in _SERVER_SET_METADATA_FIELDS:
        metadata.pop(field_name, None)

    annotations = {
        k: v
        for k, v in metadata.get("annotations", {}).items()
        if k not in _SERVER_SET_ANNOTATIONS
    }
    if annotations:
        metadata["annotations"] = annotations
    else:
        metadata.pop("annotations", None)

    normalized["metadata"] = metadata
    return normalized


@dataclass
class FieldDrift:
    path: str
    desired: object
    live: object


@dataclass
class DriftReport:
    kind: str
    name: str
    namespace: str
    drifted_fields: list[FieldDrift] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.drifted_fields)


def _diff_recursive(desired, live, path: str, out: list[FieldDrift]) -> None:
    if isinstance(desired, dict) and isinstance(live, dict):
        for key in sorted(set(desired) | set(live)):
            sub_path = f"{path}.{key}" if path else key
            if key not in desired:
                out.append(FieldDrift(sub_path, None, live[key]))
            elif key not in live:
                out.append(FieldDrift(sub_path, desired[key], None))
            else:
                _diff_recursive(desired[key], live[key], sub_path, out)
    elif isinstance(desired, list) and isinstance(live, list):
        if desired != live:
            out.append(FieldDrift(path, desired, live))
    else:
        if desired != live:
            out.append(FieldDrift(path, desired, live))


def diff_manifests(desired: dict, live: dict) -> DriftReport:
    """Compare a Git-sourced manifest against a live cluster object
    (both already normalized, or raw — normalization is applied here)
    and return every field that differs.
    """
    desired_norm = normalize_manifest(desired)
    live_norm = normalize_manifest(live)

    kind = desired_norm.get("kind", live_norm.get("kind", "Unknown"))
    metadata = desired_norm.get("metadata", {})
    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "default")

    drifted: list[FieldDrift] = []
    _diff_recursive(desired_norm, live_norm, "", drifted)

    return DriftReport(kind=kind, name=name, namespace=namespace, drifted_fields=drifted)


def check_namespace_drift(
    namespace: str,
    desired_manifests: list[dict],
    kubectl_bin: str = "kubectl",
) -> list[DriftReport]:
    """For each desired manifest (already parsed from the Git-tracked
    YAML), fetch the live object from the cluster and diff it.

    Objects that don't exist live at all are reported with a single
    synthetic drift entry rather than raising, so a bulk drift check
    across a whole namespace doesn't abort on the first missing resource.
    """
    reports = []
    for manifest in desired_manifests:
        kind = manifest["kind"]
        name = manifest["metadata"]["name"]

        result = subprocess.run(
            [kubectl_bin, "get", kind, name, "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            reports.append(
                DriftReport(
                    kind=kind,
                    name=name,
                    namespace=namespace,
                    drifted_fields=[FieldDrift(path="<object>", desired="exists", live="missing")],
                )
            )
            continue

        live = json.loads(result.stdout)
        reports.append(diff_manifests(manifest, live))

    return reports
