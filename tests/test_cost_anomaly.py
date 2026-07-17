from devops_toolkit.cost_anomaly import detect_anomalies


def _dates(n):
    return [f"2026-06-{d:02d}" for d in range(1, n + 1)]


def test_flat_series_has_no_anomalies():
    amounts = [100.0] * 10
    result = detect_anomalies(_dates(10), amounts)
    assert result == []


def test_single_spike_is_flagged():
    amounts = [100.0] * 9 + [500.0]
    result = detect_anomalies(_dates(10), amounts)
    assert len(result) == 1
    assert result[0].date == "2026-06-10"
    assert result[0].amount == 500.0
    assert result[0].pct_over_baseline > 300


def test_gradual_growth_is_not_flagged_as_anomaly():
    # steady 2%/day growth shouldn't trip the detector even though the
    # last day is higher than the first
    amounts = [100.0 * (1.02 ** i) for i in range(10)]
    result = detect_anomalies(_dates(10), amounts)
    assert result == []


def test_short_series_below_min_window_returns_no_anomalies():
    amounts = [100.0, 500.0, 100.0]
    result = detect_anomalies(_dates(3), amounts, min_window=5)
    assert result == []


def test_dip_can_also_be_flagged():
    amounts = [100.0] * 9 + [5.0]
    result = detect_anomalies(_dates(10), amounts)
    assert len(result) == 1
    assert result[0].amount == 5.0
    assert result[0].pct_over_baseline < 0


def test_multiple_spikes_are_all_flagged():
    amounts = [100.0] * 8 + [600.0, 700.0]
    result = detect_anomalies(_dates(10), amounts)
    flagged_dates = {a.date for a in result}
    assert flagged_dates == {"2026-06-09", "2026-06-10"}
