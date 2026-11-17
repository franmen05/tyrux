from __future__ import annotations

from datetime import datetime, timezone

from typing import Optional

from fastapi import APIRouter, Query

from app import rec_cache
from app.api.common import load_config
from app.schemas import (
    ConnectionConfig, RecCategory, Recommendation, RecommendationPage, ScriptRequest, ScriptResult, Severity,
)

router = APIRouter(prefix="/connections/{connection_id}", tags=["optimize"])

_ENGINE_NAMES = {"postgres": "PostgreSQL", "oracle": "Oracle"}
_RULE = "-- " + "=" * 76


def _comment(text: str) -> list[str]:
    return [f"-- {line}".rstrip() for line in text.splitlines() or [""]]


def build_script(cfg: ConnectionConfig, recs: list[Recommendation],
                 now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        "-- Script de optimización generado por Tyrux (DB Monitor)",
        f"-- Motor: {_ENGINE_NAMES.get(cfg.engine, cfg.engine)}",
        f"-- Conexión: {cfg.name} ({cfg.host}:{cfg.port}/{cfg.database})",
        f"-- Fecha: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"-- Recomendaciones incluidas: {len(recs)}",
        "--",
        "-- Revise antes de ejecutar. Tyrux nunca ejecuta este script.",
    ]
    if cfg.engine == "postgres":
        lines.append("-- CREATE/DROP INDEX CONCURRENTLY no puede ejecutarse dentro de una transacción.")
    for i, rec in enumerate(recs, 1):
        lines += ["", _RULE,
                  f"-- [{i}] {rec.title}",
                  f"-- Categoría: {rec.category} | Severidad: {rec.severity} | Id: {rec.id}"]
        if rec.object_name:
            lines.append(f"-- Objeto: {rec.object_name}")
        lines += _comment(rec.detail)
        if rec.estimated_benefit:
            lines.append(f"-- Beneficio estimado: {rec.estimated_benefit}")
        lines.append(_RULE)
        lines.append(rec.sql.strip() if rec.sql.strip() else "-- (sin SQL asociado)")
    return "\n".join(lines) + "\n"


@router.get("/recommendations/page", response_model=RecommendationPage)
async def recommendations_page(connection_id: int,
                               offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                               category: Optional[RecCategory] = None, severity: Optional[Severity] = None,
                               search: Optional[str] = Query(None, max_length=200),
                               refresh: bool = False):
    """Paginated recommendations. The full list is computed once and cached for a few minutes, so
    paging/filtering does not hit the database again; ``refresh=true`` recomputes it."""
    snap = await rec_cache.get_snapshot(await load_config(connection_id), refresh=refresh)
    return rec_cache.page(snap, offset, limit, category, severity, search)


@router.post("/optimize/script", response_model=ScriptResult)
async def optimize_script(connection_id: int, req: ScriptRequest):
    cfg = await load_config(connection_id)
    # Same (cached) list the page was built from: ids selected on several pages stay valid.
    recs: list[Recommendation] = (await rec_cache.get_snapshot(cfg)).recs
    if req.select_all:
        excluded = set(req.exclude_ids)
        selected = [r for r in rec_cache.filter_recs(recs, req.category, req.severity, req.search)
                    if r.sql.strip() and r.id not in excluded]
    else:
        by_id = {r.id: r for r in recs}
        selected = [by_id[i] for i in dict.fromkeys(req.recommendation_ids) if i in by_id]
    return ScriptResult(engine=cfg.engine, script=build_script(cfg, selected), count=len(selected))
