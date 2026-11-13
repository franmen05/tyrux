"""SQLite persistence: connections, monitor sessions, metric samples, query snapshots.

Timestamps are stored as UTC epoch seconds (REAL). Every call opens its own sqlite3
connection, so the module is safe to use from any thread.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional

from app.crypto import data_dir, decrypt, encrypt
from app.schemas import ConnectionConfig, ConnectionIn, ConnectionOut, MetricSample, TopQuery

SAMPLE_FIELDS = [
    "sessions_active", "sessions_idle", "sessions_total", "sessions_blocked",
    "cache_hit_ratio", "size_bytes", "longest_query_seconds",
    "commits", "rollbacks", "physical_reads", "logical_reads", "deadlocks",
]
_FLOAT_SAMPLE_FIELDS = {"cache_hit_ratio", "longest_query_seconds"}
SNAPSHOT_FIELDS = [
    "query_id", "query", "calls", "total_time_ms", "rows", "disk_reads", "buffer_gets",
    "max_time_ms", "username",
]

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    engine TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    database TEXT NOT NULL,
    username TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    ssl INTEGER NOT NULL DEFAULT 0,
    options TEXT NOT NULL DEFAULT '{{}}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS monitor_sessions (
    connection_id INTEGER PRIMARY KEY,
    interval_seconds REAL NOT NULL,
    started_at REAL NOT NULL,
    ends_at REAL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS samples (
    connection_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    {", ".join(f"{f} REAL" for f in SAMPLE_FIELDS)}
);
CREATE INDEX IF NOT EXISTS ix_samples ON samples (connection_id, ts);
CREATE TABLE IF NOT EXISTS query_snapshots (
    connection_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    query_id TEXT NOT NULL,
    query TEXT NOT NULL,
    calls INTEGER,
    total_time_ms REAL,
    rows INTEGER,
    disk_reads INTEGER,
    buffer_gets INTEGER,
    max_time_ms REAL,
    username TEXT
);
CREATE INDEX IF NOT EXISTS ix_snapshots ON query_snapshots (connection_id, ts);
"""

_init_lock = threading.Lock()
_initialized: set[str] = set()


def db_path() -> str:
    return str((data_dir() / "dbmon.sqlite").resolve())


def init_db() -> None:
    path = db_path()
    with _init_lock:
        if path in _initialized:
            return
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _initialized.add(path)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    init_db()
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:  # commit on success, rollback on error
            yield conn
    finally:
        conn.close()


def to_epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def from_epoch(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# ---------------------------------------------------------------- connections
def _row_to_out(row: sqlite3.Row) -> ConnectionOut:
    return ConnectionOut(
        id=row["id"], name=row["name"], engine=row["engine"], host=row["host"],
        port=row["port"], database=row["database"], username=row["username"],
        ssl=bool(row["ssl"]), options=json.loads(row["options"]),
        created_at=from_epoch(row["created_at"]),
    )


def create_connection(data: ConnectionIn) -> ConnectionOut:
    with _db() as db:
        cur = db.execute(
            "INSERT INTO connections (name, engine, host, port, database, username, password_enc,"
            " ssl, options, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (data.name, data.engine, data.host, data.port, data.database, data.username,
             encrypt(data.password), int(data.ssl), json.dumps(data.options), time.time()),
        )
        new_id = cur.lastrowid
    return get_connection(new_id)  # type: ignore[return-value]


def list_connections() -> list[ConnectionOut]:
    with _db() as db:
        rows = db.execute("SELECT * FROM connections ORDER BY name COLLATE NOCASE, id").fetchall()
    return [_row_to_out(r) for r in rows]


def get_connection(connection_id: int) -> Optional[ConnectionOut]:
    with _db() as db:
        row = db.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
    return _row_to_out(row) if row else None


def update_connection(connection_id: int, data: ConnectionIn) -> Optional[ConnectionOut]:
    sets = "name=?, engine=?, host=?, port=?, database=?, username=?, ssl=?, options=?"
    params: list = [data.name, data.engine, data.host, data.port, data.database, data.username,
                    int(data.ssl), json.dumps(data.options)]
    if data.password:
        sets += ", password_enc=?"
        params.append(encrypt(data.password))
    with _db() as db:
        cur = db.execute(f"UPDATE connections SET {sets} WHERE id = ?", (*params, connection_id))
        if cur.rowcount == 0:
            return None
    return get_connection(connection_id)


def delete_connection(connection_id: int) -> bool:
    """Delete a connection together with its sessions and history."""
    with _db() as db:
        for table in ("samples", "query_snapshots", "monitor_sessions"):
            db.execute(f"DELETE FROM {table} WHERE connection_id = ?", (connection_id,))
        cur = db.execute("DELETE FROM connections WHERE id = ?", (connection_id,))
        return cur.rowcount > 0


def get_config(connection_id: int) -> Optional[ConnectionConfig]:
    with _db() as db:
        row = db.execute("SELECT * FROM connections WHERE id = ?", (connection_id,)).fetchone()
    if not row:
        return None
    return ConnectionConfig(
        id=row["id"], name=row["name"], engine=row["engine"], host=row["host"],
        port=row["port"], database=row["database"], username=row["username"],
        password=decrypt(row["password_enc"]), ssl=bool(row["ssl"]),
        options=json.loads(row["options"]),
    )


# ---------------------------------------------------------------- monitor sessions
@dataclass
class MonitorSession:
    connection_id: int
    interval_seconds: float
    started_at: datetime
    ends_at: Optional[datetime]
    active: bool


