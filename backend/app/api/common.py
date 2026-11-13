"""Shared helpers for API routers."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException

from app import collectors, store
from app.collectors.base import CollectorError
from app.schemas import ConnectionConfig

log = logging.getLogger("dbmon.api")


async def load_config(connection_id: int) -> ConnectionConfig:
    cfg = await asyncio.to_thread(store.get_config, connection_id)
    if cfg is None:
        raise HTTPException(404, f"Conexión {connection_id} no encontrada")
    return cfg


async def ensure_exists(connection_id: int) -> None:
    if await asyncio.to_thread(store.get_connection, connection_id) is None:
        raise HTTPException(404, f"Conexión {connection_id} no encontrada")


def _call(cfg: ConnectionConfig, method: str, args: tuple, kwargs: dict) -> Any:
    collector = collectors.get_collector(cfg)
    try:
        return getattr(collector, method)(*args, **kwargs)
    finally:
        collector.close()


async def collect(cfg: ConnectionConfig, method: str, *args: Any, **kwargs: Any) -> Any:
    """Run a collector method in a worker thread; failures become HTTP 502."""
    try:
        return await asyncio.to_thread(_call, cfg, method, args, kwargs)
    except CollectorError as exc:
        raise HTTPException(502, str(exc) or "Error del colector") from exc
    except Exception as exc:  # driver errors (psycopg, oracledb, network...)
        log.exception("collector %s failed", method)
        raise HTTPException(502, str(exc) or type(exc).__name__) from exc
