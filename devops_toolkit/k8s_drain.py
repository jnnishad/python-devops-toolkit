"""Safe node cordon-and-drain planning.

`kubectl drain` will happily evict a pod straight through a
PodDisruptionBudget violation attempt and retry-loop forever, and it
doesn't tell you up front which pods are actually safe to move. This
module computes an eviction plan first — which pods can be evicted
without violating their PDB, which are DaemonSet-managed and should be
left alone, and which would need a human to look at the PDB before
proceeding — and only then (optionally) executes it against a real
cluster.

The planning logic (`plan_eviction`) is pure: it takes already-parsed
`kubectl get pods/pdb -o json` payloads and returns a plan, so it's fully
unit-testable without a cluster. `drain_node()` is the thin kubectl-backed
wrapper used for real drains.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class EvictionPlan:
    node: str
    evictable: list[str] = field(default_factory=list)          # pod names safe to evict now
    blocked_by_pdb: list[str] = field(default_factory=list)      # would violate a PDB right now
    skipped_daemonset: list[str] = field(default_factory=list)   # DaemonSet-managed, never evicted
    skipped_mirror: list[str] = field(default_factory=list)      # static/mirror pods, kubelet-managed

    @property
    def is_fully_drainable(self) -> bool:
        return not self.blocked_by_pdb


def _is_daemonset_pod(pod: dict) -> bool:
    for owner in pod.get("metadata", {}).get("ownerReferences", []):
        if owner.get("kind") == "DaemonSet":
            return True
    return False


def _is_mirror_pod(pod: dict) -> bool:
    return "kubernetes.io/config.mirror" in pod.get("metadata", {}).get("annotations", {})


def _matches_selector(labels: dict, selector: dict) -> bool:
    return all(labels.get(k) == v for k, v in selector.items())


def plan_eviction(node: str, pods: list[dict], pdbs: list[dict]) -> EvictionPlan:
    """Decide, for every pod currently on `node`, whether it can be
    evicted right now without pushing a PodDisruptionBudget below its
    minimum-available floor.

    `pods` / `pdbs` are the parsed JSON objects from
    `kubectl get pods -A -o json --field-selector spec.nodeName=<node>`
    and `kubectl get pdb -A -o json` respectively.
    """
    plan = EvictionPlan(node=node)

    for pod in pods:
        name = pod.get("metadata", {}).get("name", "unknown")
        namespace = pod.get("metadata", {}).get("namespace", "default")

        if _is_daemonset_pod(pod):
            plan.skipped_daemonset.append(f"{namespace}/{name}")
            continue
        if _is_mirror_pod(pod):
            plan.skipped_mirror.append(f"{namespace}/{name}")
            continue

        labels = pod.get("metadata", {}).get("labels", {})
        blocking_pdb = _find_blocking_pdb(namespace, labels, pdbs)
        if blocking_pdb:
            plan.blocked_by_pdb.append(f"{namespace}/{name} (would violate {blocking_pdb})")
        else:
            plan.evictable.append(f"{namespace}/{name}")

    return plan


def _find_blocking_pdb(namespace: str, pod_labels: dict, pdbs: list[dict]) -> str | None:
    """Return the name of a PDB that would be violated if one more pod
    matching its selector were evicted right now, or None if eviction is
    safe with respect to every PDB in the namespace.
    """
    for pdb in pdbs:
        pdb_meta = pdb.get("metadata", {})
        if pdb_meta.get("namespace") != namespace:
            continue

        selector = pdb.get("spec", {}).get("selector", {}).get("matchLabels", {})
        if not _matches_selector(pod_labels, selector):
            continue

        status = pdb.get("status", {})
        disruptions_allowed = status.get("disruptionsAllowed", 0)
        if disruptions_allowed <= 0:
            return pdb_meta.get("name", "unknown-pdb")

    return None


def drain_node(
    node: str,
    dry_run: bool = True,
    grace_period_seconds: int = 30,
    poll_interval_seconds: float = 5.0,
    kubectl_bin: str = "kubectl",
) -> EvictionPlan:
    """Cordon `node`, compute an eviction plan, and (unless dry_run)
    evict every pod the plan marks as safe, one at a time, waiting for
    each eviction to be accepted before moving to the next.

    Pods blocked by a PDB are left in place and reported so an operator
    can decide whether to temporarily relax the budget.
    """
    _run(kubectl_bin, "cordon", node)

    pods = json.loads(
        _run(
            kubectl_bin, "get", "pods", "-A", "-o", "json",
            f"--field-selector=spec.nodeName={node}",
        )
    )["items"]
    pdbs = json.loads(_run(kubectl_bin, "get", "pdb", "-A", "-o", "json"))["items"]

    plan = plan_eviction(node, pods, pdbs)

    if dry_run:
        return plan

    for target in plan.evictable:
        namespace, name = target.split("/", 1)
        _run(
            kubectl_bin, "delete", "pod", name,
            "-n", namespace,
            f"--grace-period={grace_period_seconds}",
        )
        time.sleep(poll_interval_seconds)

    return plan


def _run(*args: str) -> str:
    result = subprocess.run(list(args), capture_output=True, text=True, check=True)
    return result.stdout
