"""PostgreSQL collector (psycopg 3, synchronous). Supports PostgreSQL 12-17.

Read-only on the monitored database: it only runs SELECTs on catalog/statistics views and EXPLAIN.
EXPLAIN ANALYZE is limited to single SELECT statements and runs inside a READ ONLY transaction
that is always rolled back.

Every statement issued by this collector starts with the marker comment ``/* dbmon */`` so it can
be excluded from pg_stat_statements results.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row

from app.collectors.base import Collector, CollectorError, OrderBy
from app.schemas import (
    ActiveQuery, ConfigParam, ExplainRequest, ExplainResult, Finding, IndexStat, LockInfo,
    MetricSample, Overview, Recommendation, TableStat, TestResult, TopQuery,
)

MARK = "/* dbmon */ "
APP_NAME = "dbmon"
DEFAULT_STATEMENT_TIMEOUT_MS = 15000

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

# Thresholds used by plan findings and recommendations.
BIG_TABLE_ROWS = 10_000
BIG_TABLE_BYTES = 10 * 1024 * 1024

# ------------------------------------------------------------------ identifier quoting
_RESERVED = {
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric", "authorization",
    "binary", "both", "case", "cast", "check", "collate", "collation", "column", "concurrently",
    "constraint", "create", "cross", "current_catalog", "current_date", "current_role",
    "current_schema", "current_time", "current_timestamp", "current_user", "default", "deferrable",
    "desc", "distinct", "do", "else", "end", "except", "false", "fetch", "for", "foreign", "freeze",
    "from", "full", "grant", "group", "having", "ilike", "in", "initially", "inner", "intersect",
    "into", "is", "isnull", "join", "lateral", "leading", "left", "like", "limit", "localtime",
    "localtimestamp", "natural", "not", "notnull", "null", "offset", "on", "only", "or", "order",
    "outer", "overlaps", "placing", "primary", "references", "returning", "right", "select",
    "session_user", "similar", "some", "symmetric", "system_user", "table", "tablesample", "then",
    "to", "trailing", "true", "union", "unique", "user", "using", "variadic", "verbose", "when",
    "where", "window", "with",
}
_SIMPLE_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")


def quote_ident(name: str) -> str:
    if _SIMPLE_IDENT.match(name) and name not in _RESERVED:
        return name
    return '"' + name.replace('"', '""') + '"'


def qualified(schema: str, name: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(name)}"


def index_name_for(table: str, cols: list[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", f"{table}_{'_'.join(cols)}".lower()).strip("_")
    return (base[:59] + "_idx") if len(base) > 59 else base + "_idx"


# ------------------------------------------------------------------ SQL text helpers
_STR_LIT = re.compile(r"'(?:[^']|'')*'")
_DOLLAR_LIT = re.compile(r"\$([A-Za-z_]\w*)?\$.*?\$\1\$", re.S)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_PARAM = re.compile(r"\$\d+")
_DML_WORD = re.compile(r"\b(insert|update|delete|merge|truncate|copy|call)\b", re.I)
_EXPLAINABLE = {"select", "with", "values", "table", "insert", "update", "delete", "merge"}


def _strip_noise(sql: str) -> str:
    """Remove comments and literals (keeps structure for keyword/param detection)."""
    s = _BLOCK_COMMENT.sub(" ", sql)
    s = _LINE_COMMENT.sub(" ", s)
    s = _DOLLAR_LIT.sub("''", s)
    s = _STR_LIT.sub("''", s)
    return s


def _clean_statement(sql: str) -> str:
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def _first_keyword(stripped: str) -> str:
    m = re.match(r"[\s(]*([A-Za-z]+)", stripped)
    return m.group(1).lower() if m else ""


def _has_params(sql: str) -> bool:
    return bool(_PARAM.search(_strip_noise(sql)))


def _is_multi_statement(sql: str) -> bool:
    return ";" in _strip_noise(_clean_statement(sql))


# ------------------------------------------------------------------ curated config
_CONFIG_PARAMS: list[tuple[str, Optional[str]]] = [
    ("shared_buffers", "≈25% de la RAM del servidor (el valor por defecto 128MB suele ser bajo)"),
    ("effective_cache_size", "50–75% de la RAM del servidor"),
    ("work_mem", "4–64MB según concurrencia; subir si hay sorts/hash a disco"),
    ("maintenance_work_mem", "256MB–1GB (acelera VACUUM y CREATE INDEX)"),
    ("max_connections", "Mantener moderado (≤ 200–300); usar un pooler como PgBouncer"),
    ("random_page_cost", "1.1 en SSD/NVMe (4 está pensado para discos mecánicos)"),
    ("effective_io_concurrency", "200 en SSD/NVMe"),
    ("default_statistics_target", "100 (subir a 500–1000 en columnas con distribución sesgada)"),
    ("autovacuum", "on"),
    ("autovacuum_max_workers", "3–6 según número de tablas"),
    ("autovacuum_naptime", "1min (por defecto)"),
    ("autovacuum_vacuum_scale_factor", "0.02–0.1 en tablas grandes"),
    ("autovacuum_analyze_scale_factor", "0.01–0.05 en tablas grandes"),
    ("autovacuum_vacuum_cost_limit", "1000–2000 en hardware moderno (-1 usa vacuum_cost_limit)"),
    ("shared_preload_libraries", "Incluir pg_stat_statements"),
    ("track_io_timing", "on (permite ver tiempo de E/S por consulta)"),
    ("track_activity_query_size", "≥ 4096 para ver consultas completas"),
    ("log_min_duration_statement", "p. ej. 1000 ms para registrar consultas lentas"),
    ("idle_in_transaction_session_timeout", "p. ej. 5min para cortar transacciones abandonadas"),
    ("statement_timeout", None),
    ("lock_timeout", None),
    ("checkpoint_timeout", "15min en cargas de escritura"),
    ("max_wal_size", "≥ 2GB en cargas de escritura"),
    ("wal_buffers", "-1 (automático) o 16MB"),
    ("jit", "off suele ser mejor para OLTP con consultas cortas"),
    ("max_parallel_workers_per_gather", None),
    ("pg_stat_statements.track", "top o all"),
    ("pg_stat_statements.max", "≥ 5000"),
    ("compute_query_id", "auto/on (necesario para query_id en pg_stat_activity)"),
]


class PostgresCollector(Collector):
    engine = "postgres"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._conn: Optional[psycopg.Connection] = None
        self._version_num: Optional[int] = None
        self._version_str: str = ""
        self._pgss_cache: Optional[tuple[float, Optional[dict]]] = None
        opts = cfg.options or {}
        self._timeout_ms = int(opts.get("statement_timeout_ms", DEFAULT_STATEMENT_TIMEOUT_MS))

    # ================================================================ connection handling
    def _conninfo(self) -> dict:
        opts = self.cfg.options or {}
        return dict(
            host=self.cfg.host,
            port=self.cfg.port,
            dbname=self.cfg.database,
            user=self.cfg.username,
            password=self.cfg.password,
            connect_timeout=int(opts.get("connect_timeout", 5)),
            sslmode=opts.get("sslmode") or ("require" if self.cfg.ssl else "prefer"),
            application_name=APP_NAME,
        )

    def _connect(self) -> psycopg.Connection:
        try:
            conn = psycopg.connect(**self._conninfo(), autocommit=True, row_factory=dict_row)
        except psycopg.Error as exc:
            raise CollectorError(self._connect_error_message(exc)) from exc
        try:
            conn.execute(f"{MARK}SET statement_timeout = {int(self._timeout_ms)}")
            row = conn.execute(
                f"{MARK}SELECT current_setting('server_version_num')::int AS num, "
                "current_setting('server_version') AS ver"
            ).fetchone()
            self._version_num = row["num"]
            self._version_str = row["ver"].split(" ")[0]
        except psycopg.Error as exc:
            conn.close()
            raise CollectorError(self._error_message(exc)) from exc
        return conn

    def _get_conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed or self._conn.broken:
            self._drop_conn()
            self._conn = self._connect()
        return self._conn

    def _drop_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def close(self) -> None:
        self._drop_conn()

    def _q(self, sql: str, params: Any = None) -> list[dict]:
        """Run one statement, return rows. Reconnects once if the cached connection died."""
        for attempt in (0, 1):
            conn = self._get_conn()
            try:
                cur = conn.execute(MARK + sql, params)
                return cur.fetchall() if cur.description else []
            except psycopg.Error as exc:
                if conn.broken or conn.closed:
                    self._drop_conn()
                    if attempt == 0:
                        continue
                    raise CollectorError(self._connect_error_message(exc)) from exc
                raise CollectorError(self._error_message(exc)) from exc
        raise CollectorError("No se pudo ejecutar la consulta en PostgreSQL")  # pragma: no cover

    def _q1(self, sql: str, params: Any = None) -> dict:
        rows = self._q(sql, params)
        return rows[0] if rows else {}

    @property
    def version_num(self) -> int:
        if self._version_num is None:
            self._get_conn()
        return self._version_num or 0

    # ---------------------------------------------------------------- error messages
    def _connect_error_message(self, exc: Exception) -> str:
        msg = str(exc).strip()
        low = msg.lower()
        where = f"{self.cfg.host}:{self.cfg.port}"
        if "password authentication failed" in low or "authentication failed" in low:
            return (f"Autenticación fallida para el usuario «{self.cfg.username}»: "
                    "usuario o contraseña incorrectos.")
        if "no password supplied" in low:
            return f"El servidor {where} requiere contraseña para «{self.cfg.username}»."
        if "does not exist" in low and "database" in low:
            return f"La base de datos «{self.cfg.database}» no existe en {where}."
        if "does not exist" in low and "role" in low:
            return f"El usuario «{self.cfg.username}» no existe en {where}."
        if "timeout expired" in low or "timed out" in low:
            return f"Tiempo de espera agotado al conectar a {where}. Revise host, puerto y firewall."
        if "connection refused" in low:
            return f"Conexión rechazada en {where}: ¿está PostgreSQL escuchando en ese host y puerto?"
        if "could not translate host name" in low or "nodename nor servname" in low \
                or "name or service not known" in low:
            return f"No se puede resolver el host «{self.cfg.host}»."
        if "ssl" in low and ("not support" in low or "unsupported" in low):
            return f"El servidor {where} no admite SSL; desactive la opción SSL de la conexión."
        if "pg_hba.conf" in low:
            return (f"El servidor {where} rechazó la conexión (pg_hba.conf) para «{self.cfg.username}» "
                    f"en la base «{self.cfg.database}»: {msg}")
        if "too many" in low and "connections" in low:
            return f"El servidor {where} no acepta más conexiones (max_connections alcanzado)."
        return f"No se pudo conectar a PostgreSQL en {where}: {msg}"

    def _error_message(self, exc: Exception) -> str:
        if isinstance(exc, pg_errors.InsufficientPrivilege):
            return (f"Permiso denegado: {self._diag(exc)}. Otorgue el rol de monitorización al usuario: "
                    f"GRANT pg_monitor TO {quote_ident(self.cfg.username)};")
        if isinstance(exc, pg_errors.QueryCanceled):
            return (f"La consulta superó el tiempo máximo de {self._timeout_ms / 1000:g} s "
                    "(statement_timeout) y fue cancelada.")
        if isinstance(exc, pg_errors.ObjectNotInPrerequisiteState) and "pg_stat_statements" in str(exc):
            return ("pg_stat_statements no está cargado: añádalo a shared_preload_libraries "
                    "y reinicie PostgreSQL.")
        if isinstance(exc, psycopg.OperationalError):
            return self._connect_error_message(exc)
        return f"Error de PostgreSQL: {self._diag(exc)}"

    @staticmethod
    def _diag(exc: Exception) -> str:
        diag = getattr(exc, "diag", None)
        primary = getattr(diag, "message_primary", None) if diag else None
        return primary or str(exc).strip().splitlines()[0]

    # ================================================================ pg_stat_statements
    def _pgss(self) -> Optional[dict]:
        """Info about pg_stat_statements in the current DB, or None if unusable. Cached 30 s.

        Returns {"rel": qualified view name, "total": col, "mean": col, "max": col, "error": None}
        or {"error": "<reason>"}.
        """
        now = time.monotonic()
        if self._pgss_cache and now - self._pgss_cache[0] < 30:
            return self._pgss_cache[1]
        info: dict = {"error": None}
        row = self._q1(
            "SELECT n.nspname, e.extversion FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_stat_statements'"
        )
        if not row:
            info["error"] = "missing"
        else:
            rel = qualified(row["nspname"], "pg_stat_statements")
            cols = {r["attname"] for r in self._q(
                "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 "
                "AND NOT attisdropped", (rel,))}
            exec_cols = "total_exec_time" in cols
            info.update(
                rel=rel,
                version=row["extversion"],
                total="total_exec_time" if exec_cols else "total_time",
                mean="mean_exec_time" if exec_cols else "mean_time",
                max="max_exec_time" if exec_cols else "max_time",
                has_toplevel="toplevel" in cols,
            )
            try:
                self._q(f"SELECT 1 FROM {rel} LIMIT 1")
            except CollectorError as exc:
                info["error"] = "not_loaded" if "shared_preload_libraries" in str(exc) else str(exc)
        self._pgss_cache = (now, info)
        return info

    def _pgss_warning(self, info: dict) -> Optional[str]:
        err = info.get("error")
        if not err:
            return None
        if err == "missing":
            return ("La extensión pg_stat_statements no está instalada en esta base de datos: "
                    "no hay estadísticas de consultas (Top consultas). Ejecute "
                    "CREATE EXTENSION pg_stat_statements; (requiere shared_preload_libraries).")
        if err == "not_loaded":
            return ("pg_stat_statements está instalada pero no cargada: añádala a "
                    "shared_preload_libraries y reinicie PostgreSQL.")
        return f"No se pueden leer las estadísticas de pg_stat_statements: {err}"

    # ================================================================ test / overview / sample
    def test_connection(self) -> TestResult:
        t0 = time.perf_counter()
        try:
            self._drop_conn()
            self._get_conn()
            self._q("SELECT 1")
        except CollectorError as exc:
            return TestResult(ok=False, message=str(exc))
        latency = (time.perf_counter() - t0) * 1000
        return TestResult(ok=True, message="Conexión correcta",
                          version=f"PostgreSQL {self._version_str}", latency_ms=round(latency, 1))

    _SAMPLE_SQL = """
        SELECT
          (SELECT count(*) FROM pg_stat_activity a
             WHERE a.datname = current_database() AND a.backend_type = 'client backend'
               AND a.pid <> pg_backend_pid()) AS sessions_total,
          (SELECT count(*) FROM pg_stat_activity a
             WHERE a.datname = current_database() AND a.backend_type = 'client backend'
               AND a.pid <> pg_backend_pid() AND a.state = 'active'
               AND coalesce(a.application_name, '') <> 'dbmon') AS sessions_active,
          (SELECT count(*) FROM pg_stat_activity a
             WHERE a.datname = current_database() AND a.backend_type = 'client backend'
               AND a.pid <> pg_backend_pid() AND a.state LIKE 'idle%%') AS sessions_idle,
          (SELECT count(*) FROM pg_stat_activity a
             WHERE a.datname = current_database() AND a.backend_type = 'client backend'
               AND a.state LIKE 'idle in transaction%%') AS sessions_idle_in_tx,
          (SELECT count(*) FROM pg_stat_activity a
             WHERE a.datname = current_database()
               AND cardinality(pg_blocking_pids(a.pid)) > 0) AS sessions_blocked,
          (SELECT extract(epoch FROM max(now() - a.query_start)) FROM pg_stat_activity a
             WHERE a.datname = current_database() AND a.backend_type = 'client backend'
               AND a.state = 'active' AND a.pid <> pg_backend_pid()
               AND coalesce(a.application_name, '') <> 'dbmon') AS longest_query_seconds,
          d.xact_commit, d.xact_rollback, d.blks_read, d.blks_hit, d.deadlocks, d.stats_reset,
          pg_database_size(current_database()) AS size_bytes,
          extract(epoch FROM now() - pg_postmaster_start_time()) AS uptime_seconds,
          current_setting('max_connections')::int AS max_connections,
          current_database() AS database
        FROM pg_stat_database d WHERE d.datname = current_database()
    """

    @staticmethod
    def _hit_ratio(hit: Optional[int], read: Optional[int]) -> Optional[float]:
        if hit is None or read is None or hit + read == 0:
            return None
        return round(100.0 * hit / (hit + read), 2)

    def sample(self) -> MetricSample:
        r = self._q1(self._SAMPLE_SQL)
        hit, read = r.get("blks_hit"), r.get("blks_read")
        lq = r.get("longest_query_seconds")
        return MetricSample(
            ts=datetime.now(timezone.utc),
            sessions_active=r.get("sessions_active") or 0,
            sessions_idle=r.get("sessions_idle") or 0,
            sessions_total=r.get("sessions_total") or 0,
            sessions_blocked=r.get("sessions_blocked") or 0,
            cache_hit_ratio=self._hit_ratio(hit, read),
            size_bytes=r.get("size_bytes"),
            longest_query_seconds=round(float(lq), 3) if lq is not None else None,
            commits=r.get("xact_commit"),
            rollbacks=r.get("xact_rollback"),
            physical_reads=read,
            logical_reads=(hit + read) if hit is not None and read is not None else None,
            deadlocks=r.get("deadlocks"),
        )

    def overview(self) -> Overview:
        r = self._q1(self._SAMPLE_SQL)
        warnings: list[str] = []
        info = self._pgss()
        w = self._pgss_warning(info)
        if w:
            warnings.append(w)
        priv = self._q1(
            "SELECT r.rolsuper OR pg_has_role(current_user, 'pg_monitor', 'MEMBER') AS ok "
            "FROM pg_roles r WHERE r.rolname = current_user"
        )
        if not priv.get("ok"):
            warnings.append(
                f"El usuario «{self.cfg.username}» no tiene el rol pg_monitor: solo verá el texto de sus "
                f"propias consultas. Ejecute GRANT pg_monitor TO {quote_ident(self.cfg.username)};")
        if r.get("sessions_idle_in_tx"):
            warnings.append(f"{r['sessions_idle_in_tx']} sesión(es) «idle in transaction»: pueden retener "
                            "bloqueos e impedir el VACUUM.")
        if r.get("sessions_blocked"):
            warnings.append(f"{r['sessions_blocked']} sesión(es) bloqueada(s) esperando un lock.")
        lq = r.get("longest_query_seconds")
        return Overview(
            engine="postgres",
            version=f"PostgreSQL {self._version_str}",
            database=r.get("database") or self.cfg.database,
            uptime_seconds=float(r["uptime_seconds"]) if r.get("uptime_seconds") is not None else None,
            size_bytes=r.get("size_bytes"),
            sessions_total=r.get("sessions_total") or 0,
            sessions_active=r.get("sessions_active") or 0,
            sessions_idle=r.get("sessions_idle") or 0,
            sessions_blocked=r.get("sessions_blocked") or 0,
            max_connections=r.get("max_connections"),
            cache_hit_ratio=self._hit_ratio(r.get("blks_hit"), r.get("blks_read")),
            commits_total=r.get("xact_commit"),
            rollbacks_total=r.get("xact_rollback"),
            deadlocks_total=r.get("deadlocks"),
            longest_query_seconds=round(float(lq), 3) if lq is not None else None,
            top_queries_available=not info.get("error"),
            warnings=warnings,
        )

    # ================================================================ active queries
    def active_queries(self) -> list[ActiveQuery]:
        qid = "a.query_id::text" if self.version_num >= 140000 else "NULL::text"
        rows = self._q(f"""
            SELECT a.pid, a.usename, a.application_name,
                   coalesce(host(a.client_addr), a.client_hostname, 'local') AS client,
                   a.state,
                   extract(epoch FROM now() - CASE WHEN a.state = 'active' THEN a.query_start
                                                   ELSE coalesce(a.xact_start, a.state_change) END)
                     AS duration,
                   CASE WHEN a.wait_event IS NOT NULL
                        THEN a.wait_event_type || ':' || a.wait_event END AS wait_event,
                   pg_blocking_pids(a.pid)::text[] AS blocked_by,
                   {qid} AS query_id,
                   coalesce(a.query, '') AS query
            FROM pg_stat_activity a
            WHERE a.datname = current_database()
              AND a.backend_type = 'client backend'
              AND a.pid <> pg_backend_pid()
              AND coalesce(a.application_name, '') <> 'dbmon'
              AND a.state IS DISTINCT FROM 'idle'
            ORDER BY duration DESC NULLS LAST
        """)
        out = []
        for r in rows:
            out.append(ActiveQuery(
                session_id=str(r["pid"]),
                username=r["usename"],
                application=r["application_name"] or None,
                client=r["client"],
                state=r["state"],
                duration_seconds=round(float(r["duration"]), 3) if r["duration"] is not None else None,
                wait_event=r["wait_event"],
                blocked_by=list(r["blocked_by"] or []),
                query_id=r["query_id"] if r["query_id"] not in (None, "0") else None,
                query=r["query"],
            ))
        return out

    # ================================================================ top queries
    def top_queries(self, order_by: OrderBy = "total_time", limit: int = 50) -> list[TopQuery]:
        info = self._pgss()
        if info.get("error"):
            return []
        order_expr = {
            "total_time": f"s.{info['total']}",
            "mean_time": f"s.{info['mean']}",
            "calls": "s.calls",
            "io": "(s.shared_blks_read + s.local_blks_read + s.temp_blks_read + s.temp_blks_written)",
        }.get(order_by)
        if order_expr is None:
            raise CollectorError(f"Orden no soportado: {order_by}")
        limit = max(1, min(int(limit), 1000))
        # With pg_stat_statements.track = all, statements nested in dbmon's own EXPLAINs are
        # recorded as non-toplevel entries sharing the EXPLAIN's queryid: hide them too.
        nested_filter = (
            f"AND NOT (NOT s.toplevel AND s.queryid IN (SELECT d.queryid FROM {info['rel']} d "
            "WHERE d.query LIKE '/* dbmon */%%'))" if info.get("has_toplevel") else "")
        rows = self._q(f"""
            SELECT s.queryid::text AS query_id, s.query, s.calls,
                   s.{info['total']} AS total_ms, s.{info['mean']} AS mean_ms, s.{info['max']} AS max_ms,
                   s.rows, s.shared_blks_read AS disk_reads, s.shared_blks_hit AS buffer_gets,
                   r.rolname AS username
            FROM {info['rel']} s
            LEFT JOIN pg_roles r ON r.oid = s.userid
            WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND s.queryid IS NOT NULL
              AND s.query NOT LIKE '/* dbmon */%%'
              {nested_filter}
            ORDER BY {order_expr} DESC NULLS LAST
            LIMIT %s
        """, (limit,))
        total = sum(float(r["total_ms"] or 0) for r in rows) or 0.0
        out = []
        for r in rows:
            tot = float(r["total_ms"] or 0)
            out.append(TopQuery(
                query_id=r["query_id"],
                query=r["query"] or "",
                calls=int(r["calls"] or 0),
                total_time_ms=round(tot, 3),
                mean_time_ms=round(float(r["mean_ms"] or 0), 3),
                max_time_ms=round(float(r["max_ms"]), 3) if r["max_ms"] is not None else None,
                rows=r["rows"],
                disk_reads=r["disk_reads"],
                buffer_gets=r["buffer_gets"],
                cache_hit_ratio=self._hit_ratio(r["buffer_gets"], r["disk_reads"]),
                username=r["username"],
                pct_total_time=round(100.0 * tot / total, 2) if total else None,
            ))
        return out

    def _query_text(self, query_id: str) -> str:
        if not re.fullmatch(r"-?\d+", query_id.strip()):
            raise CollectorError(f"query_id no válido: {query_id}")
        info = self._pgss()
        if info.get("error"):
            raise CollectorError(self._pgss_warning(info) or "pg_stat_statements no disponible")
        row = self._q1(
            f"SELECT query FROM {info['rel']} WHERE queryid = %s::bigint "
            "AND dbid = (SELECT oid FROM pg_database WHERE datname = current_database()) LIMIT 1",
            (query_id.strip(),))
        if not row:
            raise CollectorError(f"No se encontró la consulta {query_id} en pg_stat_statements "
                                 "(puede haberse reiniciado la estadística).")
        return row["query"]

    # ================================================================ explain
    def explain(self, req: ExplainRequest) -> ExplainResult:
        if req.query_id:
            text = self._query_text(req.query_id)
        elif req.sql and req.sql.strip():
            text = req.sql
        else:
            raise CollectorError("Indique query_id o sql para obtener el plan.")
        sql = _clean_statement(text)
        stripped = _strip_noise(sql)

        def refuse(title: str, detail: str, severity: str = "info") -> ExplainResult:
            return ExplainResult(query=sql, plan_text="",
                                 findings=[Finding(severity=severity, title=title, detail=detail)])

        if _is_multi_statement(sql):
            return refuse("Varias sentencias", "El plan solo puede obtenerse para una única sentencia SQL.")
        kw = _first_keyword(stripped)
        if kw not in _EXPLAINABLE:
            return refuse("Sentencia no explicable",
                          f"EXPLAIN solo admite SELECT, WITH, VALUES, INSERT, UPDATE, DELETE o MERGE "
                          f"(se recibió «{kw.upper() or '?'}»).")
        params = _has_params(sql)
        if req.analyze:
            if kw not in ("select", "with", "values", "table") or _DML_WORD.search(stripped) \
                    or re.search(r"\binto\b", stripped, re.I):
                return refuse("EXPLAIN ANALYZE no permitido",
                              "Por seguridad EXPLAIN ANALYZE solo se permite para una única sentencia "
                              "SELECT (sin INSERT/UPDATE/DELETE ni SELECT INTO), porque ejecuta la consulta.",
                              "medium")
            if params:
                return refuse("Se necesita SQL con valores literales",
                              "La consulta tiene parámetros ($1, $2…). Para EXPLAIN ANALYZE pegue la "
                              "consulta con valores reales en lugar de los parámetros.", "medium")
        generic = params and not req.analyze

        plan_doc = self._run_explain(sql, analyze=req.analyze, generic=generic)
        root = plan_doc.get("Plan", {})
        lines: list[str] = []
        _render_node(root, 0, lines)
        if "Planning Time" in plan_doc:
            lines.append(f"Planning Time: {plan_doc['Planning Time']:.3f} ms")
        if "Execution Time" in plan_doc:
            lines.append(f"Execution Time: {plan_doc['Execution Time']:.3f} ms")
        ctx = _PlanContext(self)
        findings = ctx.analyze_plan(root, analyzed=req.analyze)
        if generic:
            findings.append(Finding(
                severity="info", title="Plan genérico",
                detail="La consulta tiene parámetros: se muestra el plan genérico, "
                       "sin valores concretos. Para tiempos reales use EXPLAIN ANALYZE con SQL literal."))
        if not any(f.severity in ("high", "medium") for f in findings):
            findings.append(Finding(severity="info", title="Sin problemas evidentes",
                                    detail="No se detectaron patrones problemáticos en el plan."))
        findings.sort(key=lambda f: SEVERITY_ORDER[f.severity])
        return ExplainResult(query=sql, plan_text="\n".join(lines), plan_json=[plan_doc],
                             total_cost=root.get("Total Cost"), findings=findings)

    def _run_explain(self, sql: str, analyze: bool, generic: bool = False) -> dict:
        opts = ["VERBOSE", "FORMAT JSON"]
        if analyze:
            opts = ["ANALYZE", "BUFFERS"] + opts
        if generic and self.version_num < 160000:
            return self._generic_plan_legacy(sql)
        if generic:
            opts.insert(0, "GENERIC_PLAN")
        stmt = f"EXPLAIN ({', '.join(opts)}) {sql}"
        if not analyze:
            rows = self._q(stmt)
        else:
            conn = self._get_conn()
            try:
                with conn.transaction(force_rollback=True):
                    conn.execute(MARK + "SET TRANSACTION READ ONLY")
                    conn.execute(MARK + f"SET LOCAL statement_timeout = {int(self._timeout_ms)}")
                    rows = conn.execute(MARK + stmt).fetchall()
            except psycopg.Error as exc:
                if conn.broken:
                    self._drop_conn()
                if isinstance(exc, pg_errors.ReadOnlySqlTransaction):
                    raise CollectorError("La consulta intenta modificar datos; EXPLAIN ANALYZE solo se "
                                         "permite para consultas de lectura.") from exc
                raise CollectorError(self._error_message(exc)) from exc
        if not rows:
            raise CollectorError("EXPLAIN no devolvió ningún plan.")
        doc = rows[0]["QUERY PLAN"]
        if isinstance(doc, str):
            import json
            doc = json.loads(doc)
        return doc[0] if isinstance(doc, list) else doc

    def _generic_plan_legacy(self, sql: str) -> dict:
        """Generic plan on PG < 16 (no EXPLAIN GENERIC_PLAN): PREPARE the parameterized text and
        EXPLAIN EXECUTE it with NULLs under plan_cache_mode = force_generic_plan (PG12+), so the
        NULLs are never folded into the plan. Nothing is executed."""
        nparams = max(int(m[1:]) for m in _PARAM.findall(_strip_noise(sql)))
        conn = self._get_conn()
        name = "dbmon_generic_plan"
        try:
            try:
                with conn.transaction(force_rollback=True):
                    conn.execute(MARK + "SET LOCAL plan_cache_mode = force_generic_plan")
                    conn.execute(MARK + f"PREPARE {name} AS {sql}")
                    args = ", ".join(["NULL"] * nparams)
                    rows = conn.execute(
                        MARK + f"EXPLAIN (VERBOSE, FORMAT JSON) EXECUTE {name}({args})").fetchall()
            finally:
                if not conn.broken:
                    try:
                        conn.execute(MARK + f"DEALLOCATE {name}")
                    except psycopg.Error:
                        pass
        except psycopg.Error as exc:
            if conn.broken:
                self._drop_conn()
            raise CollectorError(
                "No se pudo obtener el plan genérico de la consulta parametrizada "
                f"({self._diag(exc)}). Pegue la consulta con valores literales.") from exc
        doc = rows[0]["QUERY PLAN"]
        return doc[0] if isinstance(doc, list) else doc

    # ================================================================ tables / indexes
    def tables(self) -> list[TableStat]:
        rows = self._q("""
            SELECT s.schemaname, s.relname,
                   CASE WHEN c.reltuples < 0 THEN s.n_live_tup ELSE c.reltuples::bigint END AS rows_estimate,
                   pg_relation_size(s.relid) AS size_bytes,
                   pg_total_relation_size(s.relid) AS total_size_bytes,
                   s.seq_scan, s.seq_tup_read, s.idx_scan, s.n_dead_tup, s.n_mod_since_analyze,
                   greatest(s.last_analyze, s.last_autoanalyze) AS last_analyzed,
                   greatest(s.last_vacuum, s.last_autovacuum) AS last_vacuum
            FROM pg_stat_user_tables s JOIN pg_class c ON c.oid = s.relid
            ORDER BY pg_total_relation_size(s.relid) DESC
            LIMIT 1000
        """)
        return [TableStat(
            schema_name=r["schemaname"], table=r["relname"], rows_estimate=r["rows_estimate"],
            size_bytes=r["size_bytes"], total_size_bytes=r["total_size_bytes"],
            seq_scans=r["seq_scan"], seq_rows_read=r["seq_tup_read"], index_scans=r["idx_scan"],
            dead_rows=r["n_dead_tup"], modifications_since_analyze=r["n_mod_since_analyze"],
            last_analyzed=r["last_analyzed"], last_vacuum=r["last_vacuum"],
        ) for r in rows]

    def _index_rows(self) -> list[dict]:
        last_used = "s.last_idx_scan" if self.version_num >= 160000 else "NULL::timestamptz"
        return self._q(f"""
            SELECT s.schemaname, s.relname, s.indexrelname, s.indexrelid, s.relid,
                   pg_relation_size(s.indexrelid) AS size_bytes, s.idx_scan,
                   i.indisunique, i.indisprimary, i.indisvalid,
                   pg_get_indexdef(s.indexrelid) AS definition,
                   ARRAY(SELECT pg_get_indexdef(s.indexrelid, k, true)
                         FROM generate_series(1, i.indnkeyatts) AS k ORDER BY k) AS columns,
                   pg_get_expr(i.indpred, i.indrelid) AS predicate,
                   am.amname,
                   EXISTS (SELECT 1 FROM pg_constraint co WHERE co.conindid = s.indexrelid) AS backs_constraint,
                   {last_used} AS last_used
            FROM pg_stat_user_indexes s
            JOIN pg_index i ON i.indexrelid = s.indexrelid
            JOIN pg_class ic ON ic.oid = s.indexrelid
            JOIN pg_am am ON am.oid = ic.relam
            ORDER BY s.schemaname, s.relname, s.indexrelname
        """)

    @staticmethod
    def _find_duplicates(rows: list[dict]) -> dict[int, str]:
        """indexrelid -> name of the index that makes it redundant."""
        dup: dict[int, str] = {}
        by_table: dict[int, list[dict]] = {}
        for r in rows:
            by_table.setdefault(r["relid"], []).append(r)
        for idxs in by_table.values():
            for a in idxs:
                if a["indisprimary"]:
                    continue
                for b in idxs:
                    if a is b or a["amname"] != b["amname"] or a["predicate"] != b["predicate"]:
                        continue
                    ca, cb = list(a["columns"]), list(b["columns"])
                    if ca == cb:
                        # identical: keep primary > unique > constraint-backed > lower oid
                        def rank(x):
                            return (not x["indisprimary"], not x["indisunique"],
                                    not x["backs_constraint"], x["indexrelid"])
                        if rank(a) > rank(b) and not a["backs_constraint"]:
                            dup[a["indexrelid"]] = qualified(b["schemaname"], b["indexrelname"])
                            break
                    elif len(ca) < len(cb) and cb[:len(ca)] == ca and not a["indisunique"] \
                            and a["amname"] == "btree":
                        dup[a["indexrelid"]] = qualified(b["schemaname"], b["indexrelname"])
                        break
        return dup

    def indexes(self) -> list[IndexStat]:
        rows = self._index_rows()
        dup = self._find_duplicates(rows)
        return [IndexStat(
            schema_name=r["schemaname"], table=r["relname"], index=r["indexrelname"],
            columns=list(r["columns"] or []), definition=r["definition"], size_bytes=r["size_bytes"],
            scans=r["idx_scan"], is_unique=r["indisunique"], is_primary=r["indisprimary"],
            unused=(r["idx_scan"] == 0 and not r["indisunique"] and not r["indisprimary"]),
            duplicate_of=dup.get(r["indexrelid"]), last_used=r["last_used"],
        ) for r in rows]

    # ================================================================ locks
    def locks(self) -> list[LockInfo]:
        wait = ("coalesce(l.waitstart, blocked.state_change)" if self.version_num >= 140000
                else "blocked.state_change")
        rows = self._q(f"""
            SELECT blocked.pid AS blocked_pid, blocked.usename AS blocked_user, blocked.query AS blocked_query,
                   blocking.pid AS blocking_pid, blocking.usename AS blocking_user,
                   blocking.query AS blocking_query, blocking.state AS blocking_state,
                   l.locktype, l.mode,
                   coalesce(l.relation::regclass::text,
                            (SELECT string_agg(DISTINCT gl.relation::regclass::text, ', ')
                             FROM pg_locks gl JOIN pg_class gc ON gc.oid = gl.relation
                             WHERE gl.pid = blocked.pid AND gl.granted AND gc.relkind IN ('r', 'p'))
                   ) AS object_name,
                   extract(epoch FROM now() - {wait}) AS wait_seconds
            FROM pg_stat_activity blocked
            CROSS JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS bp(pid)
            JOIN pg_stat_activity blocking ON blocking.pid = bp.pid
            LEFT JOIN LATERAL (SELECT * FROM pg_locks pl WHERE pl.pid = blocked.pid AND NOT pl.granted
                               LIMIT 1) l ON true
            WHERE blocked.datname = current_database()
            ORDER BY wait_seconds DESC NULLS LAST
        """)
        out = []
        for r in rows:
            lt = r["locktype"]
            if lt and r["mode"]:
                lt = f"{lt} ({r['mode']})"
            bq = r["blocking_query"]
            if r["blocking_state"] and r["blocking_state"].startswith("idle in transaction"):
                bq = f"[{r['blocking_state']}] {bq or ''}".strip()
            out.append(LockInfo(
                blocked_session=str(r["blocked_pid"]), blocked_user=r["blocked_user"],
                blocked_query=r["blocked_query"], blocking_session=str(r["blocking_pid"]),
                blocking_user=r["blocking_user"], blocking_query=bq, lock_type=lt,
                object_name=r["object_name"],
                wait_seconds=round(float(r["wait_seconds"]), 3) if r["wait_seconds"] is not None else None,
            ))
        return out

    # ================================================================ config
    def config(self) -> list[ConfigParam]:
        names = [n for n, _ in _CONFIG_PARAMS]
        rows = self._q("""
            SELECT name, setting, current_setting(name) AS pretty, unit, short_desc, category
            FROM pg_settings WHERE name = ANY(%s)
        """, (names,))
        by_name = {r["name"]: r for r in rows}
        out = []
        for name, hint in _CONFIG_PARAMS:
            r = by_name.get(name)
            if not r:
                continue
            pretty = r["pretty"]
            unit = r["unit"] if r["unit"] and re.fullmatch(r"-?[\d.]+", pretty or "") else None
            rec = _config_hint(name, r["setting"], pretty, hint)
            out.append(ConfigParam(name=name, value=pretty, unit=unit, description=r["short_desc"],
                                   category=r["category"], recommended=rec))
        return out

    # ================================================================ recommendations
    def recommendations(self) -> list[Recommendation]:
        recs: list[Recommendation] = []
        info = self._pgss()

        # --- extension
        if info.get("error"):
            spl = self._q1("SELECT current_setting('shared_preload_libraries') AS v")["v"] or ""
            loaded = "pg_stat_statements" in spl
            recs.append(Recommendation(
                id="extension:pg_stat_statements", category="extension", severity="high",
                title="Habilitar pg_stat_statements",
                detail=("Sin pg_stat_statements no es posible saber qué consultas consumen más tiempo. "
                        + ("La librería ya está en shared_preload_libraries; solo falta crear la extensión "
                           "en esta base de datos." if loaded else
                           "La librería no está en shared_preload_libraries: hay que añadirla y reiniciar "
                           "PostgreSQL antes de crear la extensión.")),
                object_name="pg_stat_statements",
                sql=("CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" + (
                    "" if loaded else
                    "\n-- Requiere (y reinicio del servidor):\n"
                    f"-- ALTER SYSTEM SET shared_preload_libraries = '{(spl + ',') if spl else ''}"
                    "pg_stat_statements';")),
                estimated_benefit="Visibilidad de las consultas más costosas",
            ))

        tables = {(t["schemaname"], t["relname"]): t for t in self._q("""
            SELECT s.schemaname, s.relname, s.relid, s.seq_scan, s.seq_tup_read, coalesce(s.idx_scan, 0) AS idx_scan,
                   s.n_live_tup, s.n_dead_tup, s.n_mod_since_analyze,
                   CASE WHEN c.reltuples < 0 THEN s.n_live_tup ELSE c.reltuples::bigint END AS rows_est,
                   pg_relation_size(s.relid) AS size_bytes,
                   greatest(s.last_analyze, s.last_autoanalyze) AS last_analyzed,
                   c.reloptions
            FROM pg_stat_user_tables s JOIN pg_class c ON c.oid = s.relid
        """)}
        idx_rows = self._index_rows()
        dup = self._find_duplicates(idx_rows)
        stats_reset = self._q1(
            "SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()").get("stats_reset")
        since = (f" desde el último reinicio de estadísticas ({stats_reset:%Y-%m-%d %H:%M})"
                 if stats_reset else " desde que se crearon las estadísticas")

        # --- missing indexes (from plans of top queries + table scan statistics)
        recs.extend(self._missing_index_recs(info, tables, idx_rows))

        # --- duplicate / unused indexes
        keepers = set(dup.values())   # indexes that make a duplicate redundant: never suggest dropping
        for r in idx_rows:
            name = qualified(r["schemaname"], r["indexrelname"])
            size = _fmt_bytes(r["size_bytes"])
            if r["indexrelid"] in dup:
                recs.append(Recommendation(
                    id=f"duplicate_index:{r['schemaname']}.{r['indexrelname']}", category="duplicate_index",
                    severity="medium",
                    title=f"Índice duplicado {r['indexrelname']}",
                    detail=(f"El índice {name} ({', '.join(r['columns'])}) es redundante con "
                            f"{dup[r['indexrelid']]}, que cubre las mismas columnas iniciales. Ocupa {size} y "
                            "ralentiza las escrituras sin aportar a las lecturas."),
                    object_name=name,
                    sql=f"DROP INDEX CONCURRENTLY IF EXISTS {name};",
                    estimated_benefit=f"Libera {size} y abarata INSERT/UPDATE",
                ))
            elif r["idx_scan"] == 0 and not r["indisunique"] and not r["indisprimary"] \
                    and not r["backs_constraint"] and name not in keepers:
                big = (r["size_bytes"] or 0) >= BIG_TABLE_BYTES
                recs.append(Recommendation(
                    id=f"unused_index:{r['schemaname']}.{r['indexrelname']}", category="unused_index",
                    severity="medium" if big else "low",
                    title=f"Índice sin uso {r['indexrelname']}",
                    detail=(f"El índice {name} sobre {r['relname']} ({', '.join(r['columns'])}) no se ha usado "
                            f"nunca{since}. Ocupa {size}. Antes de eliminarlo compruebe que tampoco se usa en "
                            "réplicas ni en procesos periódicos (cierres de mes, informes)."),
                    object_name=name,
                    sql=f"DROP INDEX CONCURRENTLY IF EXISTS {name};",
                    estimated_benefit=f"Libera {size} y abarata INSERT/UPDATE",
                ))

        # --- stale stats / bloat
        for (schema, table), t in tables.items():
            q = qualified(schema, table)
            rows_est = max(int(t["rows_est"] or 0), int(t["n_live_tup"] or 0))
            mods = int(t["n_mod_since_analyze"] or 0)
            live, dead = int(t["n_live_tup"] or 0), int(t["n_dead_tup"] or 0)
            av_off = any(o.lower() in ("autovacuum_enabled=false", "autovacuum_enabled=off")
                         for o in (t["reloptions"] or []))
            if (t["last_analyzed"] is None and rows_est >= 1000) or \
                    (mods >= 1000 and mods >= 0.1 * max(rows_est, 1)):
                pct = 100.0 * mods / max(rows_est, 1)
                recs.append(Recommendation(
                    id=f"stale_stats:{schema}.{table}", category="stale_stats",
                    severity="medium" if pct >= 20 else "low",
                    title=f"Estadísticas desactualizadas en {table}",
                    detail=(f"La tabla {q} tiene {mods:,} filas modificadas desde el último ANALYZE "
                            f"(≈{pct:.0f}% de {rows_est:,})" +
                            ("" if t["last_analyzed"] else " y nunca se ha analizado") +
                            ". El planificador puede elegir planes malos con estimaciones antiguas."),
                    object_name=q, sql=f"ANALYZE {q};",
                    estimated_benefit="Estimaciones de filas correctas y mejores planes",
                ))
            if dead > 10_000 and dead > 0.2 * (live + dead):
                ratio = 100.0 * dead / (live + dead)
                sql = f"VACUUM (ANALYZE) {q};"
                detail = (f"La tabla {q} tiene {dead:,} filas muertas ({ratio:.0f}% del total). "
                          "Ocupan espacio y obligan a leer más páginas en cada escaneo.")
                if av_off:
                    detail += " El autovacuum está DESACTIVADO en esta tabla (autovacuum_enabled = false)."
                    sql += f"\n-- Reactivar autovacuum en la tabla:\n-- ALTER TABLE {q} RESET (autovacuum_enabled);"
                recs.append(Recommendation(
                    id=f"bloat:{schema}.{table}", category="bloat",
                    severity="high" if ratio >= 50 or av_off else "medium",
                    title=f"Filas muertas en {table}", detail=detail, object_name=q, sql=sql,
                    estimated_benefit="Menos páginas leídas por escaneo",
                ))

        # --- config
        recs.extend(self._config_recs())

        # --- slow queries
        if not info.get("error"):
            for tq in self.top_queries("total_time", 10):
                if tq.mean_time_ms >= 500 and tq.calls >= 2:
                    short = re.sub(r"\s+", " ", tq.query).strip()
                    recs.append(Recommendation(
                        id=f"query:{tq.query_id}", category="query",
                        severity="high" if tq.mean_time_ms >= 2000 else "medium",
                        title=f"Consulta lenta (media {tq.mean_time_ms / 1000:.2f} s)",
                        detail=(f"La consulta {tq.query_id} tarda de media {tq.mean_time_ms:,.0f} ms en "
                                f"{tq.calls:,} ejecuciones ({tq.pct_total_time or 0:.0f}% del tiempo del top). "
                                "Revise su plan (Explain) para ver por qué es lenta."),
                        object_name=tq.query_id,
                        sql=f"-- Revisar el plan de la consulta {tq.query_id}:\n-- "
                            + (short[:500] + ("…" if len(short) > 500 else "")),
                        estimated_benefit=f"Hasta {tq.total_time_ms / 1000:,.0f} s de tiempo acumulado",
                    ))

        # de-duplicate ids, sort by severity
        seen: set[str] = set()
        uniq = []
        for r in recs:
            if r.id not in seen:
                seen.add(r.id)
                uniq.append(r)
        uniq.sort(key=lambda r: SEVERITY_ORDER[r.severity])
        return uniq

    def _missing_index_recs(self, info: dict, tables: dict, idx_rows: list[dict]) -> list[Recommendation]:
        existing: dict[tuple[str, str], list[list[str]]] = {}
        for r in idx_rows:
            existing.setdefault((r["schemaname"], r["relname"]), []).append(list(r["columns"]))

        def covered(schema: str, table: str, cols: list[str]) -> bool:
            for ic in existing.get((schema, table), []):
                if ic[:len(cols)] == cols or (ic and ic[0] == cols[0] and len(cols) == 1):
                    return True
            return False

        # candidates from the plans of the top queries
        cand: dict[tuple[str, str, tuple[str, ...]], dict] = {}
        if not info.get("error"):
            ctx = _PlanContext(self)
            for tq in self.top_queries("total_time", 20):
                if tq.total_time_ms <= 0:
                    continue
                sql = _clean_statement(tq.query)
                stripped = _strip_noise(sql)
                if _first_keyword(stripped) not in ("select", "with", "update", "delete") \
                        or _is_multi_statement(sql):
                    continue
                params = _has_params(sql)
                try:
                    doc = self._run_explain(sql, analyze=False, generic=params)
                except CollectorError:
                    continue
                for c in ctx.index_candidates(doc.get("Plan", {})):
                    key = (c["schema"], c["table"], tuple(c["columns"]))
                    e = cand.setdefault(key, {"time": 0.0, "queries": [], "rows": c["rows"]})
                    e["time"] += tq.total_time_ms
                    e["queries"].append(tq.query_id)

        recs = []
        tables_with_rec: set[tuple[str, str]] = set()
        for (schema, table, cols), e in sorted(cand.items(), key=lambda kv: -kv[1]["time"]):
            cols_l = list(cols)
            if covered(schema, table, cols_l):
                continue
            t = tables.get((schema, table), {})
            q = qualified(schema, table)
            idx = index_name_for(table, cols_l)
            seq_reads = int(t.get("seq_tup_read") or 0)
            recs.append(Recommendation(
                id=f"missing_index:{schema}.{table}({','.join(cols_l)})", category="missing_index",
                severity="high" if e["time"] >= 10_000 or seq_reads >= 10_000_000 else "medium",
                title=f"Falta índice en {table} ({', '.join(cols_l)})",
                detail=(f"{len(e['queries'])} consulta(s) del top recorren la tabla completa {q} "
                        f"(≈{int(e['rows'] or 0):,} filas) filtrando por {', '.join(cols_l)}. Tiempo acumulado "
                        f"de esas consultas: {e['time'] / 1000:,.1f} s. Escaneos secuenciales en la tabla: "
                        f"{int(t.get('seq_scan') or 0):,} ({seq_reads:,} filas leídas). "
                        f"Consultas: {', '.join(e['queries'][:5])}."),
                object_name=q,
                sql=(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {quote_ident(idx)}\n"
                     f"    ON {q} ({', '.join(quote_ident(c) for c in cols_l)});"),
                estimated_benefit="Sustituye escaneos completos por búsquedas por índice",
            ))
            tables_with_rec.add((schema, table))

        # tables with heavy sequential scans but no plan-derived columns
        for (schema, table), t in tables.items():
            if (schema, table) in tables_with_rec:
                continue
            seq, seq_read, idx = int(t["seq_scan"] or 0), int(t["seq_tup_read"] or 0), int(t["idx_scan"] or 0)
            rows_est = int(t["rows_est"] or 0)
            big = (t["size_bytes"] or 0) >= BIG_TABLE_BYTES or rows_est >= BIG_TABLE_ROWS
            if not big or seq < 50 or seq_read < 1_000_000 or seq_read / max(seq, 1) < BIG_TABLE_ROWS:
                continue
            q = qualified(schema, table)
            recs.append(Recommendation(
                id=f"missing_index:{schema}.{table}", category="missing_index",
                severity="low",
                title=f"Muchos escaneos secuenciales en {table}",
                detail=(f"La tabla {q} ({rows_est:,} filas, {_fmt_bytes(t['size_bytes'])}) se ha recorrido "
                        f"completa {seq:,} veces ({seq_read:,} filas leídas) frente a {idx:,} accesos por índice. "
                        "No se pudo determinar automáticamente la columna filtrada: revise con Explain las "
                        "consultas que usan esta tabla y cree un índice sobre las columnas del WHERE."),
                object_name=q,
                sql=(f"-- Revise las consultas sobre {q} y cree un índice sobre las columnas filtradas, p. ej.:\n"
                     f"-- CREATE INDEX CONCURRENTLY IF NOT EXISTS {quote_ident(table + '_col_idx')} ON {q} (columna);"),
                estimated_benefit="Sustituye escaneos completos por búsquedas por índice",
            ))
        return recs

    def _config_recs(self) -> list[Recommendation]:
        recs = []
        r = self._q1("""
            SELECT d.blks_hit, d.blks_read, current_setting('shared_buffers') AS shared_buffers,
                   current_setting('track_io_timing') AS track_io_timing,
                   current_setting('max_connections')::int AS max_conn,
                   current_setting('idle_in_transaction_session_timeout') AS iits,
                   (SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend') AS conns,
                   (SELECT count(*) FROM pg_stat_activity a WHERE a.datname = current_database()
                      AND a.state LIKE 'idle in transaction%%'
                      AND now() - a.state_change > interval '30 seconds') AS idle_tx
            FROM pg_stat_database d WHERE d.datname = current_database()
        """)
        hit, read = int(r.get("blks_hit") or 0), int(r.get("blks_read") or 0)
        ratio = self._hit_ratio(hit, read)
        if ratio is not None and ratio < 95 and hit + read >= 100_000:
            recs.append(Recommendation(
                id="config:cache_hit_ratio", category="config",
                severity="high" if ratio < 90 else "medium",
                title=f"Ratio de aciertos de caché bajo ({ratio:.1f}%)",
                detail=(f"Solo el {ratio:.1f}% de las lecturas se sirven desde shared_buffers "
                        f"(actual: {r['shared_buffers']}); lo deseable en OLTP es > 99%. Considere aumentar "
                        "shared_buffers (≈25% de la RAM) y revisar consultas que leen tablas completas."),
                object_name="shared_buffers",
                sql=("-- Ajustar al ~25% de la RAM del servidor (requiere reinicio):\n"
                     "-- ALTER SYSTEM SET shared_buffers = '2GB';"),
                estimated_benefit="Menos lecturas físicas de disco",
            ))
        if int(r.get("idle_tx") or 0) > 0:
            recs.append(Recommendation(
                id="config:idle_in_transaction", category="config", severity="medium",
                title=f"{r['idle_tx']} sesión(es) «idle in transaction»",
                detail=("Hay sesiones con una transacción abierta e inactiva desde hace más de 30 s. Retienen "
                        "bloqueos y evitan que VACUUM limpie filas muertas. Revise la aplicación (commits "
                        f"olvidados) y limite su duración (actual idle_in_transaction_session_timeout = {r['iits']})."),
                object_name="idle_in_transaction_session_timeout",
                sql=("ALTER SYSTEM SET idle_in_transaction_session_timeout = '5min';\n"
                     "SELECT pg_reload_conf();"),
                estimated_benefit="Evita bloqueos y bloat por transacciones abandonadas",
            ))
        conns, max_conn = int(r.get("conns") or 0), int(r.get("max_conn") or 0)
        if max_conn and conns >= 0.8 * max_conn:
            recs.append(Recommendation(
                id="config:connections", category="config", severity="high",
                title=f"Conexiones cerca del límite ({conns}/{max_conn})",
                detail=("El número de conexiones está por encima del 80% de max_connections. Use un pooler "
                        "(PgBouncer) o revise el tamaño de los pools de la aplicación."),
                object_name="max_connections",
                sql="-- Considere un pooler (PgBouncer) antes de subir max_connections.",
            ))
        if r.get("track_io_timing") == "off":
            recs.append(Recommendation(
                id="config:track_io_timing", category="config", severity="low",
                title="Activar track_io_timing",
                detail="Permite medir cuánto tiempo pasa cada consulta esperando E/S de disco (overhead bajo).",
                object_name="track_io_timing",
                sql="ALTER SYSTEM SET track_io_timing = on;\nSELECT pg_reload_conf();",
            ))
        return recs


# ====================================================================== plan analysis
class _PlanContext:
    """Plan-walking heuristics. Looks up relation info (rows, columns, indexes) lazily."""

    def __init__(self, collector: PostgresCollector):
        self.c = collector
        self._rel: dict[tuple[str, str], dict] = {}

    def rel_info(self, schema: Optional[str], table: str) -> dict:
        key = (schema or "", table)
        if key not in self._rel:
            regname = qualified(schema, table) if schema else quote_ident(table)
            try:
                row = self.c._q1("""
                    SELECT c.oid, n.nspname, c.relname,
                           CASE WHEN c.reltuples < 0 THEN 0 ELSE c.reltuples::bigint END AS reltuples,
                           pg_relation_size(c.oid) AS size_bytes,
                           ARRAY(SELECT attname::text FROM pg_attribute
                                 WHERE attrelid = c.oid AND attnum > 0 AND NOT attisdropped) AS columns,
                           ARRAY(SELECT pg_get_indexdef(i.indexrelid, 1, true) FROM pg_index i
                                 WHERE i.indrelid = c.oid) AS index_lead
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.oid = to_regclass(%s)
                """, (regname,))
            except CollectorError:
                row = {}
            self._rel[key] = row or {}
        return self._rel[key]

    @staticmethod
    def walk(node: dict, parent: Optional[dict] = None) -> Iterable[tuple[dict, Optional[dict]]]:
        yield node, parent
        for ch in node.get("Plans", []) or []:
            yield from _PlanContext.walk(ch, node)

    @staticmethod
    def filter_columns(filter_text: str, table_cols: list[str]) -> list[str]:
        """Columns of the table compared in the filter: equality first, then ranges."""
        eq, rng = [], []
        for col in table_cols:
            pat = re.compile(
                r'(?<![\w$"])(?:[\w$]+\.)?"?' + re.escape(col) + r'"?\)?(?:::[\w ]+?)?\s*'
                r'(=\s*ANY|=|<>|!=|<=|>=|<|>|~~\*?|!~~\*?|IS\b|IN\b|BETWEEN\b)', re.I)
            m = pat.search(filter_text)
            if not m:
                continue
            op = m.group(1).upper().replace(" ", "")
            target = eq if op in ("=", "=ANY", "IN", "IS") else rng
            target.append((m.start(), col))
        return [c for _, c in sorted(eq)] + [c for _, c in sorted(rng)][:1]

    def _scan_rows(self, node: dict, info: dict, analyzed: bool) -> int:
        if analyzed and "Actual Rows" in node:
            loops = node.get("Actual Loops", 1) or 1
            return int((node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0)) * loops)
        return int(info.get("reltuples") or 0)

    def index_candidates(self, root: dict, analyzed: bool = False) -> list[dict]:
        out = []
        for node, _ in self.walk(root):
            if node.get("Node Type") != "Seq Scan" or not node.get("Filter") or not node.get("Relation Name"):
                continue
            info = self.rel_info(node.get("Schema"), node["Relation Name"])
            if not info:
                continue
            rows = self._scan_rows(node, info, analyzed)
            if rows < BIG_TABLE_ROWS and (info.get("size_bytes") or 0) < BIG_TABLE_BYTES:
                continue
            cols = self.filter_columns(node["Filter"], list(info.get("columns") or []))[:3]
            if not cols:
                continue
            out.append({"schema": info["nspname"], "table": info["relname"], "columns": cols,
                        "rows": rows, "filter": node["Filter"], "node": node,
                        "has_lead_index": cols[0] in (info.get("index_lead") or [])})
        return out

    def analyze_plan(self, root: dict, analyzed: bool) -> list[Finding]:
        findings: list[Finding] = []
        # 1. seq scans with filter on big tables
        for c in self.index_candidates(root, analyzed):
            q = qualified(c["schema"], c["table"])
            node = c["node"]
            removed = node.get("Rows Removed by Filter")
            extra = f" Se descartaron {int(removed):,} filas por el filtro." if removed is not None else ""
            if c["has_lead_index"]:
                findings.append(Finding(
                    severity="medium", title=f"Escaneo secuencial en {c['table']} pese a existir índice",
                    detail=(f"Se recorre completa {q} (≈{c['rows']:,} filas) con el filtro {c['filter']}, aunque "
                            f"existe un índice que empieza por {c['columns'][0]}. El filtro puede ser poco "
                            f"selectivo o las estadísticas estar desactualizadas.{extra}"),
                    sql=f"ANALYZE {q};"))
            else:
                idx = index_name_for(c["table"], c["columns"])
                findings.append(Finding(
                    severity="high" if c["rows"] >= 100_000 else "medium",
                    title=f"Escaneo secuencial en {c['table']} ({c['rows']:,} filas)",
                    detail=(f"Se recorre la tabla completa {q} para aplicar el filtro {c['filter']}.{extra} "
                            f"Un índice sobre ({', '.join(c['columns'])}) permitiría leer solo las filas necesarias."),
                    sql=(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {quote_ident(idx)} ON {q} "
                         f"({', '.join(quote_ident(x) for x in c['columns'])});")))

        stale_done: set[str] = set()
        for node, _ in self.walk(root):
            nt = node.get("Node Type", "")
            # 2. sorts / hashes spilling to disk
            if nt in ("Sort", "Incremental Sort"):
                method = node.get("Sort Method", "") or ""
                if node.get("Sort Space Type") == "Disk" or method.startswith("external"):
                    space = node.get("Sort Space Used")
                    findings.append(Finding(
                        severity="medium", title="Ordenación en disco",
                        detail=(f"El Sort por {', '.join(node.get('Sort Key', []) or [])} usa «{method}» y "
                                f"escribe {space or '?'} kB en disco porque no cabe en work_mem. Aumente work_mem "
                                "para esta sesión/consulta o cree un índice que devuelva las filas ya ordenadas."),
                        sql="SET work_mem = '64MB';  -- solo para la sesión que ejecuta la consulta"))
            if nt == "Hash" and (node.get("Hash Batches", 1) or 1) > 1:
                findings.append(Finding(
                    severity="medium", title="Hash dividido en lotes (disco)",
                    detail=(f"El Hash necesitó {node['Hash Batches']} lotes porque no cabe en work_mem "
                            f"(memoria pico {node.get('Peak Memory Usage', '?')} kB)."),
                    sql="SET work_mem = '64MB';  -- solo para la sesión que ejecuta la consulta"))
            # 3. estimates vs actual rows
            if analyzed and node.get("Actual Loops"):
                est, act = float(node.get("Plan Rows", 0)), float(node.get("Actual Rows", 0))
                hi, lo = max(est, act), min(est, act)
                if hi >= 1000 and (hi + 1) / (lo + 1) > 10:
                    rel = node.get("Relation Name")
                    target = qualified(node["Schema"], rel) if rel and node.get("Schema") else rel
                    key = target or nt
                    if key not in stale_done:
                        stale_done.add(key)
                        findings.append(Finding(
                            severity="medium", title="Estimación de filas muy desviada",
                            detail=(f"En el nodo {_node_label(node)} el planificador estimó {int(est):,} filas "
                                    f"pero se obtuvieron {int(act):,} (×{(hi + 1) / (lo + 1):.0f}). Las estadísticas "
                                    "pueden estar desactualizadas o haber columnas correlacionadas."),
                            sql=f"ANALYZE {target};" if target else "ANALYZE;"))
            # 4. nested loops with big outer side
            if nt == "Nested Loop" and node.get("Plans"):
                outer = node["Plans"][0]
                rows = (outer.get("Actual Rows", 0) * (outer.get("Actual Loops", 1) or 1)
                        if analyzed and "Actual Rows" in outer else outer.get("Plan Rows", 0))
                if rows >= 10_000:
                    inner = node["Plans"][1] if len(node["Plans"]) > 1 else {}
                    inner_desc = _node_label(inner) if inner else "?"
                    findings.append(Finding(
                        severity="high" if inner.get("Node Type") == "Seq Scan" else "medium",
                        title=f"Nested Loop con {int(rows):,} filas externas",
                        detail=(f"El Nested Loop ejecuta el lado interno ({inner_desc}) una vez por cada una de las "
                                f"{int(rows):,} filas externas. Si el lado interno no usa un índice por la columna de "
                                "unión, conviene crearlo; si la estimación es errónea, ejecute ANALYZE para que el "
                                "planificador elija un Hash Join.")))
        # 5. shared reads vs hits
        hit, read = root.get("Shared Hit Blocks"), root.get("Shared Read Blocks")
        if analyzed and hit is not None and read is not None and read > 1000 and read > hit:
            findings.append(Finding(
                severity="medium", title="Mayoría de lecturas desde disco",
                detail=(f"La consulta leyó {read:,} bloques desde disco frente a {hit:,} desde caché "
                        f"({100 * hit / (hit + read):.0f}% de aciertos). Puede deberse a escaneos completos de "
                        "tablas grandes o a un shared_buffers pequeño.")))
        return findings


# ====================================================================== plan rendering
_AGG = {"Plain": "Aggregate", "Sorted": "GroupAggregate", "Hashed": "HashAggregate", "Mixed": "MixedAggregate"}


def _node_label(node: dict) -> str:
    nt = node.get("Node Type", "?")
    label = nt
    if nt == "Aggregate":
        label = _AGG.get(node.get("Strategy", "Plain"), "Aggregate")
    if nt == "ModifyTable" and node.get("Operation"):
        label = node["Operation"]
    if nt in ("Hash Join", "Merge Join", "Nested Loop") and node.get("Join Type") not in (None, "Inner"):
        jt = node["Join Type"]
        label = f"{nt} {jt} Join" if nt == "Nested Loop" else nt.replace(" Join", f" {jt} Join")
    if node.get("Parallel Aware"):
        label = "Parallel " + label
    if node.get("Scan Direction") == "Backward":
        label += " Backward"
    if node.get("Index Name"):
        label += f" using {node['Index Name']}"
    if node.get("Relation Name"):
        rel = node["Relation Name"]
        if node.get("Schema"):
            rel = f"{node['Schema']}.{rel}"
        label += f" on {rel}"
        if node.get("Alias") and node["Alias"] != node["Relation Name"]:
            label += f" {node['Alias']}"
    elif node.get("CTE Name"):
        label += f" on {node['CTE Name']}"
    elif node.get("Function Name"):
        label += f" on {node['Function Name']}"
    return label


_DETAIL_KEYS = ["Index Cond", "Recheck Cond", "Hash Cond", "Merge Cond", "Join Filter", "Filter",
                "Rows Removed by Index Recheck", "Rows Removed by Join Filter", "Rows Removed by Filter",
                "Heap Fetches"]


def _render_node(node: dict, depth: int, lines: list[str]) -> None:
    indent = "      " * depth
    if node.get("Subplan Name"):
        lines.append(f"{indent}  {node['Subplan Name']}")
    head = indent + ("->  " if depth else "")
    cost = (f"(cost={node.get('Startup Cost', 0):.2f}..{node.get('Total Cost', 0):.2f} "
            f"rows={node.get('Plan Rows', 0)} width={node.get('Plan Width', 0)})")
    actual = ""
    if "Actual Total Time" in node and node.get("Actual Loops"):
        actual = (f" (actual time={node.get('Actual Startup Time', 0):.3f}..{node['Actual Total Time']:.3f} "
                  f"rows={node.get('Actual Rows', 0)} loops={node.get('Actual Loops', 0)})")
    elif "Actual Loops" in node and not node.get("Actual Loops"):
        actual = " (never executed)"
    lines.append(f"{head}{_node_label(node)}  {cost}{actual}")
    sub = indent + ("      " if depth else "  ")
    for key in _DETAIL_KEYS:
        if node.get(key) not in (None, ""):
            lines.append(f"{sub}{key}: {node[key]}")
    for key in ("Sort Key", "Group Key", "Presorted Key"):
        if node.get(key):
            lines.append(f"{sub}{key}: {', '.join(node[key])}")
    if node.get("Sort Method"):
        lines.append(f"{sub}Sort Method: {node['Sort Method']}  "
                     f"{node.get('Sort Space Type', '')}: {node.get('Sort Space Used', '?')}kB")
    if node.get("Hash Buckets"):
        lines.append(f"{sub}Buckets: {node['Hash Buckets']}  Batches: {node.get('Hash Batches', 1)}  "
                     f"Memory Usage: {node.get('Peak Memory Usage', '?')}kB")
    if node.get("Workers Planned") is not None:
        w = f"{sub}Workers Planned: {node['Workers Planned']}"
        if node.get("Workers Launched") is not None:
            w += f"  Launched: {node['Workers Launched']}"
        lines.append(w)
    bufs = []
    for kind in ("Shared", "Local", "Temp"):
        parts = [f"{lbl}={node[f'{kind} {key} Blocks']}" for key, lbl in
                 (("Hit", "hit"), ("Read", "read"), ("Dirtied", "dirtied"), ("Written", "written"))
                 if node.get(f"{kind} {key} Blocks")]
        if parts:
            bufs.append(f"{kind.lower()} {' '.join(parts)}")
    if bufs:
        lines.append(f"{sub}Buffers: {', '.join(bufs)}")
    for child in node.get("Plans", []) or []:
        _render_node(child, depth + 1, lines)


# ====================================================================== misc helpers
def _fmt_bytes(n: Optional[int]) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # pragma: no cover


def _config_hint(name: str, setting: str, pretty: str, hint: Optional[str]) -> Optional[str]:
    """Static guidance, prefixed with ⚠ when the current value looks clearly off."""
    warn = False
    try:
        if name == "shared_buffers" and int(setting) <= 16384:          # <= 128MB (8kB pages)
            warn = True
        elif name == "random_page_cost" and float(setting) >= 4:
            warn = True
        elif name == "effective_io_concurrency" and int(setting) <= 1:
            warn = True
        elif name == "work_mem" and int(setting) <= 4096:              # <= 4MB (kB)
            warn = True
        elif name == "maintenance_work_mem" and int(setting) <= 65536:  # <= 64MB
            warn = True
        elif name == "track_io_timing" and setting == "off":
            warn = True
        elif name == "autovacuum" and setting == "off":
            warn = True
        elif name == "shared_preload_libraries" and "pg_stat_statements" not in setting:
            warn = True
        elif name == "idle_in_transaction_session_timeout" and setting == "0":
            warn = True
        elif name == "max_connections" and int(setting) > 500:
            warn = True
    except ValueError:
        pass
    if hint is None:
        return None
    return f"⚠ {hint}" if warn else hint
