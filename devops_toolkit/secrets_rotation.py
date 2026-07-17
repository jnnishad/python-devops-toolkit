"""Secret rotation orchestration across AWS Secrets Manager and Kubernetes.

Rotating a secret value is the easy part; the part that actually causes
incidents is restarting every workload that consumes it, in the wrong
order, all at once. This module builds a dependency graph of which
Deployments reference a given secret (via `envFrom.secretRef` or
`env[].valueFrom.secretKeyRef`) and produces a wave-by-wave restart plan
— a small canary wave first, then the rest — so a bad rotation only takes
down one deployment's worth of pods instead of every consumer at once.

`plan_rotation` (dependency discovery + wave ordering) is pure and unit
tested against plain dicts. `rotate_secret` is the thin AWS/kubectl-backed
wrapper that executes the plan against a real account and cluster,
taking a full {key: value} map so it works for multi-key Secrets, not
just single-value ones.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class RotationWave:
    deployments: list[str] = field(default_factory=list)  # "namespace/name"


@dataclass
class RotationPlan:
    secret_name: str
    waves: list[RotationWave] = field(default_factory=list)

    @property
    def total_deployments(self) -> int:
        return sum(len(w.deployments) for w in self.waves)


def _secret_names_used_by(container_env_sources: list[dict]) -> set[str]:
    names = set()
    for src in container_env_sources:
        secret_ref = src.get("secretRef", {})
        if "name" in secret_ref:
            names.add(secret_ref["name"])
    return names


def _deployment_secret_dependencies(deployment: dict) -> set[str]:
    names: set[str] = set()
    containers = deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    for container in containers:
        names |= _secret_names_used_by(container.get("envFrom", []))
        for env_var in container.get("env", []):
            key_ref = env_var.get("valueFrom", {}).get("secretKeyRef", {})
            if "name" in key_ref:
                names.add(key_ref["name"])
    return names


def find_dependent_deployments(secret_name: str, deployments: list[dict]) -> list[str]:
    """Return "namespace/name" for every deployment whose pod spec
    references `secret_name`, either as a whole envFrom source or a
    single secretKeyRef.
    """
    dependents = []
    for deployment in deployments:
        if secret_name in _deployment_secret_dependencies(deployment):
            metadata = deployment.get("metadata", {})
            dependents.append(f"{metadata.get('namespace', 'default')}/{metadata.get('name', 'unknown')}")
    return dependents


def plan_rotation(
    secret_name: str,
    deployments: list[dict],
    canary_labels: tuple[str, ...] = ("canary", "-canary"),
) -> RotationPlan:
    """Build a two-wave restart plan for every deployment that consumes
    `secret_name`: deployments whose name matches a canary marker go
    first (wave 1), everything else follows (wave 2). If nothing matches
    the canary heuristic, the first (alphabetically smallest) dependent
    deployment is used as the de facto canary so the plan is never a
    single all-at-once wave when there's more than one dependent.
    """
    dependents = sorted(find_dependent_deployments(secret_name, deployments))
    if not dependents:
        return RotationPlan(secret_name=secret_name, waves=[])

    canary = [d for d in dependents if any(marker in d for marker in canary_labels)]
    rest = [d for d in dependents if d not in canary]

    if not canary and len(dependents) > 1:
        canary = [dependents[0]]
        rest = dependents[1:]

    waves = []
    if canary:
        waves.append(RotationWave(deployments=canary))
    if rest:
        waves.append(RotationWave(deployments=rest))
    if not waves:
        waves.append(RotationWave(deployments=dependents))

    return RotationPlan(secret_name=secret_name, waves=waves)


def rotate_secret(
    secret_name: str,
    namespace: str,
    new_values: dict[str, str],
    aws_secret_id: str | None = None,
    aws_secret_string: str | None = None,
    wave_wait_seconds: float = 60.0,
    kubectl_bin: str = "kubectl",
    dry_run: bool = True,
) -> RotationPlan:
    """Rotate `secret_name` end to end:

    1. (optional) write the new value to AWS Secrets Manager
    2. patch the Kubernetes Secret with the new value(s)
    3. discover dependent Deployments and restart them wave by wave,
       waiting `wave_wait_seconds` between waves so a canary wave has
       time to prove itself before the rest roll

    `new_values` maps every key in the Secret's `data`/`stringData` to
    its new value -- e.g. {"username": "...", "password": "..."} for a
    multi-key credential secret, or {"value": "..."} for a single-value
    one. An earlier version of this function hardcoded a single "value"
    key, which silently broke for any secret with more than one key;
    this is why `find_dependent_deployments` matches on the whole
    Secret by name rather than by key, and why callers must be explicit
    about which key(s) they're rotating instead of this function
    guessing.

    Requires boto3 (lazy import, only if `aws_secret_id` is given) and a
    working kubeconfig. Set dry_run=False to actually execute; the plan
    is always returned either way.
    """
    deployments = json.loads(
        _run(kubectl_bin, "get", "deployments", "-n", namespace, "-o", "json")
    )["items"]
    plan = plan_rotation(secret_name, deployments)

    if dry_run:
        return plan

    if aws_secret_id:
        import boto3  # noqa: local import by design, see module docstring

        boto3.client("secretsmanager").put_secret_value(
            SecretId=aws_secret_id,
            SecretString=aws_secret_string if aws_secret_string is not None else json.dumps(new_values),
        )

    _run(
        kubectl_bin, "patch", "secret", secret_name,
        "-n", namespace,
        "--type=merge",
        "-p", json.dumps({"stringData": new_values}),
    )

    for i, wave in enumerate(plan.waves):
        for target in wave.deployments:
            ns, name = target.split("/", 1)
            _run(kubectl_bin, "rollout", "restart", "deployment", name, "-n", ns)
        for target in wave.deployments:
            ns, name = target.split("/", 1)
            _run(kubectl_bin, "rollout", "status", "deployment", name, "-n", ns, "--timeout=120s")
        if i < len(plan.waves) - 1:
            time.sleep(wave_wait_seconds)

    return plan


def _run(*args: str) -> str:
    result = subprocess.run(list(args), capture_output=True, text=True, check=True)
    return result.stdout
