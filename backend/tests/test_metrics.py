from datetime import datetime, timedelta, timezone

import pytest

from app.metrics import compute_points, downsample, window_top_queries, with_pct
from app.schemas import MetricSample, TopQuery

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def s(sec, **kw):
    return MetricSample(ts=T0 + timedelta(seconds=sec), **kw)


def q(qid, calls, total, **kw):
    return TopQuery(query_id=qid, query=f"select {qid}", calls=calls, total_time_ms=total, **kw)


def test_rates_basic():
    pts = compute_points([s(0, commits=100, sessions_active=3), s(2, commits=110),
                          s(4, commits=130)])
    assert pts[0].commits_per_sec is None
    assert pts[0].sessions_active == 3
    assert pts[1].commits_per_sec == pytest.approx(5.0)
    assert pts[2].commits_per_sec == pytest.approx(10.0)


def test_rates_counter_reset_is_zero():
    pts = compute_points([s(0, logical_reads=1000), s(1, logical_reads=10)])
    assert pts[1].logical_reads_per_sec == 0.0


def test_rates_none_safe():
    pts = compute_points([s(0, commits=None, rollbacks=1), s(1, commits=5, rollbacks=None),
                          s(2, commits=7, rollbacks=3)])
    assert pts[1].commits_per_sec is None and pts[1].rollbacks_per_sec is None
    assert pts[2].commits_per_sec == 2.0 and pts[2].rollbacks_per_sec is None


def test_rates_same_timestamp_is_none():
    pts = compute_points([s(0, commits=1), s(0, commits=5)])
    assert pts[1].commits_per_sec is None


def test_empty_inputs():
    assert compute_points([]) == []
    assert window_top_queries([]) == []
    assert with_pct([]) == []


def test_downsample_keeps_last_and_bounds():
    samples = [s(i, commits=i) for i in range(1000)]
    out = downsample(samples, 100)
    assert len(out) <= 100 and out[0] is samples[0] and out[-1] is samples[-1]
    assert downsample(samples[:5], 100) == samples[:5]
    # rates over the downsampled series stay correct (1 commit/s)
    assert compute_points(out)[1].commits_per_sec == pytest.approx(1.0)


def test_window_top_queries_deltas():
    t1, t2, t3 = T0, T0 + timedelta(seconds=5), T0 + timedelta(seconds=10)
    snaps = [
        (t1, q("a", 10, 100.0, disk_reads=10, buffer_gets=90)),
        (t1, q("b", 5, 500.0)),
        (t2, q("a", 15, 150.0)),
        (t2, q("c", 1, 10.0)),          # first seen in the middle snapshot
        (t3, q("a", 20, 400.0, disk_reads=20, buffer_gets=180, max_time_ms=99.0)),
        (t3, q("b", 5, 500.0)),         # no activity → dropped
        (t3, q("c", 3, 40.0)),
        (t3, q("d", 2, 60.0)),          # only in latest → counts fully
    ]
    res = window_top_queries(snaps)
    assert [r.query_id for r in res] == ["a", "d", "c"]
    a, d, c = res
    assert (a.calls, a.total_time_ms, a.mean_time_ms) == (10, 300.0, 30.0)
    assert a.disk_reads == 10 and a.buffer_gets == 90 and a.cache_hit_ratio == 90.0
    assert a.max_time_ms == 99.0
    assert (c.calls, c.total_time_ms) == (2, 30.0)
    assert (d.calls, d.total_time_ms, d.mean_time_ms) == (2, 60.0, 30.0)
    assert sum(r.pct_total_time for r in res) == pytest.approx(100.0, abs=0.05)
    assert a.pct_total_time == pytest.approx(76.92, abs=0.01)


def test_window_top_queries_reset_and_limit():
    t1, t2 = T0, T0 + timedelta(seconds=5)
    snaps = [(t1, q("a", 100, 1000.0)), (t2, q("a", 4, 40.0)),
             (t1, q("b", 1, 1.0)), (t2, q("b", 2, 900.0))]
    res = window_top_queries(snaps, limit=1)
    assert len(res) == 1 and res[0].query_id == "b" and res[0].total_time_ms == 899.0
    assert res[0].pct_total_time == 100.0
    full = {r.query_id: r for r in window_top_queries(snaps)}
    assert full["a"].calls == 4 and full["a"].total_time_ms == 40.0  # reset → latest fully


def test_window_single_snapshot_counts_fully():
    res = window_top_queries([(T0, q("a", 3, 30.0, rows=None))])
    assert res[0].calls == 3 and res[0].rows is None and res[0].cache_hit_ratio is None


def test_with_pct_zero_total():
    res = with_pct([q("a", 0, 0.0)])
    assert res[0].pct_total_time == 0.0
