from devops_toolkit.k8s_drain import plan_eviction


def _pod(name, namespace="default", labels=None, owner_kind=None, mirror=False):
    metadata = {"name": name, "namespace": namespace, "labels": labels or {}}
    if owner_kind:
        metadata["ownerReferences"] = [{"kind": owner_kind}]
    if mirror:
        metadata["annotations"] = {"kubernetes.io/config.mirror": "true"}
    return {"metadata": metadata}


def _pdb(name, namespace, match_labels, disruptions_allowed):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"selector": {"matchLabels": match_labels}},
        "status": {"disruptionsAllowed": disruptions_allowed},
    }


def test_daemonset_pods_are_skipped_not_evicted():
    pods = [_pod("fluentd-abc", owner_kind="DaemonSet")]
    plan = plan_eviction("node-1", pods, pdbs=[])
    assert plan.skipped_daemonset == ["default/fluentd-abc"]
    assert plan.evictable == []


def test_mirror_pods_are_skipped():
    pods = [_pod("kube-apiserver-node1", namespace="kube-system", mirror=True)]
    plan = plan_eviction("node-1", pods, pdbs=[])
    assert plan.skipped_mirror == ["kube-system/kube-apiserver-node1"]


def test_pod_with_no_matching_pdb_is_evictable():
    pods = [_pod("web-1", labels={"app": "web"})]
    plan = plan_eviction("node-1", pods, pdbs=[])
    assert plan.evictable == ["default/web-1"]
    assert plan.is_fully_drainable is True


def test_pod_blocked_when_pdb_has_zero_disruptions_allowed():
    pods = [_pod("web-1", labels={"app": "web"})]
    pdbs = [_pdb("web-pdb", "default", {"app": "web"}, disruptions_allowed=0)]
    plan = plan_eviction("node-1", pods, pdbs)
    assert plan.evictable == []
    assert "default/web-1" in plan.blocked_by_pdb[0]
    assert "web-pdb" in plan.blocked_by_pdb[0]
    assert plan.is_fully_drainable is False


def test_pod_evictable_when_pdb_still_allows_disruption():
    pods = [_pod("web-1", labels={"app": "web"}), _pod("web-2", labels={"app": "web"})]
    pdbs = [_pdb("web-pdb", "default", {"app": "web"}, disruptions_allowed=1)]
    plan = plan_eviction("node-1", pods, pdbs)
    # Both pods individually check against the *current* PDB snapshot; the
    # PDB status doesn't change until a real eviction happens, so a
    # single-disruption budget still marks both as currently evictable —
    # it's the operator/live drain's job to evict one, re-check, then
    # continue, which is exactly what drain_node() does one pod at a time.
    assert plan.evictable == ["default/web-1", "default/web-2"]


def test_pdb_in_other_namespace_does_not_block():
    pods = [_pod("web-1", namespace="prod", labels={"app": "web"})]
    pdbs = [_pdb("web-pdb", "staging", {"app": "web"}, disruptions_allowed=0)]
    plan = plan_eviction("node-1", pods, pdbs)
    assert plan.evictable == ["prod/web-1"]
