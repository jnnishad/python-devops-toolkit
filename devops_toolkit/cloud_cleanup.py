"""AWS orphaned-resource discovery — the "cloud resource cleanup" half of
the Adform automation work. `filter_orphaned_volumes` is pure and unit
tested without AWS credentials; `find_orphaned_volumes` is the thin
boto3-backed wrapper used for real scans.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass
class OrphanedVolume:
    volume_id: str
    size_gb: int
    availability_zone: str
    age_hours: float


def filter_orphaned_volumes(
    volumes: list[dict],
    min_age_hours: float,
    now: datetime.datetime | None = None,
) -> list[OrphanedVolume]:
    """Given a list of volume dicts (as returned by boto3's
    describe_volumes()['Volumes']), return the ones that are unattached
    (state == 'available') and older than min_age_hours.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    orphaned = []
    for volume in volumes:
        if volume.get("State") != "available":
            continue
        created = volume["CreateTime"]
        age_hours = (now - created).total_seconds() / 3600
        if age_hours >= min_age_hours:
            orphaned.append(
                OrphanedVolume(
                    volume_id=volume["VolumeId"],
                    size_gb=volume.get("Size", 0),
                    availability_zone=volume.get("AvailabilityZone", "unknown"),
                    age_hours=round(age_hours, 1),
                )
            )
    return orphaned


def estimated_monthly_waste_usd(volumes: list[OrphanedVolume], price_per_gb_month: float = 0.08) -> float:
    """Rough gp3-style cost estimate for orphaned volumes, useful for
    prioritizing cleanup by dollar impact rather than just count."""
    total_gb = sum(v.size_gb for v in volumes)
    return round(total_gb * price_per_gb_month, 2)


def find_orphaned_volumes(region: str, min_age_hours: float = 24.0) -> list[OrphanedVolume]:
    """Real AWS scan — requires boto3 and valid credentials."""
    import boto3  # imported lazily so the module loads without boto3 installed

    client = boto3.client("ec2", region_name=region)
    volumes: list[dict] = []
    paginator = client.get_paginator("describe_volumes")
    for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        volumes.extend(page["Volumes"])
    return filter_orphaned_volumes(volumes, min_age_hours)
