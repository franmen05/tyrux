"""Pure functions turning raw stored data into API series."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.schemas import MetricPoint, MetricSample, TopQuery

COUNTERS = ["commits", "rollbacks", "physical_reads", "logical_reads", "deadlocks"]
GAUGES = [
    "sessions_active", "sessions_idle", "sessions_total", "sessions_blocked",
    "cache_hit_ratio", "size_bytes", "longest_query_seconds",
]
_DELTA_FIELDS = ["calls", "total_time_ms", "rows", "disk_reads", "buffer_gets"]


def _rate(prev: Optional[float], cur: Optional[float], seconds: float) -> Optional[float]:
    if prev is None or cur is None or seconds <= 0:
        return None
    delta = cur - prev
    return 0.0 if delta < 0 else delta / seconds  # negative delta = counter reset


def downsample(samples: list[MetricSample], max_points: int) -> list[MetricSample]:
    """Keep at most max_points samples by striding (always keeps the last one).
    Rates computed afterwards become averages over the wider interval."""
    if max_points < 2 or len(samples) <= max_points:
        return samples
    step = -(-len(samples) // max_points)  # ceil
    picked = samples[::step]
    if picked[-1] is not samples[-1]:
        picked[-1] = samples[-1]
    return picked


def compute_points(samples: list[MetricSample]) -> list[MetricPoint]:
    """One point per sample; counter rates are per second against the previous sample
    (None for the first sample or when either value is missing)."""
    points: list[MetricPoint] = []
    prev: Optional[MetricSample] = None
    for s in samples:
        data = {g: getattr(s, g) for g in GAUGES}
        seconds = (s.ts - prev.ts).total_seconds() if prev else 0.0
        for c in COUNTERS:
            data[f"{c}_per_sec"] = _rate(getattr(prev, c) if prev else None, getattr(s, c), seconds)
        points.append(MetricPoint(ts=s.ts, **data))
        prev = s
    return points


def with_pct(queries: list[TopQuery]) -> list[TopQuery]:
    """Fill pct_total_time (share of total time across the list)."""
    total = sum(q.total_time_ms for q in queries)
    for q in queries:
        q.pct_total_time = round(q.total_time_ms * 100.0 / total, 2) if total > 0 else 0.0
    return queries


def _delta(latest: Optional[float], first: Optional[float]) -> Optional[float]:
    if latest is None:
        return None
    if first is None or latest < first:  # stats reset → count the latest value fully
        return latest
    return latest - first


def window_top_queries(snapshots: list[tuple[datetime, TopQuery]],
                       limit: Optional[int] = None) -> list[TopQuery]:
    """Activity inside a window from cumulative snapshots.

    For each query in the latest snapshot: latest minus its earliest snapshot in the window.
    Queries that only appear in the latest snapshot count fully. Queries with no activity
    in the window are dropped. Sorted by total time desc.
    """
    if not snapshots:
        return []
    by_ts: dict[datetime, dict[str, TopQuery]] = defaultdict(dict)
    for ts, q in snapshots:
        by_ts[ts][q.query_id] = q
    ordered = sorted(by_ts)
    latest_ts = ordered[-1]

    earliest: dict[str, TopQuery] = {}
    for ts in ordered[:-1]:
        for qid, q in by_ts[ts].items():
            earliest.setdefault(qid, q)

    result: list[TopQuery] = []
    for qid, last in by_ts[latest_ts].items():
        first = earliest.get(qid)
        d = {f: _delta(getattr(last, f), getattr(first, f) if first else None)
             for f in _DELTA_FIELDS}
        calls = int(d["calls"] or 0)
        total = float(d["total_time_ms"] or 0.0)
        if calls == 0 and total == 0:
            continue
        reads, gets = d["disk_reads"], d["buffer_gets"]
        hit = None
        if reads is not None and gets is not None and reads + gets > 0:
            hit = round(gets * 100.0 / (reads + gets), 2)
        result.append(TopQuery(
            query_id=qid, query=last.query, calls=calls, total_time_ms=total,
            mean_time_ms=total / calls if calls else 0.0, max_time_ms=last.max_time_ms,
            rows=_int(d["rows"]), disk_reads=_int(reads), buffer_gets=_int(gets),
            cache_hit_ratio=hit, username=last.username,
        ))
    result.sort(key=lambda q: q.total_time_ms, reverse=True)
    if limit is not None:
        result = result[:limit]
    return with_pct(result)


def _int(v: Optional[float]) -> Optional[int]:
    return None if v is None else int(v)
