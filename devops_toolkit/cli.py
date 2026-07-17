"""Unified CLI: `devops-toolkit <subcommand> ...`"""

from __future__ import annotations

import argparse
import json
import sys

from devops_toolkit.async_prober import CircuitBreaker, probe_many, summarize_outcomes
from devops_toolkit.chaos_pod_kill import run_experiment
from devops_toolkit.cost_anomaly import detect_anomalies, fetch_daily_costs_aws
from devops_toolkit.deployment_validator import validate_live
from devops_toolkit.gitops_drift import check_namespace_drift
from devops_toolkit.health_check import check_many, summarize
from devops_toolkit.k8s_drain import drain_node
from devops_toolkit.secrets_rotation import rotate_secret


def _parse_selector(raw: str) -> dict:
    """Parse `key=value,key2=value2` into a dict, as used by --selector flags."""
    selector = {}
    for pair in raw.split(","):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        selector[key.strip()] = value.strip()
    return selector


def _cmd_health_check(args: argparse.Namespace) -> int:
    results = check_many(args.urls, timeout=args.timeout, retries=args.retries)
    for r in results:
        status = "OK  " if r.healthy else "FAIL"
        detail = f"status={r.status_code}" if r.status_code else f"error={r.error}"
        latency = f"{r.latency_ms}ms" if r.latency_ms is not None else "n/a"
        print(f"[{status}] {r.url}  ({detail}, latency={latency}, attempts={r.attempts})")

    healthy, unhealthy = summarize(results)
    print(f"\n{healthy} healthy, {unhealthy} unhealthy")
    return 1 if unhealthy else 0


def _cmd_validate_deployment(args: argparse.Namespace) -> int:
    status = validate_live(args.name, namespace=args.namespace)
    print(f"{status.namespace}/{status.name}: {status.reason}")
    print(f"  desired={status.desired_replicas} available={status.available_replicas} updated={status.updated_replicas}")
    return 0 if status.healthy else 1


def _cmd_probe(args: argparse.Namespace) -> int:
    breakers = {url: CircuitBreaker(failure_threshold=args.failure_threshold) for url in args.urls}
    outcomes = probe_many(args.urls, breakers=breakers, max_concurrency=args.max_concurrency, retries=args.retries)
    for o in outcomes:
        if o.skipped:
            print(f"[SKIP] {o.url}  (circuit {o.circuit_state.value})")
        else:
            status = "OK  " if o.result.healthy else "FAIL"
            print(f"[{status}] {o.url}  (circuit {o.circuit_state.value}, attempts={o.result.attempts})")
    counts = summarize_outcomes(outcomes)
    print(f"\n{counts['healthy']} healthy, {counts['unhealthy']} unhealthy, {counts['skipped']} skipped")
    return 1 if counts["unhealthy"] or counts["skipped"] else 0


def _cmd_plan_drain(args: argparse.Namespace) -> int:
    plan = drain_node(args.node, dry_run=not args.execute, grace_period_seconds=args.grace_period)
    print(f"Node: {plan.node}  ({'EXECUTED' if args.execute else 'DRY RUN'})")
    print(f"  evictable        : {len(plan.evictable)}")
    for p in plan.evictable:
        print(f"    - {p}")
    print(f"  blocked by PDB   : {len(plan.blocked_by_pdb)}")
    for p in plan.blocked_by_pdb:
        print(f"    - {p}")
    print(f"  skipped (DaemonSet/mirror): {len(plan.skipped_daemonset) + len(plan.skipped_mirror)}")
    return 1 if not plan.is_fully_drainable else 0


def _cmd_cost_anomaly(args: argparse.Namespace) -> int:
    dates, amounts = fetch_daily_costs_aws(days=args.days, profile=args.profile)
    anomalies = detect_anomalies(dates, amounts, threshold=args.threshold)
    if not anomalies:
        print(f"No cost anomalies in the last {args.days} days (checked {len(dates)} data points).")
        return 0
    print(f"{len(anomalies)} cost anomaly day(s) detected:")
    for a in anomalies:
        sign = "+" if a.pct_over_baseline >= 0 else ""
        print(f"  {a.date}: ${a.amount} (baseline ${a.baseline_median}, {sign}{a.pct_over_baseline}%, z={a.modified_z_score})")
    return 1


def _cmd_drift_check(args: argparse.Namespace) -> int:
    manifests = []
    for path in args.manifest_files:
        with open(path) as f:
            manifests.append(json.load(f))

    reports = check_namespace_drift(args.namespace, manifests)
    drifted = [r for r in reports if r.has_drift]
    for r in reports:
        marker = "DRIFTED" if r.has_drift else "clean"
        print(f"[{marker}] {r.kind}/{r.namespace}/{r.name}")
        for d in r.drifted_fields:
            print(f"    {d.path}: desired={d.desired!r} live={d.live!r}")
    print(f"\n{len(drifted)}/{len(reports)} resource(s) drifted")
    return 1 if drifted else 0


