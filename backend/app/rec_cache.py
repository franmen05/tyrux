"""Short-lived cache of the recommendations of each connection, plus filtering/pagination.

Computing recommendations on a large database is expensive (catalog scans, EXPLAINs...). The
Optimización page pages through the list and generates the script from ids selected on several
pages, so the list is computed once and reused for TTL seconds (or until ``refresh``). Concurrent
requests for the same connection wait for a single computation.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app import store
from app.api.common import collect
from app.schemas import ConnectionConfig, Recommendation, RecommendationPage

CACHE_TTL_S = 300
MAX_ENTRIES = 32
SEVERITY_ORDER = ("high", "medium", "low", "info")


@dataclass
class Snapshot:
    recs: list[Recommendation]
    warnings: list[str]
    generated_at: datetime
    elapsed_ms: float
    created: float   # time.monotonic()


_cache: dict[tuple, Snapshot] = {}
_locks: dict[tuple, asyncio.Lock] = {}


def _key(cfg: ConnectionConfig) -> tuple:
    # db_path: separate stores (tests) never share entries; the config hash invalidates the entry
    # when the connection is edited.
    fp = hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()
    return (store.db_path(), cfg.id, fp)


def clear() -> None:
    _cache.clear()
    _locks.clear()


async def get_snapshot(cfg: ConnectionConfig, refresh: bool = False) -> Snapshot:
    key = _key(cfg)
    asked = time.monotonic()
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        snap = _cache.get(key)
        if snap is not None:
            fresh = time.monotonic() - snap.created < CACHE_TTL_S
            # a refresh that waited for another computation started after it reuses that result
            if (fresh and not refresh) or snap.created >= asked:
                return snap
        t0 = time.perf_counter()
        recs, warnings = await collect(cfg, "recommendations_report")
        snap = Snapshot(recs=list(recs), warnings=list(warnings), generated_at=datetime.now(timezone.utc),
                        elapsed_ms=round((time.perf_counter() - t0) * 1000, 1), created=time.monotonic())
        _cache[key] = snap
        while len(_cache) > MAX_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k].created)
            _cache.pop(oldest, None)
            _locks.pop(oldest, None)
        return snap


def filter_recs(recs: list[Recommendation], category: Optional[str] = None, severity: Optional[str] = None,
                search: Optional[str] = None) -> list[Recommendation]:
    needle = (search or "").strip().lower()
    out = []
    for r in recs:
        if category and r.category != category:
            continue
        if severity and r.severity != severity:
            continue
        if needle and needle not in " ".join(
                (r.title, r.detail, r.object_name or "", r.id)).lower():
            continue
        out.append(r)
    return out


def page(snap: Snapshot, offset: int, limit: int, category: Optional[str] = None,
         severity: Optional[str] = None, search: Optional[str] = None) -> RecommendationPage:
    matching = filter_recs(snap.recs, category, severity, search)
    by_sev: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    for r in snap.recs:
        by_sev[r.severity] = by_sev.get(r.severity, 0) + 1
        by_cat[r.category] = by_cat.get(r.category, 0) + 1
    return RecommendationPage(
        items=matching[offset:offset + limit], total=len(matching),
        selectable_total=sum(1 for r in matching if r.sql.strip()),
        offset=offset, limit=limit, total_all=len(snap.recs),
        by_severity={s: by_sev[s] for s in SEVERITY_ORDER if s in by_sev}, by_category=by_cat,
        warnings=snap.warnings, generated_at=snap.generated_at, elapsed_ms=snap.elapsed_ms,
    )
