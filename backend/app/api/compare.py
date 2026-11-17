"""Compare the indexes of two saved connections and generate the SQL to copy them (never executed)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.api.common import collect, load_config
from app.index_compare import build_copy_script, compare_indexes, count_by_status
from app.schemas import (
    CompareSide, ConnectionConfig, IndexCompareItem, IndexCompareRequest, IndexCompareResult,
    IndexCopyScriptRequest, ScriptResult,
)

router = APIRouter(prefix="/compare", tags=["compare"])


def _side(cfg: ConnectionConfig) -> CompareSide:
    return CompareSide(id=cfg.id or 0, name=cfg.name, engine=cfg.engine, database=cfg.database)


def _default_schema(cfg: ConnectionConfig) -> str:
    return "public" if cfg.engine == "postgres" else cfg.username.upper()


async def _compare(req: IndexCompareRequest) -> tuple[ConnectionConfig, ConnectionConfig, list[IndexCompareItem]]:
    if req.source_id == req.target_id:
        raise HTTPException(400, "Origen y destino deben ser conexiones distintas")
    src_cfg = await load_config(req.source_id)
    dst_cfg = await load_config(req.target_id)
    src_idx, dst_idx, dst_tables = await asyncio.gather(
        collect(src_cfg, "indexes"), collect(dst_cfg, "indexes"), collect(dst_cfg, "tables"))
    items = compare_indexes(src_idx, dst_idx, source_engine=src_cfg.engine, target_engine=dst_cfg.engine,
                            target_tables=dst_tables, target_default_schema=_default_schema(dst_cfg))
    return src_cfg, dst_cfg, items


@router.post("/indexes", response_model=IndexCompareResult)
async def compare(req: IndexCompareRequest):
    src_cfg, dst_cfg, items = await _compare(req)
    return IndexCompareResult(source=_side(src_cfg), target=_side(dst_cfg), items=items,
                              counts=count_by_status(items))


@router.post("/indexes/script", response_model=ScriptResult)
async def copy_script(req: IndexCopyScriptRequest):
    src_cfg, dst_cfg, items = await _compare(req)
    by_id = {i.id: i for i in items if i.copiable}
    selected = [by_id[i] for i in dict.fromkeys(req.ids) if i in by_id]
    return ScriptResult(engine=dst_cfg.engine, script=build_copy_script(src_cfg, dst_cfg, selected),
                        count=len(selected))
