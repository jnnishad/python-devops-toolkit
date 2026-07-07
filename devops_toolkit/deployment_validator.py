"""Kubernetes deployment rollout validation — the "deployment
validations" half of the Adform automation work. Parsing logic is pure
(no cluster access) so it's fully unit-testable; `validate_live()` is a
thin wrapper that shells out to kubectl for real use.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class RolloutStatus:
    name: str
    namespace: str
    desired_replicas: int
    available_replicas: int
    updated_replicas: int
    healthy: bool
    reason: str


def evaluate_rollout(deployment_json: dict) -> RolloutStatus:
    """Evaluate a `kubectl get deployment -o json` payload (already parsed)
    and decide whether the rollout looks healthy.

    A deployment is considered healthy when:
      - available replicas == desired replicas, and
      - updated replicas == desired replicas (no old-version pods still running)
    """
    metadata = deployment_json.get("metadata", {})
    spec = deployment_json.get("spec", {})
    status = deployment_json.get("status", {})

    name = metadata.get("name", "unknown")
    namespace = metadata.get("namespace", "default")
    desired = spec.get("replicas", 0)
    available = status.get("availableReplicas", 0)
    updated = status.get("updatedReplicas", 0)

    if available < desired:
        return RolloutStatus(
            name, namespace, desired, available, updated,
            healthy=False,
            reason=f"only {available}/{desired} replicas available",
        )

    if updated < desired:
        return RolloutStatus(
            name, namespace, desired, available, updated,
            healthy=False,
            reason=f"rollout in progress: {updated}/{desired} replicas updated to latest revision",
        )

    return RolloutStatus(
        name, namespace, desired, available, updated,
        healthy=True,
        reason="all replicas available and up to date",
    )


def validate_live(name: str, namespace: str = "default", kubectl_bin: str = "kubectl") -> RolloutStatus:
    """Shell out to kubectl and evaluate the live rollout status.

    Requires a working kubeconfig; not used by the test suite.
    """
    result = subprocess.run(
        [kubectl_bin, "get", "deployment", name, "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    return evaluate_rollout(payload)
