from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import connections, diagnostics, monitor, optimize
from app.sampler import manager

logging.basicConfig(level=os.environ.get("DBMON_LOG_LEVEL", "INFO"))

DEFAULT_STATIC = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await manager.startup()
    try:
        yield
    finally:
        await manager.shutdown()


app = FastAPI(title="Tyrux · DB Monitor", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (connections, diagnostics, monitor, optimize):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
               include_in_schema=False)
async def api_not_found(rest: str):
    raise HTTPException(404, "Not Found")


static_dir = Path(os.environ.get("DBMON_STATIC_DIR", DEFAULT_STATIC)).resolve()
if static_dir.is_dir():
    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static_dir):
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
