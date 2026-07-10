from devops_toolkit.gitops_drift import diff_manifests, normalize_manifest


def _base_manifest(replicas=3, image="app:v1"):
    return {
        "kind": "Deployment",
        "metadata": {
            "name": "web",
            "namespace": "prod",
            "resourceVersion": "12345",
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "uid": "abc-123",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": "{...}",
                "team": "platform",
            },
        },
        "spec": {"replicas": replicas, "template": {"spec": {"containers": [{"image": image}]}}},
        "status": {"availableReplicas": replicas},
    }


def test_normalize_strips_server_set_fields():
    normalized = normalize_manifest(_base_manifest())
    assert "status" not in normalized
    assert "resourceVersion" not in normalized["metadata"]
    assert "creationTimestamp" not in normalized["metadata"]
    assert "uid" not in normalized["metadata"]


def test_normalize_strips_only_server_set_annotations():
    normalized = normalize_manifest(_base_manifest())
    assert "kubectl.kubernetes.io/last-applied-configuration" not in normalized["metadata"]["annotations"]
    assert normalized["metadata"]["annotations"]["team"] == "platform"


def test_identical_manifests_have_no_drift():
    desired = _base_manifest()
    live = _base_manifest()
    report = diff_manifests(desired, live)
    assert report.has_drift is False


def test_manual_replica_edit_is_detected_as_drift():
    desired = _base_manifest(replicas=3)
    live = _base_manifest(replicas=5)  # someone ran `kubectl scale`
    report = diff_manifests(desired, live)
    assert report.has_drift is True
    paths = {d.path for d in report.drifted_fields}
    assert "spec.replicas" in paths


def test_image_drift_is_detected():
    # container specs are compared as a whole list (order matters for
    # containers, so we don't recurse into list elements) — drift shows
    # up as a change on the `containers` field itself.
    desired = _base_manifest(image="app:v1")
    live = _base_manifest(image="app:v1-hotfix")
    report = diff_manifests(desired, live)
    paths = {d.path for d in report.drifted_fields}
    assert any("containers" in p for p in paths)


def test_server_managed_fields_never_produce_drift():
    desired = _base_manifest()
    live = _base_manifest()
    live["metadata"]["resourceVersion"] = "99999"
    live["status"]["availableReplicas"] = 999
    report = diff_manifests(desired, live)
    assert report.has_drift is False


def test_report_identifies_kind_name_namespace():
    report = diff_manifests(_base_manifest(), _base_manifest())
    assert report.kind == "Deployment"
    assert report.name == "web"
    assert report.namespace == "prod"
