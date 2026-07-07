"""Unified CLI: `devops-toolkit <subcommand> ...`"""

from __future__ import annotations

import argparse
import sys

from devops_toolkit.health_check import check_many, summarize
from devops_toolkit.deployment_validator import validate_live


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devops-toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    hc = sub.add_parser("health-check", help="Check HTTP endpoint health")
    hc.add_argument("urls", nargs="+", help="One or more URLs to check")
    hc.add_argument("--timeout", type=float, default=5.0)
    hc.add_argument("--retries", type=int, default=2)
    hc.set_defaults(func=_cmd_health_check)

    dv = sub.add_parser("validate-deployment", help="Check whether a Kubernetes rollout is healthy")
    dv.add_argument("name", help="Deployment name")
    dv.add_argument("--namespace", default="default")
    dv.set_defaults(func=_cmd_validate_deployment)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
