from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.api.common import collect, load_config
from app.schemas import ConnectionConfig, Recommendation, ScriptRequest, ScriptResult

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


@router.post("/optimize/script", response_model=ScriptResult)
async def optimize_script(connection_id: int, req: ScriptRequest):
    cfg = await load_config(connection_id)
    recs: list[Recommendation] = await collect(cfg, "recommendations")
    by_id = {r.id: r for r in recs}
    selected = [by_id[i] for i in dict.fromkeys(req.recommendation_ids) if i in by_id]
    return ScriptResult(engine=cfg.engine, script=build_script(cfg, selected), count=len(selected))
