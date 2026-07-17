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


def test_zero_desired_replicas_is_healthy():
    # A deployment intentionally scaled to zero (e.g. a suspended
    # CronJob-backed workload) is healthy at 0/0, not "insufficient
    # replicas" -- naive `available < desired` logic happens to get
    # this right (0 < 0 is False), but it's worth asserting explicitly
    # since it's the one case where "desired" being falsy could tempt a
    # different implementation into a wrong special case.
    status = evaluate_rollout(_deployment(0, 0, 0))
    assert status.healthy is True


def test_more_available_than_updated_still_reports_rollout_in_progress():
    # 5 desired, all 5 available (old pods still serving traffic during
    # rollout), but only 2 have actually rolled to the new revision --
    # this must not be reported healthy just because nothing is down.
    status = evaluate_rollout(_deployment(5, 5, 2))
    assert status.healthy is False
    assert "2/5" in status.reason


def test_available_exceeds_desired_during_scale_down_is_still_healthy_once_settled():
    # Transient over-availability during a scale-down (old ReplicaSet
    # still terminating) shouldn't be flagged unhealthy once updated
    # also matches desired -- available >= desired is the actual bar,
    # not available == desired.
    status = evaluate_rollout(_deployment(3, 4, 3))
    assert status.healthy is True
