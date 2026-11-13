from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Response

from app import store
from app.api.common import collect, load_config
from app.sampler import manager
from app.schemas import ConnectionConfig, ConnectionIn, ConnectionOut, TestResult

router = APIRouter(tags=["connections"])


def _out(conn: ConnectionOut) -> ConnectionOut:
    conn.monitoring = manager.is_running(conn.id)
    return conn


async def _test(cfg: ConnectionConfig) -> TestResult:
    # Testing is expected to fail sometimes: report it in the body instead of a 502.
    try:
        return await collect(cfg, "test_connection")
    except HTTPException as exc:
        return TestResult(ok=False, message=str(exc.detail))


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections():
    return [_out(c) for c in await asyncio.to_thread(store.list_connections)]


@router.post("/connections", response_model=ConnectionOut, status_code=201)
async def create_connection(data: ConnectionIn):
    return _out(await asyncio.to_thread(store.create_connection, data))


@router.post("/connections/test", response_model=TestResult)
async def test_new_connection(data: ConnectionIn):
    return await _test(ConnectionConfig(**data.model_dump()))


@router.put("/connections/{connection_id}", response_model=ConnectionOut)
async def update_connection(connection_id: int, data: ConnectionIn):
    conn = await asyncio.to_thread(store.update_connection, connection_id, data)
    if conn is None:
        raise HTTPException(404, f"Conexión {connection_id} no encontrada")
    await manager.restart(connection_id)  # pick up new settings if monitoring
    return _out(conn)


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: int):
    await manager.stop(connection_id)
    if not await asyncio.to_thread(store.delete_connection, connection_id):
        raise HTTPException(404, f"Conexión {connection_id} no encontrada")
    return Response(status_code=204)


@router.post("/connections/{connection_id}/test", response_model=TestResult)
async def test_connection(connection_id: int):
    return await _test(await load_config(connection_id))
