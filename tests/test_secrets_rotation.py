import json
from unittest.mock import patch

from devops_toolkit.secrets_rotation import find_dependent_deployments, plan_rotation, rotate_secret


def _deployment(name, namespace="default", secret_refs=(), secret_key_refs=()):
    env_from = [{"secretRef": {"name": s}} for s in secret_refs]
    env = [
        {"name": f"VAR_{s}", "valueFrom": {"secretKeyRef": {"name": s, "key": "value"}}}
        for s in secret_key_refs
    ]
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"template": {"spec": {"containers": [{"envFrom": env_from, "env": env}]}}},
    }


def test_finds_deployments_using_envfrom_secretref():
    deployments = [_deployment("api", secret_refs=["db-creds"])]
    result = find_dependent_deployments("db-creds", deployments)
    assert result == ["default/api"]


def test_finds_deployments_using_secretkeyref():
    deployments = [_deployment("worker", secret_key_refs=["api-token"])]
    result = find_dependent_deployments("api-token", deployments)
    assert result == ["default/worker"]


def test_ignores_deployments_that_dont_reference_secret():
    deployments = [_deployment("api", secret_refs=["other-secret"])]
    result = find_dependent_deployments("db-creds", deployments)
    assert result == []


def test_plan_rotation_with_no_dependents_is_empty():
    plan = plan_rotation("unused-secret", [])
    assert plan.waves == []
    assert plan.total_deployments == 0


def test_plan_rotation_puts_canary_marked_deployment_first():
    deployments = [
        _deployment("api", secret_refs=["db-creds"]),
        _deployment("api-canary", secret_refs=["db-creds"]),
        _deployment("worker", secret_refs=["db-creds"]),
    ]
    plan = plan_rotation("db-creds", deployments)
    assert plan.waves[0].deployments == ["default/api-canary"]
    assert set(plan.waves[1].deployments) == {"default/api", "default/worker"}


def test_plan_rotation_picks_synthetic_canary_when_none_marked():
    deployments = [
        _deployment("api", secret_refs=["db-creds"]),
        _deployment("worker", secret_refs=["db-creds"]),
    ]
    plan = plan_rotation("db-creds", deployments)
    assert plan.total_deployments == 2
    assert len(plan.waves[0].deployments) == 1
    # single dependent overall should not be split into two waves
    single = plan_rotation("db-creds", [_deployment("solo", secret_refs=["db-creds"])])
    assert len(single.waves) == 1
    assert single.waves[0].deployments == ["default/solo"]


def test_rotate_secret_dry_run_only_reads_never_writes():
    empty_deployments = json.dumps({"items": []})
    with patch("devops_toolkit.secrets_rotation._run", return_value=empty_deployments) as mock_run:
        plan = rotate_secret("unused-secret", "default", {"value": "new"}, dry_run=True)

    assert plan.waves == []
    # dry_run must never call kubectl patch/rollout -- only the initial
    # "get deployments" read used to build the plan.
    mock_run.assert_called_once()


def test_rotate_secret_patches_full_key_map_not_a_single_hardcoded_key():
    # Regression test: an earlier version of rotate_secret hardcoded
    # `stringData: {"value": new_value}`, which silently broke for any
    # Secret with more than one key. This confirms an arbitrary
    # {key: value} map round-trips into the kubectl patch payload as-is.
    empty_deployments = json.dumps({"items": []})
    calls = []

    def fake_run(*args):
        calls.append(args)
        return empty_deployments

    with patch("devops_toolkit.secrets_rotation._run", side_effect=fake_run):
        rotate_secret(
            "db-creds",
            "default",
            {"username": "svc-account", "password": "s3cr3t-value"},
            dry_run=False,
        )

    patch_call = next(c for c in calls if "patch" in c)
    payload = json.loads(patch_call[-1])
    assert payload == {
        "stringData": {"username": "svc-account", "password": "s3cr3t-value"}
    }
