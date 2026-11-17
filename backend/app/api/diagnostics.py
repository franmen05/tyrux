from __future__ import annotations

from fastapi import APIRouter, Query

from app import rec_cache
from app.api.common import collect, load_config
from app.collectors.base import OrderBy
from app.metrics import with_pct
from app.schemas import (
    ActiveQuery, ConfigParam, ExplainRequest, ExplainResult, IndexStat, LockInfo, Overview,
    Recommendation, TableStat, TopQuery,
)

router = APIRouter(prefix="/connections/{connection_id}", tags=["diagnostics"])


@router.get("/overview", response_model=Overview)
async def overview(connection_id: int):
    return await collect(await load_config(connection_id), "overview")


@router.get("/active-queries", response_model=list[ActiveQuery])
async def active_queries(connection_id: int):
    return await collect(await load_config(connection_id), "active_queries")


@router.get("/top-queries", response_model=list[TopQuery])
async def top_queries(connection_id: int, order_by: OrderBy = "total_time",
                      limit: int = Query(50, ge=1, le=500)):
    queries = await collect(await load_config(connection_id), "top_queries",
                            order_by=order_by, limit=limit)
    if any(q.pct_total_time is None for q in queries):
        with_pct(queries)
    return queries


@router.post("/explain", response_model=ExplainResult)
async def explain(connection_id: int, req: ExplainRequest):
    return await collect(await load_config(connection_id), "explain", req)


@router.get("/tables", response_model=list[TableStat])
async def tables(connection_id: int):
    return await collect(await load_config(connection_id), "tables")


@router.get("/indexes", response_model=list[IndexStat])
async def indexes(connection_id: int):
    return await collect(await load_config(connection_id), "indexes")


@router.get("/locks", response_model=list[LockInfo])
async def locks(connection_id: int):
    return await collect(await load_config(connection_id), "locks")


@router.get("/config", response_model=list[ConfigParam])
async def config(connection_id: int):
    return await collect(await load_config(connection_id), "config")


@router.get("/recommendations", response_model=list[Recommendation])
async def recommendations(connection_id: int, refresh: bool = False):
    """Full list (unpaginated, kept for compatibility). The Optimización page uses
    /recommendations/page; both share the per-connection cache of app.rec_cache."""
    snap = await rec_cache.get_snapshot(await load_config(connection_id), refresh=refresh)
    return snap.recs
