# python-devops-toolkit

A small, dependency-light Python CLI for the recurring operational
scripting work behind most DevOps roles: HTTP health checks with
retries, Kubernetes rollout validation, and AWS orphaned-resource
discovery — extracted from "automated operational workflows using
Python scripting for cloud resource cleanup, monitoring checks, and
deployment validations" at Adform.

## Why this one is different from the others

Every other repo in this profile is infrastructure config (Terraform,
Helm, Ansible). This one is actual application code — it has a real
test suite (12 unit tests, no live cluster/AWS/network required to run
them) and ships as an installable CLI.

## Install

```bash
pip install -e ".[dev]"       # core + test/lint tooling
pip install -e ".[aws]"       # add boto3 for the cloud-cleanup AWS scan
```

## Usage

```bash
# Health-check a list of endpoints; exits non-zero if any are unhealthy
devops-toolkit health-check https://api.example.com/healthz https://app.example.com/healthz

# Validate that a Kubernetes deployment's rollout is fully healthy
devops-toolkit validate-deployment my-app --namespace production
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
  health_check.py          HTTP checks with retry/backoff, stdlib only
  deployment_validator.py   Kubernetes rollout health evaluation (pure logic + kubectl wrapper)
  cloud_cleanup.py           Orphaned EBS volume discovery (pure logic + boto3 wrapper)
  cli.py                      argparse entrypoint tying it together
tests/
  test_health_check.py, test_deployment_validator.py, test_cloud_cleanup.py
```

## Design notes

- **Pure logic separated from I/O everywhere.** `evaluate_rollout()`,
  `filter_orphaned_volumes()`, and the retry logic in `check_http()` are
  all testable without a live cluster, AWS credentials, or network
  access — the boto3/kubectl/urllib calls are thin wrappers around them.
- **Stdlib-only for the hot path.** `health_check.py` uses
  `urllib.request` instead of `requests` so it runs with zero
  dependencies on a bare box during an incident.
- **Non-zero exit codes everywhere.** Every subcommand is designed to be
  used as a CI/cron gate, not just a human-readable report.

## Related repos

- [`ansible-infra-automation`](https://github.com/jnnishad/ansible-infra-automation) — a custom Ansible module version of the cloud-cleanup logic
- [`gitops-cicd-pipelines`](https://github.com/jnnishad/gitops-cicd-pipelines) — where `validate-deployment` fits into a deploy pipeline

## License

MIT — see [LICENSE](LICENSE).
