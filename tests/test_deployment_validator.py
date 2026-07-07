from devops_toolkit.deployment_validator import evaluate_rollout


def _deployment(desired, available, updated, name="my-app", namespace="production"):
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"replicas": desired},
        "status": {"availableReplicas": available, "updatedReplicas": updated},
    }


def test_healthy_rollout():
    status = evaluate_rollout(_deployment(3, 3, 3))
    assert status.healthy is True
    assert "up to date" in status.reason


def test_rollout_in_progress_is_unhealthy():
    status = evaluate_rollout(_deployment(3, 3, 1))
    assert status.healthy is False
    assert "rollout in progress" in status.reason


def test_insufficient_available_replicas_is_unhealthy():
    status = evaluate_rollout(_deployment(3, 1, 1))
    assert status.healthy is False
    assert "only 1/3" in status.reason


def test_missing_status_fields_default_to_zero():
    deployment = {"metadata": {"name": "x"}, "spec": {"replicas": 2}, "status": {}}
    status = evaluate_rollout(deployment)
    assert status.healthy is False
    assert status.available_replicas == 0