def _row_to_session(row: sqlite3.Row) -> MonitorSession:
    return MonitorSession(
        connection_id=row["connection_id"], interval_seconds=row["interval_seconds"],
        started_at=from_epoch(row["started_at"]),
        ends_at=from_epoch(row["ends_at"]) if row["ends_at"] is not None else None,
        active=bool(row["active"]),
    )


def save_session(connection_id: int, interval_seconds: float, started_at: datetime,
                 ends_at: Optional[datetime]) -> None:
    with _db() as db:
        db.execute(
            "INSERT OR REPLACE INTO monitor_sessions"
            " (connection_id, interval_seconds, started_at, ends_at, active) VALUES (?,?,?,?,1)",
            (connection_id, interval_seconds, to_epoch(started_at),
             to_epoch(ends_at) if ends_at else None),
        )


def end_session(connection_id: int) -> None:
    with _db() as db:
        db.execute("UPDATE monitor_sessions SET active = 0 WHERE connection_id = ?",
                   (connection_id,))


def get_session(connection_id: int) -> Optional[MonitorSession]:
    with _db() as db:
        row = db.execute("SELECT * FROM monitor_sessions WHERE connection_id = ?",
                         (connection_id,)).fetchone()
    return _row_to_session(row) if row else None


def active_sessions() -> list[MonitorSession]:
    with _db() as db:
        rows = db.execute("SELECT * FROM monitor_sessions WHERE active = 1").fetchall()
    return [_row_to_session(r) for r in rows]


# ---------------------------------------------------------------- samples
def insert_sample(connection_id: int, sample: MetricSample) -> None:
    values = [getattr(sample, f) for f in SAMPLE_FIELDS]
    with _db() as db:
        db.execute(
            f"INSERT INTO samples (connection_id, ts, {', '.join(SAMPLE_FIELDS)})"
            f" VALUES (?, ?, {', '.join('?' * len(SAMPLE_FIELDS))})",
            (connection_id, to_epoch(sample.ts), *values),
        )


def _row_to_sample(row: sqlite3.Row) -> MetricSample:
    # REAL column affinity returns floats; restore ints for integer fields
    data = {f: (float(row[f]) if f in _FLOAT_SAMPLE_FIELDS else int(row[f]))
            for f in SAMPLE_FIELDS if row[f] is not None}
    return MetricSample(ts=from_epoch(row["ts"]), **data)


def get_samples(connection_id: int, start: datetime, end: datetime,
                include_previous: bool = False) -> list[MetricSample]:
    """Samples with start <= ts <= end, ascending. include_previous also returns the last
    sample before `start` (useful to compute the first rate of the window)."""
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM samples WHERE connection_id = ? AND ts >= ? AND ts <= ? ORDER BY ts",
            (connection_id, to_epoch(start), to_epoch(end)),
        ).fetchall()
        if include_previous:
            prev = db.execute(
                "SELECT * FROM samples WHERE connection_id = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
                (connection_id, to_epoch(start)),
            ).fetchone()
            if prev:
                rows = [prev, *rows]
    return [_row_to_sample(r) for r in rows]


def count_samples(connection_id: int, since: Optional[datetime] = None) -> int:
    with _db() as db:
        return db.execute(
            "SELECT COUNT(*) FROM samples WHERE connection_id = ? AND ts >= ?",
            (connection_id, to_epoch(since) if since else 0),
        ).fetchone()[0]


def last_sample_at(connection_id: int) -> Optional[datetime]:
    with _db() as db:
        ts = db.execute("SELECT MAX(ts) FROM samples WHERE connection_id = ?",
                        (connection_id,)).fetchone()[0]
    return from_epoch(ts) if ts is not None else None


# ---------------------------------------------------------------- query snapshots
def insert_snapshot(connection_id: int, ts: datetime, queries: list[TopQuery]) -> None:
    if not queries:
        return
    epoch = to_epoch(ts)
    with _db() as db:
        db.executemany(
            f"INSERT INTO query_snapshots (connection_id, ts, {', '.join(SNAPSHOT_FIELDS)})"
            f" VALUES (?, ?, {', '.join('?' * len(SNAPSHOT_FIELDS))})",
            [(connection_id, epoch, *(getattr(q, f) for f in SNAPSHOT_FIELDS)) for q in queries],
        )


def get_snapshots(connection_id: int, start: datetime,
                  end: datetime) -> list[tuple[datetime, TopQuery]]:
    with _db() as db:
        rows = db.execute(
            "SELECT * FROM query_snapshots WHERE connection_id = ? AND ts >= ? AND ts <= ?"
            " ORDER BY ts",
            (connection_id, to_epoch(start), to_epoch(end)),
        ).fetchall()
    result = []
    for r in rows:
        q = TopQuery(**{f: r[f] for f in SNAPSHOT_FIELDS if r[f] is not None})
        result.append((from_epoch(r["ts"]), q))
    return result


# ---------------------------------------------------------------- maintenance
def clear_history(connection_id: int) -> None:
    with _db() as db:
        db.execute("DELETE FROM samples WHERE connection_id = ?", (connection_id,))
        db.execute("DELETE FROM query_snapshots WHERE connection_id = ?", (connection_id,))


def retention_hours() -> float:
    return float(os.environ.get("DBMON_RETENTION_HOURS", "24"))


def prune(hours: Optional[float] = None) -> int:
    """Delete samples and snapshots older than the retention window. Returns rows deleted."""
    cutoff = time.time() - (hours if hours is not None else retention_hours()) * 3600
    with _db() as db:
        n = db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,)).rowcount
        n += db.execute("DELETE FROM query_snapshots WHERE ts < ?", (cutoff,)).rowcount
    return n
