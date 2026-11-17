"""API contract. Shared by backend modules and mirrored by frontend/src/api/types.ts.

Change shapes only when necessary and keep types.ts in sync.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Engine = Literal["postgres", "oracle"]
Severity = Literal["high", "medium", "low", "info"]
RecCategory = Literal[
    "missing_index", "unused_index", "duplicate_index", "stale_stats",
    "bloat", "config", "extension", "query",
]


# ---------------------------------------------------------------- connections
class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    engine: Engine
    host: str
    port: int = Field(ge=1, le=65535)
    database: str = Field(description="Postgres dbname or Oracle service name")
    username: str
    password: str = Field(default="", description="Write-only. Empty on update = keep current")
    ssl: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class ConnectionOut(BaseModel):
    id: int
    name: str
    engine: Engine
    host: str
    port: int
    database: str
    username: str
    ssl: bool
    options: dict[str, Any]
    created_at: datetime
    monitoring: bool = False


class ConnectionConfig(BaseModel):
    """Internal: decrypted config handed to collectors."""
    id: Optional[int] = None
    name: str = ""
    engine: Engine
    host: str
    port: int
    database: str
    username: str
    password: str
    ssl: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class TestResult(BaseModel):
    ok: bool
    message: str
    version: Optional[str] = None
    latency_ms: Optional[float] = None


# ---------------------------------------------------------------- diagnostics
class Overview(BaseModel):
    engine: Engine
    version: str
    database: str
    uptime_seconds: Optional[float] = None
    size_bytes: Optional[int] = None
    sessions_total: int = 0
    sessions_active: int = 0
    sessions_idle: int = 0
    sessions_blocked: int = 0
    max_connections: Optional[int] = None
    cache_hit_ratio: Optional[float] = Field(None, description="0..100")
    commits_total: Optional[int] = None
    rollbacks_total: Optional[int] = None
    deadlocks_total: Optional[int] = None
    longest_query_seconds: Optional[float] = None
    top_queries_available: bool = True
    warnings: list[str] = Field(default_factory=list)


class ActiveQuery(BaseModel):
    session_id: str          # PG pid / Oracle "sid,serial#"
    username: Optional[str] = None
    application: Optional[str] = None
    client: Optional[str] = None
    state: Optional[str] = None
    duration_seconds: Optional[float] = None
    wait_event: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)
    query_id: Optional[str] = None   # PG queryid / Oracle sql_id
    query: str = ""


class TopQuery(BaseModel):
    query_id: str
    query: str
    calls: int = 0
    total_time_ms: float = 0.0
    mean_time_ms: float = 0.0
    max_time_ms: Optional[float] = None
    rows: Optional[int] = None
    disk_reads: Optional[int] = None      # PG shared_blks_read / Oracle disk_reads
    buffer_gets: Optional[int] = None     # PG shared_blks_hit / Oracle buffer_gets
    cache_hit_ratio: Optional[float] = None  # 0..100
    username: Optional[str] = None
    pct_total_time: Optional[float] = None   # share of total time in the result set, 0..100


class ExplainRequest(BaseModel):
    query_id: Optional[str] = None
    sql: Optional[str] = None
    analyze: bool = False


class Finding(BaseModel):
    severity: Severity
    title: str
    detail: str
    sql: Optional[str] = None   # suggested fix, if any


class ExplainResult(BaseModel):
    query: str
    plan_text: str
    plan_json: Optional[Any] = None
    total_cost: Optional[float] = None
    findings: list[Finding] = Field(default_factory=list)


class TableStat(BaseModel):
    schema_name: str
    table: str
    rows_estimate: Optional[int] = None
    size_bytes: Optional[int] = None
    total_size_bytes: Optional[int] = None   # incl. indexes
    seq_scans: Optional[int] = None          # Oracle: full scans seen in V$SQL_PLAN
    seq_rows_read: Optional[int] = None
    index_scans: Optional[int] = None
    dead_rows: Optional[int] = None
    modifications_since_analyze: Optional[int] = None
    last_analyzed: Optional[datetime] = None
    last_vacuum: Optional[datetime] = None


class IndexStat(BaseModel):
    schema_name: str
    table: str
    index: str
    columns: list[str] = Field(default_factory=list)
    definition: Optional[str] = None
    size_bytes: Optional[int] = None
    scans: Optional[int] = None
    is_unique: bool = False
    is_primary: bool = False
    unused: bool = False
    duplicate_of: Optional[str] = None
    last_used: Optional[datetime] = None


class LockInfo(BaseModel):
    blocked_session: str
    blocked_user: Optional[str] = None
    blocked_query: Optional[str] = None
    blocking_session: str
    blocking_user: Optional[str] = None
    blocking_query: Optional[str] = None
    lock_type: Optional[str] = None
    object_name: Optional[str] = None
    wait_seconds: Optional[float] = None


class ConfigParam(BaseModel):
    name: str
    value: str
    unit: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    recommended: Optional[str] = None


class Recommendation(BaseModel):
    id: str                   # stable id, e.g. "unused_index:public.orders_status_idx"
    category: RecCategory
    severity: Severity
    title: str
    detail: str
    object_name: Optional[str] = None
    sql: str = ""            # ready-to-run SQL (may be several statements)
    estimated_benefit: Optional[str] = None


class RecommendationPage(BaseModel):
    """One page of the (cached) recommendations of a connection, after filters."""
    items: list[Recommendation]
    total: int                      # matching the filters
    selectable_total: int           # matching the filters and with SQL (selectable for the script)
    offset: int
    limit: int
    total_all: int                  # all recommendations, without filters
    by_severity: dict[str, int] = Field(default_factory=dict)   # counts without filters
    by_category: dict[str, int] = Field(default_factory=dict)   # counts without filters
    warnings: list[str] = Field(default_factory=list)  # sections that failed / were truncated
    generated_at: datetime
    elapsed_ms: Optional[float] = None


class ScriptRequest(BaseModel):
    recommendation_ids: list[str] = Field(default_factory=list)
    # "Select all" across pages: every recommendation with SQL matching the filters, minus exclude_ids.
    select_all: bool = False
    exclude_ids: list[str] = Field(default_factory=list)
    category: Optional[RecCategory] = None
    severity: Optional[Severity] = None
    search: Optional[str] = None


class ScriptResult(BaseModel):
    engine: Engine
    script: str
    count: int


# ---------------------------------------------------------------- monitoring
class MetricSample(BaseModel):
    """One sampler tick. Gauges are instantaneous, counters are cumulative raw values."""
    ts: datetime
    # gauges
    sessions_active: int = 0
    sessions_idle: int = 0
    sessions_total: int = 0
    sessions_blocked: int = 0
    cache_hit_ratio: Optional[float] = None
    size_bytes: Optional[int] = None
    longest_query_seconds: Optional[float] = None
    # the statement behind longest_query_seconds at sample time (truncated text)
    longest_query_text: Optional[str] = None
    longest_query_id: Optional[str] = None       # PG queryid (14+) / Oracle sql_id
    longest_query_user: Optional[str] = None
    longest_query_session: Optional[str] = None  # PG pid / Oracle "sid,serial#"
    # counters (cumulative)
    commits: Optional[int] = None
    rollbacks: Optional[int] = None
    physical_reads: Optional[int] = None
    logical_reads: Optional[int] = None
    deadlocks: Optional[int] = None


class MetricPoint(BaseModel):
    ts: datetime
    sessions_active: int
    sessions_idle: int
    sessions_total: int
    sessions_blocked: int
    cache_hit_ratio: Optional[float] = None
    size_bytes: Optional[int] = None
    longest_query_seconds: Optional[float] = None
    commits_per_sec: Optional[float] = None
    rollbacks_per_sec: Optional[float] = None
    physical_reads_per_sec: Optional[float] = None
    logical_reads_per_sec: Optional[float] = None
    deadlocks_per_sec: Optional[float] = None


class LongestQueryAt(BaseModel):
    """The longest running statement captured by the sample closest to a chart point."""
    ts: datetime
    longest_query_seconds: Optional[float] = None
    query: Optional[str] = None
    query_id: Optional[str] = None
    username: Optional[str] = None
    session_id: Optional[str] = None


class MetricSeries(BaseModel):
    connection_id: int
    interval_seconds: Optional[float] = None
    points: list[MetricPoint] = Field(default_factory=list)


class MonitorStart(BaseModel):
    interval_seconds: float = Field(ge=1, le=3600)
    duration_minutes: Optional[float] = Field(default=None, gt=0)


class MonitorStatus(BaseModel):
    connection_id: int
    running: bool
    interval_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    samples: int = 0
    last_sample_at: Optional[datetime] = None
    last_error: Optional[str] = None


# ---------------------------------------------------------------- compare indexes
IndexDiffStatus = Literal["missing", "extra", "same", "different"]


class IndexCompareRequest(BaseModel):
    source_id: int
    target_id: int


class IndexCopyScriptRequest(IndexCompareRequest):
    ids: list[str]


class CompareSide(BaseModel):
    id: int
    name: str
    engine: Engine
    database: str


class IndexCompareItem(BaseModel):
    id: str                          # "src:<schema>.<index>" or "dst:<schema>.<index>" (extra)
    status: IndexDiffStatus
    table: str
    source_schema: Optional[str] = None
    source_index: Optional[str] = None
    source_columns: list[str] = Field(default_factory=list)
    source_unique: Optional[bool] = None
    target_schema: Optional[str] = None
    target_index: Optional[str] = None
    target_columns: list[str] = Field(default_factory=list)
    target_unique: Optional[bool] = None
    is_primary: bool = False
    copiable: bool = False
    note: Optional[str] = None
    sql: Optional[str] = None        # DDL for the target (only when copiable)


class IndexCompareResult(BaseModel):
    source: CompareSide
    target: CompareSide
    items: list[IndexCompareItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