def _cmd_rotate_secret(args: argparse.Namespace) -> int:
    # --set KEY=VALUE (repeatable) covers multi-key secrets; --new-value
    # is shorthand for the common single-value case and maps to the
    # conventional "value" key. rotate_secret() itself takes a plain
    # {key: value} map -- see its docstring for why it no longer
    # hardcodes a single key.
    new_values = _parse_selector(",".join(args.key_values)) if args.key_values else {}
    if args.new_value is not None:
        new_values.setdefault("value", args.new_value)
    if not new_values:
        parser_error = "rotate-secret requires --new-value or at least one --set KEY=VALUE"
        raise SystemExit(parser_error)

    plan = rotate_secret(
        args.secret_name,
        args.namespace,
        new_values=new_values,
        aws_secret_id=args.aws_secret_id,
        dry_run=not args.execute,
    )
    print(f"Secret: {plan.secret_name}  ({'EXECUTED' if args.execute else 'DRY RUN'})")
    print(f"  {plan.total_deployments} dependent deployment(s) across {len(plan.waves)} wave(s)")
    for i, wave in enumerate(plan.waves, start=1):
        print(f"  wave {i}: {', '.join(wave.deployments)}")
    return 0


def _cmd_chaos_kill(args: argparse.Namespace) -> int:
    selector = _parse_selector(args.selector)
    result = run_experiment(
        namespace_selector=selector,
        allowed_namespaces=set(args.namespaces),
        min_replicas_remaining=args.min_replicas,
        dry_run=not args.execute,
    )
    mode = "EXECUTED" if args.execute else "DRY RUN"
    if result.victim:
        print(f"[{mode}] victim: {result.victim} ({result.reason})")
    else:
        print(f"[{mode}] no victim selected: {result.reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devops-toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    hc = sub.add_parser("health-check", help="Check HTTP endpoint health (sequential)")
    hc.add_argument("urls", nargs="+", help="One or more URLs to check")
    hc.add_argument("--timeout", type=float, default=5.0)
    hc.add_argument("--retries", type=int, default=2)
    hc.set_defaults(func=_cmd_health_check)

    dv = sub.add_parser("validate-deployment", help="Check whether a Kubernetes rollout is healthy")
    dv.add_argument("name", help="Deployment name")
    dv.add_argument("--namespace", default="default")
    dv.set_defaults(func=_cmd_validate_deployment)

    pr = sub.add_parser("probe", help="Concurrent endpoint probing with per-host circuit breakers")
    pr.add_argument("urls", nargs="+", help="One or more URLs to probe")
    pr.add_argument("--max-concurrency", type=int, default=20)
    pr.add_argument("--failure-threshold", type=int, default=3, help="Consecutive failures before a host's circuit opens")
    pr.add_argument("--retries", type=int, default=1)
    pr.set_defaults(func=_cmd_probe)

    pd = sub.add_parser("plan-drain", help="Compute (and optionally execute) a PDB-safe node drain")
    pd.add_argument("node", help="Node name")
    pd.add_argument("--grace-period", type=int, default=30)
    pd.add_argument("--execute", action="store_true", help="Actually cordon/evict; default is dry-run")
    pd.set_defaults(func=_cmd_plan_drain)

    ca = sub.add_parser("cost-anomaly", help="Detect anomalous days in AWS daily spend (requires boto3 + Cost Explorer access)")
    ca.add_argument("--days", type=int, default=30)
    ca.add_argument("--threshold", type=float, default=3.5, help="Modified z-score threshold")
    ca.add_argument("--profile", default=None, help="AWS profile name")
    ca.set_defaults(func=_cmd_cost_anomaly)

    dc = sub.add_parser("drift-check", help="Diff Git-tracked manifests (as JSON) against the live cluster state")
    dc.add_argument("namespace")
    dc.add_argument("manifest_files", nargs="+", help="Paths to desired-state manifests, as JSON")
    dc.set_defaults(func=_cmd_drift_check)

    rs = sub.add_parser("rotate-secret", help="Plan (and optionally execute) a wave-based secret rotation")
    rs.add_argument("secret_name")
    rs.add_argument("namespace")
    rs.add_argument("--new-value", default=None, help="Shorthand for --set value=<NEW_VALUE>, for single-value secrets")
    rs.add_argument(
        "--set",
        dest="key_values",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set a specific key's new value; repeatable for multi-key secrets, e.g. --set username=svc --set password=hunter2",
    )
    rs.add_argument("--aws-secret-id", default=None, help="Also rotate this value in AWS Secrets Manager")
    rs.add_argument("--execute", action="store_true", help="Actually rotate and restart; default is dry-run")
    rs.set_defaults(func=_cmd_rotate_secret)

    ck = sub.add_parser("chaos-kill", help="Terminate one safely-selected pod as a minimal chaos experiment")
    ck.add_argument("--selector", required=True, help="Label selector, e.g. app=web,tier=frontend")
    ck.add_argument("--namespaces", nargs="+", required=True, help="Explicit namespace allowlist")
    ck.add_argument("--min-replicas", type=int, default=2, help="Minimum replicas that must remain after the kill")
    ck.add_argument("--execute", action="store_true", help="Actually delete the pod; default is dry-run")
    ck.set_defaults(func=_cmd_chaos_kill)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
