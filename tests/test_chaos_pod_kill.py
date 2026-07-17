import random

from devops_toolkit.chaos_pod_kill import select_victim


def _pod(name, namespace="prod", labels=None):
    return {"metadata": {"name": name, "namespace": namespace, "labels": labels or {"app": "web"}}}


def _pdb(namespace, match_labels, disruptions_allowed):
    return {
        "metadata": {"namespace": namespace},
        "spec": {"selector": {"matchLabels": match_labels}},
        "status": {"disruptionsAllowed": disruptions_allowed},
    }


def test_refuses_to_touch_namespace_not_in_allowlist():
    pods = [_pod("web-1"), _pod("web-2"), _pod("web-3")]
    result = select_victim(pods, [], {"app": "web"}, allowed_namespaces={"staging"})
    assert result.victim is None


def test_refuses_when_below_replica_floor():
    pods = [_pod("web-1"), _pod("web-2")]  # exactly at the floor of 2
    result = select_victim(pods, [], {"app": "web"}, allowed_namespaces={"prod"}, min_replicas_remaining=2)
    assert result.victim is None
    assert "replica floor" in result.reason


def test_picks_a_victim_when_safe():
    pods = [_pod("web-1"), _pod("web-2"), _pod("web-3")]
    result = select_victim(
        pods, [], {"app": "web"}, allowed_namespaces={"prod"},
        min_replicas_remaining=2, rng=random.Random(42),
    )
    assert result.victim in {"prod/web-1", "prod/web-2", "prod/web-3"}
    assert result.candidates_considered == 3


def test_refuses_when_pdb_would_be_violated():
    pods = [_pod("web-1"), _pod("web-2"), _pod("web-3")]
    pdbs = [_pdb("prod", {"app": "web"}, disruptions_allowed=0)]
    result = select_victim(pods, pdbs, {"app": "web"}, allowed_namespaces={"prod"}, min_replicas_remaining=2)
    assert result.victim is None


def test_ignores_pods_not_matching_selector():
    pods = [_pod("web-1"), _pod("db-1", labels={"app": "db"})]
    result = select_victim(pods, [], {"app": "web"}, allowed_namespaces={"prod"}, min_replicas_remaining=0)
    assert result.victim == "prod/web-1"


def test_result_is_deterministic_with_seeded_rng():
    pods = [_pod("web-1"), _pod("web-2"), _pod("web-3"), _pod("web-4")]
    r1 = select_victim(pods, [], {"app": "web"}, {"prod"}, min_replicas_remaining=2, rng=random.Random(7))
    r2 = select_victim(pods, [], {"app": "web"}, {"prod"}, min_replicas_remaining=2, rng=random.Random(7))
    assert r1.victim == r2.victim
