import datetime

from devops_toolkit.cloud_cleanup import filter_orphaned_volumes, estimated_monthly_waste_usd


def _volume(volume_id, size, hours_old, state="available"):
    now = datetime.datetime(2026, 7, 7, tzinfo=datetime.timezone.utc)
    created = now - datetime.timedelta(hours=hours_old)
    return {
        "VolumeId": volume_id,
        "Size": size,
        "State": state,
        "AvailabilityZone": "eu-central-1a",
        "CreateTime": created,
    }, now


def test_filters_only_old_available_volumes():
    v1, now = _volume("vol-old", 100, hours_old=48)
    v2, _ = _volume("vol-new", 50, hours_old=1)
    v3, _ = _volume("vol-attached", 200, hours_old=100, state="in-use")

    orphaned = filter_orphaned_volumes([v1, v2, v3], min_age_hours=24, now=now)

    assert len(orphaned) == 1
    assert orphaned[0].volume_id == "vol-old"
    assert orphaned[0].age_hours == 48.0


def test_estimated_monthly_waste():
    v1, now = _volume("vol-a", 100, hours_old=48)
    v2, _ = _volume("vol-b", 50, hours_old=48)
    orphaned = filter_orphaned_volumes([v1, v2], min_age_hours=24, now=now)

    waste = estimated_monthly_waste_usd(orphaned, price_per_gb_month=0.10)
    assert waste == 15.0  # (100 + 50) * 0.10
