# python-devops-toolkit

A dependency-light Python CLI for the recurring operational and
platform-reliability work behind a senior DevOps role: HTTP health
checks, Kubernetes rollout/drain/chaos safety, GitOps drift detection,
AWS cost anomaly detection, and secret-rotation orchestration —
extracted from "automated operational workflows using Python scripting
for cloud resource cleanup, monitoring checks, and deployment
validations" at Adform, plus the reliability tooling that naturally
follows once that baseline is in place.

## Why this one is different from the others

Every other repo in this profile is infrastructure config (Terraform,
Helm, Ansible). This one is actual application code — it has a real
test suite (50+ unit tests, no live cluster/AWS/network required to run
them) and ships as an installable CLI.

## Modules

| Module | What it does | Notable technique |
|---|---|---|
| `health_check.py` | Sequential HTTP checks with retry/backoff | stdlib only |
| `async_prober.py` | Concurrent HTTP probing at scale | asyncio + per-host circuit breaker (closed/open/half-open) |
| `deployment_validator.py` | Is a K8s rollout actually healthy | pure evaluator + kubectl wrapper |
| `k8s_drain.py` | Safe node cordon/drain | PDB-aware eviction planning, DaemonSet/mirror-pod exclusion |
| `gitops_drift.py` | Git-desired vs. live cluster state | normalized recursive diff (strips server-set fields) |
| `cost_anomaly.py` | Flag anomalous cloud spend days | median/MAD modified z-score (robust to the spike itself) |
| `secrets_rotation.py` | Rotate a secret without an incident | dependency-graph discovery + canary-first wave restart plan |
| `chaos_pod_kill.py` | Minimal, safe chaos experiment | PDB + replica-floor + namespace-allowlist gated victim selection |
| `cloud_cleanup.py` | Orphaned EBS volume discovery | pure filter + boto3 wrapper |

## Install

```bash
pip install -e ".[dev]"       # core + test/lint tooling
pip install -e ".[aws]"       # add boto3 for the cloud-cleanup AWS scan
```

## Usage

```bash
# Health-check a list of endpoints; exits non-zero if any are unhealthy
devops-toolkit health-check https://api.example.com/healthz https://app.example.com/healthz

# Same, but concurrent and circuit-breaker-protected — for probing hundreds of endpoints
devops-toolkit probe https://api.example.com/healthz https://app.example.com/healthz --max-concurrency 50

# Validate that a Kubernetes deployment's rollout is fully healthy
devops-toolkit validate-deployment my-app --namespace production

# Compute a PDB-safe drain plan for a node (dry-run by default)
devops-toolkit plan-drain worker-node-3 --grace-period 60

# Flag anomalous days in the last 30 days of AWS spend
devops-toolkit cost-anomaly --days 30 --profile prod

# Diff Git-tracked manifests against what's actually running
devops-toolkit drift-check production deploy-web.json deploy-worker.json

# Plan (dry-run) a canary-first, wave-based secret rotation
devops-toolkit rotate-secret db-creds production --aws-secret-id prod/db-creds

# Same, but for a multi-key credential secret (username + password)
devops-toolkit rotate-secret db-creds production --set username=svc-account --set password=hunter2

# Pick (but don't kill, unless --execute) one safe pod for a chaos experiment
devops-toolkit chaos-kill --selector app=web --namespaces production --min-replicas 3
```

Or use the modules directly:

```python
from devops_toolkit.cloud_cleanup import find_orphaned_volumes, estimated_monthly_waste_usd

orphaned = find_orphaned_volumes(region="eu-central-1", min_age_hours=72)
print(f"${estimated_monthly_waste_usd(orphaned)}/month in orphaned EBS volumes")
```

## Structure

```
devops_toolkit/
  health_check.py           HTTP checks with retry/backoff, stdlib only
  async_prober.py           asyncio concurrent probing + per-host circuit breaker
  deployment_validator.py   Kubernetes rollout health evaluation (pure logic + kubectl wrapper)
  k8s_drain.py               PDB-aware safe node drain planning + execution
  gitops_drift.py            Normalized desired-vs-live manifest diffing
  cost_anomaly.py             Median/MAD-based cloud spend anomaly detection
  secrets_rotation.py         Dependency-graph + wave-based secret rotation
  chaos_pod_kill.py            Guardrailed single-pod chaos experiment
  cloud_cleanup.py              Orphaned EBS volume discovery (pure logic + boto3 wrapper)
  cli.py                         argparse entrypoint tying it all together
tests/
  one test_*.py per module above, pure-logic only — no live cluster/AWS/network required
```

## Design notes

- **Pure logic separated from I/O everywhere.** `evaluate_rollout()`,
  `filter_orphaned_volumes()`, `plan_eviction()`, `diff_manifests()`,
  `detect_anomalies()`, `plan_rotation()`, `select_victim()`, and the
  retry logic in `check_http()` are all testable without a live cluster,
  AWS credentials, or network access — the boto3/kubectl/urllib calls
  are thin wrappers around them.
- **Stdlib-only for the hot path.** `health_check.py` and
  `async_prober.py` use `urllib.request`/`asyncio` instead of `requests`/
  `aiohttp` so they run with zero dependencies on a bare box during an
  incident.
- **Every destructive action defaults to dry-run.** `plan-drain`,
  `rotate-secret`, and `chaos-kill` all require an explicit `--execute`
  flag; without it they only report what *would* happen.
- **Robust statistics over naive ones.** `cost_anomaly.py` uses the
  median/MAD-based modified z-score instead of mean/stddev specifically
  because a single spike shouldn't be able to desensitize the detector
  to itself.
- **Non-zero exit codes everywhere.** Every subcommand is designed to be
  used as a CI/cron gate, not just a human-readable report.

## Related repos

- [`ansible-infra-automation`](https://github.com/jnnishad/ansible-infra-automation) — a custom Ansible module version of the cloud-cleanup logic
- [`gitops-cicd-pipelines`](https://github.com/jnnishad/gitops-cicd-pipelines) — where `validate-deployment` fits into a deploy pipeline

## License

MIT — see [LICENSE](LICENSE).

<!-- JN -->

<!-- JN -->

<!-- JN -->
