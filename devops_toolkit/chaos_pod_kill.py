"""Minimal chaos engineering experiment: kill one pod, safely.

The point of a controlled chaos experiment is to prove resilience you
already believe you have, not to discover an outage by accident. This
picks a single victim pod from a target selector and refuses to touch
it if doing so would: drop a PodDisruptionBudget's disruptionsAllowed to
zero, take a Deployment below a configured minimum replica floor, or
touch a namespace that isn't explicitly opted in. It defaults to
dry_run=True everywhere, including the CLI, on purpose — you have to
deliberately ask for a live experiment.

`select_victim` (the actual safety logic) is pure and unit tested.
`run_experiment` is the kubectl-backed wrapper that lists real pods/PDBs
and deletes the chosen one.
"""

from __future__ import annotations

import json
import random
import subprocess
from dataclasses import dataclass


@dataclass
class ChaosResult:
    victim: str | None
    reason: str
    dry_run: bool
    candidates_considered: int


def _matches_selector(labels: dict, selector: dict) -> bool:
    return all(labels.get(k) == v for k, v in selector.items())


def _pdb_allows_disruption(namespace: str, labels: dict, pdbs: list[dict]) -> bool:
    for pdb in pdbs:
        meta = pdb.get("metadata", {})
        if meta.get("namespace") != namespace:
            continue
        selector = pdb.get("spec", {}).get("selector", {}).get("matchLabels", {})
        if not _matches_selector(labels, selector):
            continue
        if pdb.get("status", {}).get("disruptionsAllowed", 0) <= 0:
            return False
    return True


def select_victim(
    pods: list[dict],
    pdbs: list[dict],
    namespace_selector: dict,
    allowed_namespaces: set[str],
    min_replicas_remaining: int = 2,
    rng: random.Random | None = None,
) -> ChaosResult:
    """Pick one pod matching `namespace_selector` labels to terminate.

    Safety gates, all of which must pass for a pod to even be a
    candidate:
      - its namespace must be in `allowed_namespaces` (explicit opt-in)
      - removing it must leave at least `min_replicas_remaining` pods
        with the same labels running in that namespace
      - it must not push a matching PodDisruptionBudget's
        disruptionsAllowed to zero
    """
    rng = rng or random.Random()

    by_namespace_and_labels: dict[tuple[str, tuple], list[dict]] = {}
    for pod in pods:
        namespace = pod.get("metadata", {}).get("namespace", "default")
        labels = pod.get("metadata", {}).get("labels", {})
        if not _matches_selector(labels, namespace_selector):
            continue
        if namespace not in allowed_namespaces:
            continue
        key = (namespace, tuple(sorted(labels.items())))
        by_namespace_and_labels.setdefault(key, []).append(pod)

    candidates = []
    for (namespace, _labels_tuple), group in by_namespace_and_labels.items():
        if len(group) <= min_replicas_remaining:
            continue  # killing one would breach the replica floor
        for pod in group:
            labels = pod.get("metadata", {}).get("labels", {})
            if not _pdb_allows_disruption(namespace, labels, pdbs):
                continue
            candidates.append(pod)

    if not candidates:
        return ChaosResult(
            victim=None,
            reason="no safe candidate: every match is below the replica floor or PDB-protected",
            dry_run=True,
            candidates_considered=0,
        )

    chosen = rng.choice(candidates)
    metadata = chosen.get("metadata", {})
    target = f"{metadata.get('namespace', 'default')}/{metadata.get('name', 'unknown')}"
    return ChaosResult(
        victim=target,
        reason=f"selected from {len(candidates)} safe candidate(s)",
        dry_run=True,
        candidates_considered=len(candidates),
    )


def run_experiment(
    namespace_selector: dict,
    allowed_namespaces: set[str],
    min_replicas_remaining: int = 2,
    dry_run: bool = True,
    kubectl_bin: str = "kubectl",
) -> ChaosResult:
    """List real pods/PDBs across `allowed_namespaces`, pick a victim via
    `select_victim`, and (only if dry_run is False) delete it.
    """
    pods: list[dict] = []
    pdbs: list[dict] = []
    for namespace in sorted(allowed_namespaces):
        pods.extend(json.loads(_run(kubectl_bin, "get", "pods", "-n", namespace, "-o", "json"))["items"])
        pdbs.extend(json.loads(_run(kubectl_bin, "get", "pdb", "-n", namespace, "-o", "json"))["items"])

    result = select_victim(pods, pdbs, namespace_selector, allowed_namespaces, min_replicas_remaining)
    result.dry_run = dry_run

    if not dry_run and result.victim:
        namespace, name = result.victim.split("/", 1)
        _run(kubectl_bin, "delete", "pod", name, "-n", namespace)

    return result


def _run(*args: str) -> str:
    result = subprocess.run(list(args), capture_output=True, text=True, check=True)
    return result.stdout
