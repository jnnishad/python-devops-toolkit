"""Cloud cost anomaly detection using a robust (median-based) statistic.

A plain mean/stddev z-score is a bad fit for cloud spend: a single
one-off spike (a forgotten load test, a stuck autoscaler) drags the mean
and stddev up with it, which can mask the very anomaly you're looking for
and desensitizes the detector to the next one. This uses the median and
median absolute deviation (MAD) instead, which are far less sensitive to
outliers in the training window, following the same approach recommended
by Iglewicz & Hoaglin's modified z-score.

`detect_anomalies` is pure and unit tested against synthetic series.
`fetch_daily_costs_aws` is the thin AWS Cost Explorer wrapper used for a
real account (lazy boto3 import, same pattern as cloud_cleanup.py).
"""

from __future__ import annotations

import datetime
import statistics
from dataclasses import dataclass


@dataclass
class CostAnomaly:
    date: str
    amount: float
    baseline_median: float
    modified_z_score: float
    pct_over_baseline: float


def _modified_z_scores(values: list[float]) -> list[float]:
    """Iglewicz & Hoaglin modified z-score: 0.6745 * (x - median) / MAD.

    0.6745 makes the MAD comparable in scale to a standard deviation
    under a normal distribution, so the usual "> 3.5 is an outlier"
    threshold still applies.
    """
    median = statistics.median(values)
    abs_deviations = [abs(v - median) for v in values]
    mad = statistics.median(abs_deviations)
    if mad == 0:
        # Degenerate case: every value in the window is identical (or the
        # window is one point). Fall back to raw deviation from median so
        # a real spike still registers instead of dividing by zero.
        return [0.0 if v == median else float("inf") for v in values]
    return [0.6745 * (v - median) / mad for v in values]


def detect_anomalies(
    dates: list[str],
    amounts: list[float],
    threshold: float = 3.5,
    min_window: int = 5,
) -> list[CostAnomaly]:
    """Flag days whose spend is an outlier relative to the whole series.

    Uses a modified z-score built from the median and MAD of `amounts`,
    which stays stable even when the series itself contains a spike
    (unlike mean/stddev). Requires at least `min_window` points to avoid
    flagging noise in short series.
    """
    if len(amounts) < min_window:
        return []

    scores = _modified_z_scores(amounts)
    median = statistics.median(amounts)

    anomalies = []
    for date, amount, score in zip(dates, amounts, scores):
        if score >= threshold:
            pct_over = ((amount - median) / median * 100) if median else float("inf")
            anomalies.append(
                CostAnomaly(
                    date=date,
                    amount=round(amount, 2),
                    baseline_median=round(median, 2),
                    modified_z_score=round(score, 2),
                    pct_over_baseline=round(pct_over, 1),
                )
            )
    return anomalies


def fetch_daily_costs_aws(
    days: int = 30,
    group_by_service: bool = False,
    profile: str | None = None,
) -> tuple[list[str], list[float]]:
    """Pull daily unblended cost totals from AWS Cost Explorer for the
    trailing `days` days. Requires boto3 and Cost Explorer access;
    imported lazily so the module loads without boto3 installed.
    """
    import boto3  # local import by design, see module docstring

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("ce")

    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)

    response = client.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
    )

    dates: list[str] = []
    amounts: list[float] = []
    for period in response["ResultsByTime"]:
        dates.append(period["TimePeriod"]["Start"])
        amounts.append(float(period["Total"]["UnblendedCost"]["Amount"]))

    return dates, amounts
