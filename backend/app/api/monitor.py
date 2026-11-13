from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response

from app import store
from app.api.common import ensure_exists
from app.metrics import compute_points, downsample, window_top_queries
from app.sampler import manager
from app.schemas import MetricSeries, MonitorStart, MonitorStatus, TopQuery

router = APIRouter(prefix="/connections/{connection_id}", tags=["monitor"])


def _window(minutes: float, start: Optional[datetime],
            end: Optional[datetime]) -> tuple[datetime, datetime]:
    def utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    end = utc(end) if end else datetime.now(timezone.utc)
    start = utc(start) if start else end - timedelta(minutes=minutes)
    if start > end:
        raise HTTPException(422, "'from' debe ser anterior a 'to'")
    return start, end


@router.get("/monitor", response_model=MonitorStatus)
async def monitor_status(connection_id: int):
    await ensure_exists(connection_id)
    return await manager.status(connection_id)


@router.post("/monitor", response_model=MonitorStatus)
async def monitor_start(connection_id: int, req: MonitorStart):
    await ensure_exists(connection_id)
    return await manager.start(connection_id, req.interval_seconds, req.duration_minutes)


@router.delete("/monitor", response_model=MonitorStatus)
async def monitor_stop(connection_id: int):
    await ensure_exists(connection_id)
    return await manager.stop(connection_id)


@router.get("/metrics", response_model=MetricSeries)
async def metrics(connection_id: int, minutes: float = Query(15, gt=0, le=60 * 24 * 31),
                  start: Optional[datetime] = Query(None, alias="from"),
                  end: Optional[datetime] = Query(None, alias="to"),
                  max_points: int = Query(1500, ge=10, le=100_000)):
    await ensure_exists(connection_id)
    start, end = _window(minutes, start, end)
    samples = await asyncio.to_thread(store.get_samples, connection_id, start, end, True)
    in_window = [s for s in samples if store.to_epoch(s.ts) >= start.timestamp()]
    if len(in_window) > max_points:
        samples = downsample(in_window, max_points)
    points = [p for p in compute_points(samples) if p.ts.timestamp() >= start.timestamp()]
    session = await asyncio.to_thread(store.get_session, connection_id)
    return MetricSeries(connection_id=connection_id,
                        interval_seconds=session.interval_seconds if session else None,
                        points=points)


@router.get("/metrics/top-queries", response_model=list[TopQuery])
async def metrics_top_queries(connection_id: int, minutes: float = Query(15, gt=0),
                              start: Optional[datetime] = Query(None, alias="from"),
                              end: Optional[datetime] = Query(None, alias="to"),
                              limit: int = Query(25, ge=1, le=500)):
    await ensure_exists(connection_id)
    start, end = _window(minutes, start, end)
    snaps = await asyncio.to_thread(store.get_snapshots, connection_id, start, end)
    return window_top_queries(snaps, limit=limit)


@router.delete("/metrics", status_code=204)
async def clear_metrics(connection_id: int):
    await ensure_exists(connection_id)
    await asyncio.to_thread(store.clear_history, connection_id)
    return Response(status_code=204)
