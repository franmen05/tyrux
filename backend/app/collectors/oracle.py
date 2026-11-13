"""Oracle collector (12.2+, incl. 23ai Free) using python-oracledb in THIN mode.

Read-only: only V$ / DBA_ views are queried (no AWR/ASH, no DBMS_SQLTUNE, i.e. no Diagnostics or
Tuning Pack). The only writes are:
  * EXPLAIN PLAN, which inserts into the session-private PLAN_TABLE (global temporary table) and is
    rolled back right away;
  * DBMS_STATS.FLUSH_DATABASE_MONITORING_INFO (only if the user has the privilege; otherwise skipped),
    which flushes in-memory DML counters to DBA_TAB_MODIFICATIONS.
EXPLAIN "analyze" runs a single SELECT inside a READ ONLY transaction with a call timeout and a
fetch cap.

Everything works from inside a PDB: V$ views are container-scoped there and CDB_ views are avoided.
"""
from __future__ import annotations

import re
import time
import uuid
import zlib
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

import oracledb

from app.collectors.base import Collector, CollectorError, OrderBy
from app.schemas import (
    ActiveQuery, ConfigParam, ConnectionConfig, ExplainRequest, ExplainResult, Finding, IndexStat,
    LockInfo, MetricSample, Overview, Recommendation, TableStat, TestResult, TopQuery,
)

MODULE = "dbmon"                 # V$SESSION.MODULE of our own sessions (excluded everywhere)
MAX_SQL_CHARS = 4000
CALL_TIMEOUT_MS = 15000
CONNECT_TIMEOUT_S = 5
ANALYZE_FETCH_CAP = 10000
LARGE_TABLE_ROWS = 10000         # full scans below this size are not worth an index
SIZE_CACHE_SECONDS = 60
FLUSH_EVERY_SECONDS = 300

_PRIV_HINT = (
    " Conceda al usuario de monitorización el rol SELECT_CATALOG_ROLE "
    "(o SELECT ANY DICTIONARY): GRANT SELECT_CATALOG_ROLE TO <usuario>;"
)

# Non-Oracle-maintained schemas (application data). 12.1+ column.
_APP_OWNERS = "SELECT username FROM dba_users WHERE oracle_maintained = 'N'"
_SYS_OWNERS = "SELECT username FROM dba_users WHERE oracle_maintained = 'Y'"


# ======================================================================= pure helpers (unit-tested)
_SQL_ID_RE = re.compile(r"^[0-9a-z]{13}$")
_IDENT = r'"([A-Za-z0-9_$#]+)"'
# "COL" <op>   or   "ALIAS"."COL" <op>
_PRED_RE = re.compile(
    r'(?:"[A-Za-z0-9_$#]+"\.)?' + _IDENT +
    r"\s*(=|<>|!=|<=|>=|<|>|\bNOT\s+LIKE\b|\bLIKE\b|\bNOT\s+IN\b|\bIN\b|\bBETWEEN\b|\bIS\b)(?=\s*(.{0,3}))",
    re.IGNORECASE,
)


def predicate_columns(predicate: Optional[str]) -> list[str]:
    """Columns usable for a B-tree index in an Oracle filter/access predicate.

    Equality columns first (in order of appearance), then range/LIKE columns.  Columns wrapped in
    functions (UPPER("X")=...) are skipped: they would need a function-based index.  A LIKE with a
    leading wildcard is not indexable either.
    """
    if not predicate:
        return []
    eq: list[str] = []
    rng: list[str] = []
    for m in _PRED_RE.finditer(predicate):
        col, op, nxt = m.group(1).upper(), m.group(2).upper(), m.group(3)
        # skip when the identifier is a function argument: preceded by "FUNC(" immediately
        start = m.start()
        before = predicate[:start].rstrip()
        if before.endswith("(") and re.search(r"[A-Za-z0-9_]\($", before):
            continue
        if "NOT" in op or op in ("<>", "!=", "IS"):
            continue  # not selective / IS NULL is not indexable by a single-column B-tree
        if op == "LIKE" and nxt.startswith("'%"):
            continue
        target = eq if op in ("=", "IN") else rng
        if col not in eq and col not in rng:
            target.append(col)
    return eq + rng


def index_name_for(table: str, cols: list[str]) -> str:
    """IX_<TABLE>_<COLS>, max 30 chars (portable), hashed suffix when truncated."""
    base = "IX_" + table + "_" + "_".join(cols)
    base = re.sub(r"[^A-Z0-9_$#]", "_", base.upper())
    if len(base) <= 30:
        return base
    h = format(zlib.crc32(base.encode()) & 0xFFFFFF, "06X")
    return base[:23] + "_" + h


def q(ident: str) -> str:
    """Quote an Oracle identifier only when needed."""
    if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", ident or ""):
        return ident
    return '"' + (ident or "").replace('"', '""') + '"'


def strip_sql(sql: str) -> str:
    """Trim whitespace, trailing ';' and a trailing '/' line (SQL*Plus terminator)."""
    s = (sql or "").strip()
    s = re.sub(r"\n\s*/\s*$", "", s).strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def _strip_comments_and_strings(sql: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    s = re.sub(r"--[^\n]*", " ", s)
    s = re.sub(r"'(?:[^']|'')*'", "''", s)
    s = re.sub(r'"(?:[^"]|"")*"', '""', s)
    return s


def check_single_select(sql: str) -> Optional[str]:
    """Return None if `sql` is a single plain SELECT safe to execute, else a Spanish reason."""
    body = _strip_comments_and_strings(strip_sql(sql)).strip()
    if not body:
        return "La sentencia está vacía."
    if ";" in body:
        return "Solo se admite una única sentencia."
    up = body.upper()
    if not re.match(r"^\(*\s*(SELECT|WITH)\b", up):
        return "EXPLAIN con ejecución (analyze) solo se permite para sentencias SELECT."
    if re.search(r"\bWITH\s+(FUNCTION|PROCEDURE)\b", up):
        return "No se permiten funciones PL/SQL en la cláusula WITH."
    if re.search(r"\bFOR\s+UPDATE\b", up):
        return "SELECT ... FOR UPDATE bloquea filas: no se ejecuta."
    if re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|BEGIN|DECLARE|CALL|EXECUTE)\b",
                 re.sub(r"\bFOR\s+UPDATE\b", "", up)):
        return "La sentencia contiene palabras clave no permitidas para ejecutarse."
    if re.search(r"(?<![:\w]):[A-Za-z0-9_]+", body):
        return ("La sentencia tiene variables bind (:x); para ejecutarla con estadísticas reales "
                "sustitúyalas por valores literales.")
    return None


def bind_names(sql: str) -> list[str]:
    """Distinct bind placeholder names (:name / :1) outside strings and comments, in order."""
    out: list[str] = []
    for m in re.finditer(r"(?<![:\w]):(\w+)", _strip_comments_and_strings(sql)):
        n = m.group(1).upper() if not m.group(1).isdigit() else m.group(1)
        if n not in out:
            out.append(n)
    return out


def add_gather_hint(sql: str, tag: str) -> str:
    """Prefix a tag comment and add /*+ GATHER_PLAN_STATISTICS */ after the first SELECT."""
    s = strip_sql(sql)
    new, n = re.subn(r"\bSELECT\b", "SELECT /*+ GATHER_PLAN_STATISTICS */", s, count=1, flags=re.I)
    return f"/* {tag} */ " + (new if n else s)


def find_duplicates(idx: list[dict]) -> dict[str, str]:
    """Map index key -> key of the index that makes it redundant.

    `idx` items: {key, table_key, columns(list), unique(bool), type(str)}.  An index A is redundant
    when another B-tree index B on the same table has A's columns as its leading columns (prefix or
    identical).  Unique/PK indexes are never reported as redundant.  For identical column lists the
    second one (by key order) is reported.
    """
    out: dict[str, str] = {}
    by_table: dict[str, list[dict]] = {}
    for i in idx:
        by_table.setdefault(i["table_key"], []).append(i)
    for items in by_table.values():
        items = sorted(items, key=lambda x: x["key"])
        for a in items:
            if a["unique"] or not a["columns"] or a["type"] not in ("NORMAL", "NORMAL/REV"):
                continue
            for b in items:
                if b is a or b["type"] != a["type"] or not b["columns"]:
                    continue
                ca, cb = a["columns"], b["columns"]
                if len(cb) < len(ca) or cb[: len(ca)] != ca:
                    continue
                if len(cb) == len(ca) and not b["unique"] and b["key"] > a["key"]:
                    continue  # identical: report the later one only
                out[a["key"]] = b["key"]
                break
    return out


def plan_findings(rows: list[dict], table_info: dict[str, dict], index_cols: dict[str, list[list[str]]],
                  sql_stats: Optional[dict] = None) -> list[Finding]:
    """Heuristic findings from plan rows (V$SQL_PLAN[_STATISTICS_ALL] / PLAN_TABLE shaped dicts).

    table_info: "OWNER.TABLE" -> {"num_rows": int|None, "columns": set[str], "stale": bool}
    index_cols: "OWNER.TABLE" -> list of index column lists
    sql_stats:  {"executions", "disk_reads", "buffer_gets"} for the cursor (optional)
    """
    findings: list[Finding] = []
    seen: set[str] = set()

    def add(key: str, f: Finding) -> None:
        if key not in seen:
            seen.add(key)
            findings.append(f)

    for r in rows:
        op = (r.get("operation") or "").upper()
        opt = (r.get("options") or "").upper()
        owner, name = r.get("object_owner"), r.get("object_name")
        tkey = f"{owner}.{name}" if owner and name else None
        card = r.get("cardinality")
        fp = r.get("filter_predicates")

        if op == "TABLE ACCESS" and "FULL" in opt and tkey:
            info = table_info.get(tkey, {})
            nrows = info.get("num_rows")
            if nrows is not None and nrows >= LARGE_TABLE_ROWS and fp:
                cols = [c for c in predicate_columns(fp) if not info.get("columns") or c in info["columns"]][:3]
                covered = any(ic[: len(cols)] == cols for ic in index_cols.get(tkey, [])) if cols else False
                sql = None
                if cols and not covered:
                    ix = index_name_for(name, cols)
                    sql = (f"CREATE INDEX {q(owner)}.{ix} ON {q(owner)}.{q(name)} "
                           f"({', '.join(q(c) for c in cols)}) ONLINE;")
                add(f"full:{tkey}", Finding(
                    severity="high" if nrows >= 100000 else "medium",
                    title=f"Acceso completo (TABLE ACCESS FULL) a {tkey}",
                    detail=(f"La tabla tiene ~{nrows:,} filas y se recorre entera para aplicar el filtro "
                            f"{fp[:300]}. "
                            + (f"Un índice en ({', '.join(cols)}) permitiría acceder solo a las filas necesarias."
                               if sql else
                               "Ya existe un índice que cubre esas columnas; revise selectividad, "
                               "estadísticas o conversiones implícitas de tipo." if covered else
                               "No se pudieron deducir columnas indexables del predicado.")),
                    sql=sql))

        if op == "MERGE JOIN" and "CARTESIAN" in opt:
            add(f"cart:{r.get('id')}", Finding(
                severity="high", title="Producto cartesiano (MERGE JOIN CARTESIAN)",
                detail="El plan combina todas las filas de dos fuentes sin condición de join. Suele indicar "
                       "una condición de join olvidada o estadísticas que estiman 1 fila erróneamente."))

        # estimate vs actual (only when runtime statistics exist)
        actual, starts = r.get("last_output_rows"), r.get("last_starts")
        if actual is not None and card is not None and starts:
            per_start = actual / max(starts, 1)
            hi, lo = max(per_start, card, 1), max(min(per_start, card), 1)
            if hi / lo > 10 and hi >= 100:
                tbl = tkey if op == "TABLE ACCESS" or op == "INDEX" else None
                sql = None
                if tbl and op == "TABLE ACCESS":
                    sql = (f"BEGIN\n  DBMS_STATS.GATHER_TABLE_STATS(ownname => '{owner}', tabname => '{name}', "
                           f"cascade => TRUE);\nEND;\n/")
                add(f"card:{r.get('id')}", Finding(
                    severity="medium",
                    title=f"Estimación de filas muy desviada en el paso {r.get('id')} ({op} {opt}".strip() + ")",
                    detail=(f"El optimizador estimó {card:,} filas pero se obtuvieron ~{int(per_start):,} por "
                            "ejecución (más de 10x de diferencia). Las estadísticas pueden estar obsoletas o "
                            "faltar histogramas/estadísticas extendidas."),
                    sql=sql))

        # sorts / hashes that went (or are estimated to go) to TEMP
        if op in ("SORT", "HASH JOIN", "HASH", "WINDOW", "HASH GROUP BY") or op.startswith("HASH"):
            temp = r.get("max_tempseg_size") or r.get("last_tempseg_size") or r.get("temp_space")
            if temp:
                add(f"temp:{r.get('id')}", Finding(
                    severity="medium",
                    title=f"{op} {opt}".strip() + " usa espacio temporal (TEMP)",
                    detail=(f"La operación necesita ~{int(temp) // 1024 // 1024} MB en TEMP: no cabe en la PGA. "
                            "Revise pga_aggregate_target, reduzca filas antes de ordenar/hacer hash o añada "
                            "un índice que evite el ordenamiento.")))

        if op == "NESTED LOOPS" and card and card >= 100000:
            add(f"nl:{r.get('id')}", Finding(
                severity="low", title=f"NESTED LOOPS con muchas filas estimadas (paso {r.get('id')})",
                detail=f"Se estiman {card:,} filas en un bucle anidado; un HASH JOIN podría ser más eficiente."))

    if sql_stats:
        execs = max(int(sql_stats.get("executions") or 0), 1)
        dr = int(sql_stats.get("disk_reads") or 0)
        bg = int(sql_stats.get("buffer_gets") or 0)
        if dr / execs >= 1000:
            findings.append(Finding(
                severity="medium", title="Muchas lecturas físicas por ejecución",
                detail=(f"{dr / execs:,.0f} lecturas de disco por ejecución ({bg / execs:,.0f} lecturas lógicas). "
                        "La consulta lee muchos bloques que no están en caché: un índice más selectivo o "
                        "un buffer cache mayor reduciría la E/S.")))
    return findings


# ======================================================================= collector
_CONFIG_PARAMS: list[tuple[str, str, Optional[str]]] = [
    # name, category, recommended hint
    ("memory_target", "Memoria", "0 cuando se gestionan SGA y PGA por separado (recomendado con HugePages)"),
    ("memory_max_target", "Memoria", None),
    ("sga_target", "Memoria", "Dimensionar con V$SGA_TARGET_ADVICE; > 0 para gestión automática de SGA"),
    ("sga_max_size", "Memoria", None),
    ("pga_aggregate_target", "Memoria", "Suficiente para evitar ordenaciones en TEMP (ver V$PGA_TARGET_ADVICE)"),
    ("pga_aggregate_limit", "Memoria", None),
    ("db_cache_size", "Memoria", "0 (automático) si sga_target > 0; o mínimo garantizado"),
    ("shared_pool_size", "Memoria", "0 (automático) si sga_target > 0; o mínimo garantizado"),
    ("processes", "Conexiones", "Por encima del pico de procesos de V$RESOURCE_LIMIT con margen del 20%"),
    ("sessions", "Conexiones", "≈ 1.5 × processes + 22"),
    ("open_cursors", "Conexiones", ">= 300 en aplicaciones con pools de conexiones"),
    ("session_cached_cursors", "Conexiones", "50–200"),
    ("optimizer_mode", "Optimizador", "ALL_ROWS"),
    ("optimizer_index_cost_adj", "Optimizador", "100 (valor por defecto; valores bajos fuerzan índices)"),
    ("optimizer_features_enable", "Optimizador", "Igual a la versión de la base de datos"),
    ("optimizer_adaptive_plans", "Optimizador", "TRUE"),
    ("optimizer_adaptive_statistics", "Optimizador", "FALSE (recomendación de Oracle desde 12.2)"),
    ("cursor_sharing", "Optimizador", "EXACT; FORCE solo como parche para SQL con literales"),
    ("statistics_level", "Estadísticas", "TYPICAL (ALL añade sobrecarga; BASIC desactiva asesores)"),
    ("timed_statistics", "Estadísticas", "TRUE"),
    ("db_file_multiblock_read_count", "E/S", "Sin fijar (Oracle lo ajusta automáticamente)"),
    ("filesystemio_options", "E/S", "SETALL en sistemas de ficheros que lo soporten"),
    ("disk_asynch_io", "E/S", "TRUE"),
    ("undo_retention", "E/S", None),
    ("db_block_size", "E/S", None),
    ("control_management_pack_access", "Licencias",
     "NONE si no tiene licencia de Diagnostics/Tuning Pack"),
]


class OracleCollector(Collector):
    engine = "oracle"

    def __init__(self, cfg: ConnectionConfig):
        super().__init__(cfg)
        self._conn: Optional[oracledb.Connection] = None
        self._warnings: list[str] = []
        self._size_cache: tuple[float, Optional[int]] = (0.0, None)
        self._last_flush = 0.0
        self._flush_allowed = True

    # ------------------------------------------------------------------ connection
    def _dsn(self) -> str:
        proto = "tcps://" if self.cfg.ssl else ""
        return f"{proto}{self.cfg.host}:{self.cfg.port}/{self.cfg.database}"

    def _connect(self) -> oracledb.Connection:
        opts = self.cfg.options or {}
        try:
            conn = oracledb.connect(
                user=self.cfg.username, password=self.cfg.password, dsn=self._dsn(),
                tcp_connect_timeout=float(opts.get("connect_timeout", CONNECT_TIMEOUT_S)),
                retry_count=0,
            )
        except oracledb.Error as e:
            raise CollectorError(self._message(e)) from e
        conn.call_timeout = int(opts.get("call_timeout_ms", CALL_TIMEOUT_MS))
        conn.module = MODULE
        conn.action = "collector"
        conn.client_identifier = "dbmon"
        return conn

    def _get_conn(self) -> oracledb.Connection:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _run(self, fn: Callable[[oracledb.Connection], Any]) -> Any:
        """Run fn(conn) with the cached connection; reconnect and retry once if it died."""
        self._warnings = []
        for attempt in (1, 2):
            conn = self._get_conn()
            try:
                return fn(conn)
            except CollectorError:
                raise
            except oracledb.Error as e:
                dead = not self._healthy(conn) or self._is_conn_error(e)
                if dead:
                    self.close()
                    if attempt == 1:
                        continue
                raise CollectorError(self._message(e)) from e
        raise CollectorError("No se pudo ejecutar la consulta en Oracle.")  # pragma: no cover

    @staticmethod
    def _healthy(conn: oracledb.Connection) -> bool:
        try:
            return conn.is_healthy()
        except Exception:
            return False

    @staticmethod
    def _code(e: Exception) -> str:
        err = e.args[0] if e.args else None
        return getattr(err, "full_code", "") or ""

    def _is_conn_error(self, e: Exception) -> bool:
        code = self._code(e)
        return code.startswith("DPY-1") or code.startswith("DPY-4011") or code in (
            "ORA-03113", "ORA-03114", "ORA-03135", "ORA-01012", "ORA-00028", "ORA-02396", "DPY-4024")

    def _message(self, e: Exception) -> str:
        code = self._code(e)
        raw = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)
        where = f"{self.cfg.host}:{self.cfg.port}/{self.cfg.database}"
        if code == "ORA-01017":
            return "Usuario o contraseña de Oracle incorrectos (ORA-01017)."
        if code == "ORA-28000":
            return "La cuenta de Oracle está bloqueada (ORA-28000)."
        if code == "ORA-28001":
            return "La contraseña de Oracle ha caducado (ORA-28001)."
        if code in ("ORA-12514", "DPY-6001"):
            return f"El listener no conoce el service name '{self.cfg.database}' ({code}). Revise el nombre del servicio (p. ej. FREEPDB1)."
        if code in ("ORA-12541", "DPY-6005", "DPY-6000", "ORA-12170", "DPY-4011") or "timed out" in raw.lower():
            lines = [x.strip() for x in str(e).strip().splitlines() if x.strip()]
            cause = f" ({lines[-1]})" if len(lines) > 1 else ""
            return f"No se pudo conectar a Oracle en {where}: {raw}{cause}"
        if code in ("ORA-00942", "ORA-01031"):
            return f"Privilegios insuficientes en Oracle ({raw})." + _PRIV_HINT
        if code == "DPY-4024" or "ORA-01013" in raw:
            return f"La consulta superó el tiempo límite de {CALL_TIMEOUT_MS // 1000} s y se canceló."
        return f"Error de Oracle: {raw}"

    # ------------------------------------------------------------------ query helpers
    @staticmethod
    def _rows(conn, sql: str, binds: Any = None, lobs: bool = False) -> list[dict]:
        with conn.cursor() as cur:
            cur.arraysize = 500
            cur.prefetchrows = 501
            cur.execute(sql, binds or {}, fetch_lobs=lobs)
            cols = [d[0].lower() for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _opt_rows(self, conn, sql: str, binds: Any = None, label: str = "") -> Optional[list[dict]]:
        """Optional query: on missing view/privilege/column, record a warning and return None."""
        try:
            return self._rows(conn, sql, binds)
        except oracledb.DatabaseError as e:
            code = self._code(e)
            if code in ("ORA-00942", "ORA-01031", "ORA-00904", "ORA-06550", "ORA-04043"):
                msg = f"No accesible: {label or 'vista opcional'} ({code})."
                if code in ("ORA-00942", "ORA-01031"):
                    msg += _PRIV_HINT
                if msg not in self._warnings:
                    self._warnings.append(msg)
                return None
            raise

    @staticmethod
    def _own_sid(conn) -> int:
        with conn.cursor() as cur:
            cur.execute("SELECT TO_NUMBER(SYS_CONTEXT('USERENV','SID')) FROM dual")
            return int(cur.fetchone()[0])

    @staticmethod
    def _truncate(text: Optional[str]) -> str:
        t = (text or "").strip()
        return t if len(t) <= MAX_SQL_CHARS else t[: MAX_SQL_CHARS - 3] + "..."

    # ------------------------------------------------------------------ test / overview / sample
    def test_connection(self) -> TestResult:
        t0 = time.perf_counter()
        try:
            conn = self._connect()
        except CollectorError as e:
            return TestResult(ok=False, message=str(e))
        try:
            version = self._version(conn)
            con = self._rows(conn, "SELECT SYS_CONTEXT('USERENV','CON_NAME') con FROM dual")[0]["con"]
            ms = (time.perf_counter() - t0) * 1000
            msg = f"Conexión correcta a {con}."
            try:
                self._rows(conn, "SELECT 1 FROM v$session WHERE ROWNUM = 1")
                self._rows(conn, "SELECT 1 FROM dba_tables WHERE ROWNUM = 1")
            except oracledb.DatabaseError as e:
                msg += " Atención: " + self._message(e)
            return TestResult(ok=True, message=msg, version=version, latency_ms=round(ms, 1))
        except oracledb.Error as e:
            return TestResult(ok=False, message=self._message(e))
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _version(self, conn) -> str:
        r = self._opt_rows(conn, "SELECT version_full v FROM v$instance", label="V$INSTANCE")
        if not r:
            r = self._opt_rows(conn, "SELECT version v FROM v$instance", label="V$INSTANCE")
        if r:
            return f"Oracle {r[0]['v']}"
        v = conn.version
        return f"Oracle {v}"

    def _size(self, conn) -> Optional[int]:
        ts, val = self._size_cache
        if time.time() - ts < SIZE_CACHE_SECONDS and val is not None:
            return val
        r = self._opt_rows(conn, "SELECT SUM(bytes) b FROM dba_data_files", label="DBA_DATA_FILES")
        val = int(r[0]["b"]) if r and r[0]["b"] is not None else None
        self._size_cache = (time.time(), val)
        return val

    def _metrics(self, conn) -> dict:
        own = self._own_sid(conn)
        out: dict[str, Any] = {}
        s = self._rows(conn, f"""
            SELECT COUNT(*) total,
                   NVL(SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END), 0) active,
                   NVL(SUM(CASE WHEN status <> 'ACTIVE' THEN 1 ELSE 0 END), 0) idle,
                   NVL(SUM(CASE WHEN blocking_session IS NOT NULL THEN 1 ELSE 0 END), 0) blocked,
                   MAX(CASE WHEN status = 'ACTIVE' AND NVL(module, '-') <> '{MODULE}' THEN last_call_et END) longest
            FROM v$session
            WHERE type = 'USER' AND sid <> :own""", {"own": own})[0]
        out.update(sessions_total=int(s["total"]), sessions_active=int(s["active"]),
                   sessions_idle=int(s["idle"]), sessions_blocked=int(s["blocked"]),
                   longest_query_seconds=float(s["longest"]) if s["longest"] is not None else None)
        st = self._opt_rows(conn, """
            SELECT name, value FROM v$sysstat
            WHERE name IN ('user commits', 'user rollbacks', 'physical reads', 'physical reads cache',
                           'session logical reads', 'db block gets', 'consistent gets', 'enqueue deadlocks')""",
                            label="V$SYSSTAT")
        if st is not None:
            v = {r["name"]: int(r["value"] or 0) for r in st}
            out["commits"] = v.get("user commits")
            out["rollbacks"] = v.get("user rollbacks")
            out["physical_reads"] = v.get("physical reads")
            out["logical_reads"] = v.get("session logical reads")
            out["deadlocks"] = v.get("enqueue deadlocks")
            gets = v.get("db block gets", 0) + v.get("consistent gets", 0)
            phys = v.get("physical reads cache", v.get("physical reads", 0))
            if gets > 0:
                out["cache_hit_ratio"] = round(max(0.0, min(100.0, (1 - phys / gets) * 100)), 2)
        out["size_bytes"] = self._size(conn)
        return out

    def overview(self) -> Overview:
        def fn(conn):
            m = self._metrics(conn)
            version = self._version(conn)
            db = self._rows(conn, "SELECT SYS_CONTEXT('USERENV','CON_NAME') con, "
                                  "SYS_CONTEXT('USERENV','DB_NAME') db FROM dual")[0]
            up = self._opt_rows(conn, "SELECT (SYSDATE - startup_time) * 86400 s FROM v$instance",
                                label="V$INSTANCE")
            mx = self._opt_rows(conn, "SELECT value FROM v$parameter WHERE name = 'sessions'",
                                label="V$PARAMETER")
            top_ok = self._opt_rows(conn, "SELECT 1 x FROM v$sql WHERE ROWNUM = 1", label="V$SQL") is not None
            dbname = db["con"] if db["con"] == db["db"] else f"{db['con']} ({db['db']})"
            return Overview(
                engine="oracle", version=version, database=dbname,
                uptime_seconds=float(up[0]["s"]) if up else None,
                size_bytes=m.get("size_bytes"),
                sessions_total=m["sessions_total"], sessions_active=m["sessions_active"],
                sessions_idle=m["sessions_idle"], sessions_blocked=m["sessions_blocked"],
                max_connections=int(mx[0]["value"]) if mx else None,
                cache_hit_ratio=m.get("cache_hit_ratio"),
                commits_total=m.get("commits"), rollbacks_total=m.get("rollbacks"),
                deadlocks_total=m.get("deadlocks"),
                longest_query_seconds=m.get("longest_query_seconds"),
                top_queries_available=top_ok, warnings=list(self._warnings))
        return self._run(fn)

    def sample(self) -> MetricSample:
        def fn(conn):
            m = self._metrics(conn)
            return MetricSample(ts=datetime.now(timezone.utc), **m)
        return self._run(fn)

    # ------------------------------------------------------------------ active queries
    def active_queries(self) -> list[ActiveQuery]:
        def fn(conn):
            own = self._own_sid(conn)
            rows = self._rows(conn, f"""
                SELECT s.sid, s.serial# serial, s.username, s.module, s.program, s.machine, s.osuser,
                       s.status, s.state, s.event, s.wait_class, s.last_call_et, s.sql_id,
                       s.blocking_session, b.serial# b_serial, q.sql_fulltext
                FROM v$session s
                LEFT JOIN v$session b ON b.sid = s.blocking_session
                LEFT JOIN v$sql q ON q.sql_id = s.sql_id AND q.child_number = s.sql_child_number
                WHERE s.type = 'USER' AND s.status = 'ACTIVE' AND s.sid <> :own
                  AND NVL(s.module, '-') <> '{MODULE}'
                ORDER BY s.last_call_et DESC""", {"own": own})
            out = []
            for r in rows:
                out.append(ActiveQuery(
                    session_id=f"{r['sid']},{r['serial']}", username=r["username"],
                    application=r["module"] or r["program"],
                    client=f"{r['osuser'] or ''}@{r['machine'] or ''}".strip("@") or None,
                    state=r["status"].lower() if r["status"] else None,
                    duration_seconds=float(r["last_call_et"]) if r["last_call_et"] is not None else None,
                    wait_event=r["event"] if r["state"] == "WAITING" else "ON CPU",
                    blocked_by=[f"{r['blocking_session']},{r['b_serial']}"] if r["blocking_session"] else [],
                    query_id=r["sql_id"], query=self._truncate(r["sql_fulltext"])))
            return out
        return self._run(fn)

    # ------------------------------------------------------------------ top queries
    def top_queries(self, order_by: OrderBy = "total_time", limit: int = 50) -> list[TopQuery]:
        order = {
            "total_time": "elapsed DESC",
            "mean_time": "elapsed / GREATEST(execs, 1) DESC",
            "calls": "execs DESC",
            "io": "disk_reads DESC",
        }.get(order_by, "elapsed DESC")
        limit = max(1, min(int(limit), 500))
        return self._run(lambda conn: self._top(conn, order, limit))

    def _top(self, conn, order: str, limit: int) -> list[TopQuery]:
        rows = self._opt_rows(conn, f"""
            WITH a AS (
              SELECT sql_id, MAX(parsing_schema_name) username, SUM(executions) execs,
                     SUM(elapsed_time) elapsed, SUM(buffer_gets) buffer_gets,
                     SUM(disk_reads) disk_reads, SUM(rows_processed) rows_processed,
                     MIN(child_number) child
              FROM v$sql
              WHERE parsing_schema_name NOT IN ({_SYS_OWNERS})
                AND NVL(module, '-') <> '{MODULE}'
                AND command_type <> 0
              GROUP BY sql_id
            ), t AS (
              SELECT * FROM a WHERE elapsed > 0 ORDER BY {order} FETCH FIRST {limit} ROWS ONLY
            )
            SELECT t.*, (SELECT s.sql_fulltext FROM v$sql s
                         WHERE s.sql_id = t.sql_id AND s.child_number = t.child AND ROWNUM = 1) text
            FROM t ORDER BY {order}""", label="V$SQL")
        if rows is None:
            return []
        total = sum(float(r["elapsed"] or 0) for r in rows) or 1.0
        out = []
        for r in rows:
            execs = int(r["execs"] or 0)
            el_ms = float(r["elapsed"] or 0) / 1000.0
            bg, dr = int(r["buffer_gets"] or 0), int(r["disk_reads"] or 0)
            out.append(TopQuery(
                query_id=r["sql_id"], query=self._truncate(r["text"]), calls=execs,
                total_time_ms=round(el_ms, 3), mean_time_ms=round(el_ms / max(execs, 1), 3),
                rows=int(r["rows_processed"] or 0), disk_reads=dr, buffer_gets=bg,
                cache_hit_ratio=round(max(0.0, (bg - dr) / bg * 100), 2) if bg else None,
                username=r["username"],
                pct_total_time=round(float(r["elapsed"] or 0) / total * 100, 2)))
        return out

    # ------------------------------------------------------------------ explain
    def explain(self, req: ExplainRequest) -> ExplainResult:
        if not req.query_id and not (req.sql and req.sql.strip()):
            raise CollectorError("Indique query_id (sql_id) o el texto SQL.")
        return self._run(lambda conn: self._explain(conn, req))

    def _explain(self, conn, req: ExplainRequest) -> ExplainResult:
        sql_stats = None
        if req.analyze:
            # query_id takes precedence over sql (the UI sends both from the top-queries list)
            if req.query_id:
                sql_id = req.query_id.strip()
                if not _SQL_ID_RE.match(sql_id):
                    raise CollectorError(f"sql_id no válido: {sql_id!r}")
                text = self._sql_text(conn, sql_id)
            else:
                text = req.sql
            reason = check_single_select(text or "")
            if reason:
                raise CollectorError(reason)
            query, plan_text, rows = self._explain_analyze(conn, text)
        elif req.query_id:
            sql_id = req.query_id.strip()
            if not _SQL_ID_RE.match(sql_id):
                raise CollectorError(f"sql_id no válido: {sql_id!r}")
            query = self._sql_text(conn, sql_id)
            plan_text = self._display_cursor(conn, sql_id, None, "TYPICAL")
            rows = self._cursor_plan_rows(conn, sql_id, None)
            st = self._rows(conn, """SELECT SUM(executions) executions, SUM(disk_reads) disk_reads,
                                            SUM(buffer_gets) buffer_gets FROM v$sql WHERE sql_id = :i""",
                            {"i": sql_id})
            sql_stats = st[0] if st else None
        else:
            query = strip_sql(req.sql or "")
            plan_text, rows = self._explain_plan(conn, query)

        info, idx_cols = self._plan_objects(conn, rows)
        findings = plan_findings(rows, info, idx_cols, sql_stats)
        for tkey, ti in info.items():
            if ti.get("stale"):
                owner, name = tkey.split(".", 1)
                findings.append(Finding(
                    severity="medium", title=f"Estadísticas obsoletas en {tkey}",
                    detail="DBA_TAB_STATISTICS marca las estadísticas como obsoletas o inexistentes; el "
                           "optimizador puede elegir un mal plan.",
                    sql=(f"BEGIN\n  DBMS_STATS.GATHER_TABLE_STATS(ownname => '{owner}', tabname => '{name}', "
                         f"cascade => TRUE);\nEND;\n/")))
        total_cost = None
        for r in rows:
            if r.get("id") == 0 and r.get("cost") is not None:
                total_cost = float(r["cost"])
        if not rows and not plan_text.strip():
            plan_text = "No se encontró el plan (el cursor pudo salir de la shared pool)."
        return ExplainResult(query=query, plan_text=plan_text, plan_json=_jsonable(rows),
                             total_cost=total_cost, findings=findings)

    def _sql_text(self, conn, sql_id: str) -> str:
        r = self._rows(conn, "SELECT sql_fulltext FROM v$sql WHERE sql_id = :i AND ROWNUM = 1", {"i": sql_id})
        if not r:
            raise CollectorError(f"El sql_id {sql_id} ya no está en la shared pool (V$SQL).")
        return self._truncate(r[0]["sql_fulltext"]) if len(r[0]["sql_fulltext"] or "") > 100000 \
            else (r[0]["sql_fulltext"] or "")

    def _display_cursor(self, conn, sql_id: str, child: Optional[int], fmt: str) -> str:
        r = self._rows(conn, "SELECT plan_table_output FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(:i, :c, :f))",
                       {"i": sql_id, "c": child, "f": fmt})
        return "\n".join((x["plan_table_output"] or "") for x in r)

    _PLAN_COLS = ("id, parent_id, depth, operation, options, object_owner, object_name, object_type, "
                  "cost, cardinality, bytes, temp_space, access_predicates, filter_predicates")

    def _cursor_plan_rows(self, conn, sql_id: str, child: Optional[int]) -> list[dict]:
        child_sql = "child_number = :c" if child is not None else \
            "child_number = (SELECT MIN(child_number) FROM v$sql_plan WHERE sql_id = :i)"
        binds = {"i": sql_id, **({"c": child} if child is not None else {})}
        rows = self._opt_rows(conn, f"""
            SELECT {self._PLAN_COLS}, last_starts, last_output_rows, last_cr_buffer_gets, last_disk_reads,
                   last_tempseg_size, max_tempseg_size
            FROM v$sql_plan_statistics_all WHERE sql_id = :i AND {child_sql} ORDER BY id""", binds,
                              label="V$SQL_PLAN_STATISTICS_ALL")
        if not rows:
            rows = self._rows(conn, f"SELECT {self._PLAN_COLS} FROM v$sql_plan "
                                    f"WHERE sql_id = :i AND {child_sql} ORDER BY id", binds)
        return rows

    def _explain_plan(self, conn, sql: str) -> tuple[str, list[dict]]:
        if not sql:
            raise CollectorError("La sentencia está vacía.")
        if ";" in _strip_comments_and_strings(sql):
            raise CollectorError("Solo se admite una única sentencia.")
        stmt_id = "dbmon_" + uuid.uuid4().hex[:20]
        try:
            with conn.cursor() as cur:
                # EXPLAIN PLAN never executes the statement, but the driver still wants a value for
                # every placeholder (:x / :1): bind NULLs, Oracle ignores them for the plan.
                names = bind_names(sql)
                binds: Any = [None] * len(names) if names and all(n.isdigit() for n in names) \
                    else {n: None for n in names}
                cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{stmt_id}' FOR {sql}", binds)
            rows = self._rows(conn, f"SELECT {self._PLAN_COLS} FROM plan_table "
                                    "WHERE statement_id = :s ORDER BY id", {"s": stmt_id})
            out = self._rows(conn, "SELECT plan_table_output FROM "
                                   "TABLE(DBMS_XPLAN.DISPLAY('PLAN_TABLE', :s, 'TYPICAL'))", {"s": stmt_id})
            return "\n".join((x["plan_table_output"] or "") for x in out), rows
        except oracledb.DatabaseError as e:
            if self._is_conn_error(e):
                raise
            raise CollectorError(f"No se pudo obtener el plan: {str(e).splitlines()[0]}") from e
        finally:
            try:
                conn.rollback()   # removes our PLAN_TABLE rows
            except oracledb.Error:
                pass

    def _explain_analyze(self, conn, sql: str) -> tuple[str, str, list[dict]]:
        tag = "dbmon-analyze " + uuid.uuid4().hex[:16]
        text = add_gather_hint(sql, tag)
        fetched, capped = 0, False
        conn.action = "analyze"
        try:
            conn.rollback()   # SET TRANSACTION must start a new transaction
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.arraysize = 1000
                cur.execute(text)
                while True:
                    batch = cur.fetchmany(1000)
                    if not batch:
                        break
                    fetched += len(batch)
                    if fetched >= ANALYZE_FETCH_CAP:
                        capped = True
                        break
        except oracledb.DatabaseError as e:
            if self._is_conn_error(e):
                raise CollectorError(self._message(e)) from e
            raise CollectorError(f"Error ejecutando la consulta: {str(e).splitlines()[0]}") from e
        finally:
            try:
                conn.rollback()
            except oracledb.Error:
                pass
            conn.action = "collector"
        found = self._rows(conn, "SELECT sql_id, child_number FROM v$sql WHERE sql_text LIKE :t "
                                 "ORDER BY last_active_time DESC FETCH FIRST 1 ROWS ONLY",
                           {"t": f"/* {tag} */%"})
        if not found:
            raise CollectorError("La consulta se ejecutó pero no se encontró su cursor en V$SQL.")
        sql_id, child = found[0]["sql_id"], found[0]["child_number"]
        plan_text = self._display_cursor(conn, sql_id, child, "ALLSTATS LAST")
        note = f"-- Filas obtenidas: {fetched}" + (f" (limitado a {ANALYZE_FETCH_CAP}; estadísticas parciales)"
                                                  if capped else "")
        rows = self._cursor_plan_rows(conn, sql_id, child)
        return strip_sql(sql), note + "\n" + plan_text, rows

    def _plan_objects(self, conn, rows: list[dict]) -> tuple[dict[str, dict], dict[str, list[list[str]]]]:
        """num_rows / columns / staleness / index columns for the tables referenced by a plan."""
        tables: set[tuple[str, str]] = set()
        for r in rows:
            o, n, t = r.get("object_owner"), r.get("object_name"), (r.get("object_type") or "")
            if o and n and (r.get("operation") == "TABLE ACCESS" or t.startswith("TABLE")):
                tables.add((o, n))
            elif o and n and (r.get("operation") or "").endswith("INDEX"):
                ir = self._opt_rows(conn, "SELECT table_owner, table_name FROM dba_indexes "
                                          "WHERE owner = :o AND index_name = :n", {"o": o, "n": n},
                                    label="DBA_INDEXES")
                if ir:
                    tables.add((ir[0]["table_owner"], ir[0]["table_name"]))
        info: dict[str, dict] = {}
        idx: dict[str, list[list[str]]] = {}
        for o, n in tables:
            key = f"{o}.{n}"
            t = self._opt_rows(conn, """SELECT t.num_rows, s.stale_stats, s.last_analyzed
                                        FROM dba_tables t LEFT JOIN dba_tab_statistics s
                                          ON s.owner = t.owner AND s.table_name = t.table_name
                                         AND s.object_type = 'TABLE'
                                        WHERE t.owner = :o AND t.table_name = :n""", {"o": o, "n": n},
                               label="DBA_TABLES")
            c = self._opt_rows(conn, "SELECT column_name FROM dba_tab_columns WHERE owner = :o AND table_name = :n",
                               {"o": o, "n": n}, label="DBA_TAB_COLUMNS") or []
            ic = self._opt_rows(conn, """SELECT index_name, column_name FROM dba_ind_columns
                                         WHERE table_owner = :o AND table_name = :n
                                         ORDER BY index_name, column_position""", {"o": o, "n": n},
                                label="DBA_IND_COLUMNS") or []
            sys_owned = o in ("SYS", "SYSTEM")
            info[key] = {
                "num_rows": int(t[0]["num_rows"]) if t and t[0]["num_rows"] is not None else None,
                "columns": {x["column_name"] for x in c},
                "stale": bool(t) and not sys_owned and (t[0]["stale_stats"] == "YES" or t[0]["last_analyzed"] is None),
            }
            per: dict[str, list[str]] = {}
            for x in ic:
                per.setdefault(x["index_name"], []).append(x["column_name"])
            idx[key] = list(per.values())
        return info, idx

    # ------------------------------------------------------------------ tables
    def _flush_monitoring(self, conn) -> None:
        if not self._flush_allowed or time.time() - self._last_flush < FLUSH_EVERY_SECONDS:
            return
        self._last_flush = time.time()
        try:
            with conn.cursor() as cur:
                cur.callproc("DBMS_STATS.FLUSH_DATABASE_MONITORING_INFO")
        except oracledb.DatabaseError:
            self._flush_allowed = False   # no privilege: rely on Oracle's periodic flush

    def _seq_scans(self, conn) -> dict[str, int]:
        # Approximation: TABLE ACCESS FULL steps in cursors still in the shared pool, weighted by the
        # cursor's executions. Cursors aged out of the library cache are not counted.
        r = self._opt_rows(conn, f"""
            SELECT p.object_owner o, p.object_name n, SUM(GREATEST(s.executions, 1)) c
            FROM v$sql_plan p JOIN v$sql s
              ON s.address = p.address AND s.hash_value = p.hash_value AND s.child_number = p.child_number
            WHERE p.operation = 'TABLE ACCESS' AND p.options LIKE '%FULL'
              AND p.object_owner IN ({_APP_OWNERS}) AND NVL(s.module, '-') <> '{MODULE}'
            GROUP BY p.object_owner, p.object_name""", label="V$SQL_PLAN")
        return {f"{x['o']}.{x['n']}": int(x["c"]) for x in (r or [])}

    def _index_plan_usage(self, conn) -> dict[str, int]:
        """Index -> executions of cached cursors whose plan uses it (approximate)."""
        r = self._opt_rows(conn, f"""
            SELECT p.object_owner o, p.object_name n, SUM(GREATEST(s.executions, 1)) c
            FROM v$sql_plan p JOIN v$sql s
              ON s.address = p.address AND s.hash_value = p.hash_value AND s.child_number = p.child_number
            WHERE p.object_type LIKE 'INDEX%' AND p.object_owner IN ({_APP_OWNERS})
              AND NVL(s.module, '-') <> '{MODULE}'
            GROUP BY p.object_owner, p.object_name""", label="V$SQL_PLAN")
        return {f"{x['o']}.{x['n']}": int(x["c"]) for x in (r or [])}

    def _segment_sizes(self, conn) -> tuple[dict[str, int], dict[str, int]]:
        r = self._opt_rows(conn, f"""
            SELECT owner, segment_name, CASE WHEN segment_type LIKE 'INDEX%' THEN 'I' ELSE 'T' END k,
                   SUM(bytes) b
            FROM dba_segments
            WHERE owner IN ({_APP_OWNERS})
              AND (segment_type LIKE 'TABLE%' OR segment_type LIKE 'INDEX%')
            GROUP BY owner, segment_name, CASE WHEN segment_type LIKE 'INDEX%' THEN 'I' ELSE 'T' END""",
                           label="DBA_SEGMENTS")
        tables, idx = {}, {}
        for x in r or []:
            (idx if x["k"] == "I" else tables)[f"{x['owner']}.{x['segment_name']}"] = int(x["b"] or 0)
        return tables, idx

    def tables(self) -> list[TableStat]:
        def fn(conn):
            self._flush_monitoring(conn)
            rows = self._rows(conn, f"""
                SELECT t.owner, t.table_name, t.num_rows, t.last_analyzed
                FROM dba_tables t
                WHERE t.owner IN ({_APP_OWNERS})
                  AND t.temporary = 'N' AND t.nested = 'NO' AND t.secondary = 'N' AND t.dropped = 'NO'
                  AND t.table_name NOT LIKE 'BIN$%' AND t.table_name NOT LIKE 'DR$%'
                ORDER BY t.num_rows DESC NULLS LAST
                FETCH FIRST 2000 ROWS ONLY""")
            tsize, isize = self._segment_sizes(conn)
            imap = self._opt_rows(conn, f"""SELECT owner, index_name, table_owner, table_name FROM dba_indexes
                                            WHERE table_owner IN ({_APP_OWNERS})""", label="DBA_INDEXES") or []
            idx_total: dict[str, int] = {}
            idx_scans: dict[str, int] = {}
            plan_use = self._index_plan_usage(conn)
            for i in imap:
                tk = f"{i['table_owner']}.{i['table_name']}"
                ik = f"{i['owner']}.{i['index_name']}"
                idx_total[tk] = idx_total.get(tk, 0) + isize.get(ik, 0)
                if ik in plan_use:
                    idx_scans[tk] = idx_scans.get(tk, 0) + plan_use[ik]
            mods = self._opt_rows(conn, f"""
                SELECT table_owner o, table_name n, SUM(inserts + updates + deletes) m
                FROM dba_tab_modifications
                WHERE table_owner IN ({_APP_OWNERS}) AND partition_name IS NULL
                GROUP BY table_owner, table_name""", label="DBA_TAB_MODIFICATIONS")
            mod = {f"{x['o']}.{x['n']}": int(x["m"] or 0) for x in (mods or [])}
            seq = self._seq_scans(conn)
            out = []
            for r in rows:
                k = f"{r['owner']}.{r['table_name']}"
                size = tsize.get(k)
                out.append(TableStat(
                    schema_name=r["owner"], table=r["table_name"],
                    rows_estimate=int(r["num_rows"]) if r["num_rows"] is not None else None,
                    size_bytes=size,
                    total_size_bytes=(size or 0) + idx_total.get(k, 0) if size is not None or k in idx_total else None,
                    seq_scans=seq.get(k, 0), index_scans=idx_scans.get(k, 0),
                    modifications_since_analyze=mod.get(k, 0) if mods is not None else None,
                    last_analyzed=r["last_analyzed"]))
            return out
        return self._run(fn)

    # ------------------------------------------------------------------ indexes
    def _index_data(self, conn) -> list[IndexStat]:
        rows = self._rows(conn, f"""
            SELECT i.owner, i.index_name, i.table_owner, i.table_name, i.uniqueness, i.index_type,
                   i.visibility, i.status
            FROM dba_indexes i
            WHERE i.owner IN ({_APP_OWNERS}) AND i.index_type NOT IN ('LOB', 'IOT - TOP', 'CLUSTER')
              AND i.dropped = 'NO' AND i.table_name NOT LIKE 'BIN$%' AND i.table_name NOT LIKE 'DR$%'
              AND i.temporary = 'N'""")
        cols = self._rows(conn, f"""
            SELECT index_owner, index_name, column_name, column_position, descend
            FROM dba_ind_columns WHERE index_owner IN ({_APP_OWNERS})
            ORDER BY index_owner, index_name, column_position""")
        exprs = self._opt_rows(conn, f"""
            SELECT index_owner, index_name, column_position, column_expression
            FROM dba_ind_expressions WHERE index_owner IN ({_APP_OWNERS})""", label="DBA_IND_EXPRESSIONS") or []
        expr = {(e["index_owner"], e["index_name"], e["column_position"]): (e["column_expression"] or "").strip()
                for e in exprs}
        colmap: dict[str, list[str]] = {}
        for c in cols:
            name = expr.get((c["index_owner"], c["index_name"], c["column_position"])) or c["column_name"]
            if c["descend"] == "DESC" and not expr.get((c["index_owner"], c["index_name"], c["column_position"])):
                name += " DESC"
            colmap.setdefault(f"{c['index_owner']}.{c['index_name']}", []).append(name)
        cons = self._opt_rows(conn, f"""
            SELECT index_owner, index_name, constraint_type FROM dba_constraints
            WHERE owner IN ({_APP_OWNERS}) AND constraint_type IN ('P', 'U') AND index_name IS NOT NULL""",
                              label="DBA_CONSTRAINTS") or []
        ctype = {f"{c['index_owner'] or ''}.{c['index_name']}": c["constraint_type"] for c in cons}
        usage = self._opt_rows(conn, f"""
            SELECT owner, name, total_access_count, last_used FROM dba_index_usage
            WHERE owner IN ({_APP_OWNERS})""", label="DBA_INDEX_USAGE (12.2+)")
        use = {f"{u['owner']}.{u['name']}": u for u in (usage or [])}
        plan_use = self._index_plan_usage(conn)
        _, isize = self._segment_sizes(conn)

        dup_input = []
        for r in rows:
            k = f"{r['owner']}.{r['index_name']}"
            dup_input.append({"key": k, "table_key": f"{r['table_owner']}.{r['table_name']}",
                              "columns": colmap.get(k, []),
                              "unique": r["uniqueness"] == "UNIQUE" or k in ctype, "type": r["index_type"]})
        dups = find_duplicates(dup_input)
        dup_targets = set(dups.values())

        out = []
        for r in rows:
            k = f"{r['owner']}.{r['index_name']}"
            cl = colmap.get(k, [])
            is_pk = ctype.get(k) == "P"
            is_unique = r["uniqueness"] == "UNIQUE"
            u = use.get(k)
            iu_count = int(u["total_access_count"] or 0) if u else 0
            plan_count = plan_use.get(k, 0)
            scans = iu_count if usage is not None else plan_count
            if usage is not None and plan_count and not iu_count:
                scans = plan_count   # DBA_INDEX_USAGE is flushed every ~15 min; plan cache is fresher
            # An index that makes another one redundant is not "unused": the optimizer just prefers the
            # smaller duplicate today. The duplicate_index recommendation covers that pair.
            unused = (not is_unique and k not in ctype and iu_count == 0 and plan_count == 0
                      and k not in dup_targets)
            kind = "UNIQUE " if is_unique else ("BITMAP " if r["index_type"].startswith("BITMAP") else "")
            definition = (f"CREATE {kind}INDEX {q(r['owner'])}.{q(r['index_name'])} ON "
                          f"{q(r['table_owner'])}.{q(r['table_name'])} ({', '.join(cl)})")
            if r["visibility"] == "INVISIBLE":
                definition += " INVISIBLE"
            out.append(IndexStat(
                schema_name=r["owner"], table=r["table_name"], index=r["index_name"], columns=cl,
                definition=definition, size_bytes=isize.get(k), scans=scans, is_unique=is_unique,
                is_primary=is_pk, unused=unused,
                duplicate_of=dups[k].split(".", 1)[1] if k in dups else None,
                last_used=u["last_used"] if u else None))
        return out

    def indexes(self) -> list[IndexStat]:
        return self._run(self._index_data)

    # ------------------------------------------------------------------ locks
    def locks(self) -> list[LockInfo]:
        def fn(conn):
            rows = self._rows(conn, """
                SELECT w.sid w_sid, w.serial# w_serial, w.username w_user, w.state w_state,
                       w.seconds_in_wait, w.wait_time_micro, w.row_wait_obj#, w.event,
                       NVL(w.sql_id, w.prev_sql_id) w_sql,
                       b.sid b_sid, b.serial# b_serial, b.username b_user,
                       NVL(b.sql_id, b.prev_sql_id) b_sql,
                       (SELECT l.type || ' (modo ' || l.request || ')' FROM v$lock l
                         WHERE l.sid = w.sid AND l.request > 0 AND ROWNUM = 1) lock_type,
                       (SELECT o.owner || '.' || o.object_name FROM dba_objects o
                         WHERE o.object_id = w.row_wait_obj#) obj
                FROM v$session w JOIN v$session b ON b.sid = w.blocking_session
                WHERE w.blocking_session IS NOT NULL
                ORDER BY w.seconds_in_wait DESC""")
            ids = {r["w_sql"] for r in rows} | {r["b_sql"] for r in rows}
            ids.discard(None)
            texts: dict[str, str] = {}
            for sid in ids:
                t = self._rows(conn, "SELECT sql_fulltext FROM v$sql WHERE sql_id = :i AND ROWNUM = 1", {"i": sid})
                if t:
                    texts[sid] = self._truncate(t[0]["sql_fulltext"])
            out = []
            for r in rows:
                wait = (r["wait_time_micro"] / 1e6 if r["w_state"] == "WAITING" and r["wait_time_micro"] is not None
                        else r["seconds_in_wait"])
                lt = r["lock_type"] or r["event"]
                out.append(LockInfo(
                    blocked_session=f"{r['w_sid']},{r['w_serial']}", blocked_user=r["w_user"],
                    blocked_query=texts.get(r["w_sql"]),
                    blocking_session=f"{r['b_sid']},{r['b_serial']}", blocking_user=r["b_user"],
                    blocking_query=texts.get(r["b_sql"]), lock_type=lt, object_name=r["obj"],
                    wait_seconds=float(wait) if wait is not None else None))
            return out
        return self._run(fn)

    # ------------------------------------------------------------------ config
    def config(self) -> list[ConfigParam]:
        names = [p[0] for p in _CONFIG_PARAMS]

        def fn(conn):
            binds = {f"p{i}": n for i, n in enumerate(names)}
            rows = self._rows(conn, f"""
                SELECT name, value, display_value, description, isdefault FROM v$parameter
                WHERE name IN ({', '.join(':' + b for b in binds)})""", binds)
            by = {r["name"]: r for r in rows}
            out = []
            for name, cat, rec in _CONFIG_PARAMS:
                r = by.get(name)
                if not r:
                    continue
                val = r["display_value"] or r["value"] or ""
                desc = r["description"] or ""
                if r["isdefault"] == "TRUE":
                    desc += " (valor por defecto)"
                out.append(ConfigParam(name=name, value=str(val), unit=None, description=desc or None,
                                       category=cat, recommended=rec))
            return out
        return self._run(fn)

    # ------------------------------------------------------------------ recommendations
    def recommendations(self) -> list[Recommendation]:
        def fn(conn):
            self._flush_monitoring(conn)
            recs: list[Recommendation] = []
            recs += self._rec_missing_indexes(conn)
            idx = self._index_data(conn)
            recs += self._rec_indexes(idx)
            recs += self._rec_stale(conn)
            recs += self._rec_config(conn)
            recs += self._rec_queries(conn)
            if self._warnings:
                recs.append(Recommendation(
                    id="config:privileges", category="config", severity="info",
                    title="Algunas vistas del diccionario no son accesibles",
                    detail=" ".join(self._warnings),
                    sql="-- Ejecutar como DBA:\nGRANT SELECT_CATALOG_ROLE TO " + q(self.cfg.username.upper()) + ";"))
            order = {"high": 0, "medium": 1, "low": 2, "info": 3}
            recs.sort(key=lambda r: (order[r.severity], r.category, r.id))
            return recs
        return self._run(fn)

    def _rec_missing_indexes(self, conn) -> list[Recommendation]:
        rows = self._opt_rows(conn, f"""
            SELECT p.object_owner o, p.object_name n, p.filter_predicates fp, s.sql_id,
                   s.executions execs, s.elapsed_time el, t.num_rows
            FROM v$sql_plan p
            JOIN v$sql s ON s.address = p.address AND s.hash_value = p.hash_value
                        AND s.child_number = p.child_number
            JOIN dba_tables t ON t.owner = p.object_owner AND t.table_name = p.object_name
            WHERE p.operation = 'TABLE ACCESS' AND p.options LIKE '%FULL'
              AND p.filter_predicates IS NOT NULL
              AND p.object_owner IN ({_APP_OWNERS})
              AND NVL(s.module, '-') <> '{MODULE}'
              AND t.num_rows >= {LARGE_TABLE_ROWS}""", label="V$SQL_PLAN")
        if not rows:
            return []
        tcols: dict[str, set[str]] = {}
        icols: dict[str, list[list[str]]] = {}
        cand: dict[tuple[str, str, tuple[str, ...]], dict] = {}
        for r in rows:
            tk = f"{r['o']}.{r['n']}"
            if tk not in tcols:
                info, ic = self._plan_objects(conn, [{"object_owner": r["o"], "object_name": r["n"],
                                                       "operation": "TABLE ACCESS"}])
                tcols[tk] = info.get(tk, {}).get("columns", set())
                icols[tk] = ic.get(tk, [])
            cols = [c for c in predicate_columns(r["fp"]) if not tcols[tk] or c in tcols[tk]][:3]
            if not cols or any(ic[: len(cols)] == cols for ic in icols[tk]):
                continue
            c = cand.setdefault((r["o"], r["n"], tuple(cols)),
                                {"execs": 0, "el": 0, "sql_ids": set(), "rows": int(r["num_rows"] or 0),
                                 "pred": r["fp"]})
            c["execs"] += int(r["execs"] or 0)
            c["el"] += int(r["el"] or 0)
            c["sql_ids"].add(r["sql_id"])
        # Merge candidates whose columns are a prefix of a wider candidate on the same table
        # (e.g. (STATUS) into (STATUS, CREATED_AT)): one composite index serves both.
        for key in sorted(cand, key=lambda k: len(k[2])):
            o, n, cols = key
            wider = [k for k in cand if k[0] == o and k[1] == n and len(k[2]) > len(cols) and k[2][: len(cols)] == cols]
            if wider:
                w = cand[max(wider, key=lambda k: cand[k]["el"])]
                w["execs"] += cand[key]["execs"]
                w["el"] += cand[key]["el"]
                w["sql_ids"] |= cand[key]["sql_ids"]
                del cand[key]
        out = []
        for (o, n, cols), c in cand.items():
            el_s = c["el"] / 1e6
            sev = "high" if el_s >= 10 or c["execs"] >= 100 else "medium" if el_s >= 1 or c["execs"] >= 10 else "low"
            ix = index_name_for(n, list(cols))
            ids = ", ".join(sorted(c["sql_ids"])[:5])
            sql = (f"-- {o}.{n}: {c['execs']} ejecuciones con TABLE ACCESS FULL filtrando por "
                   f"{', '.join(cols)} (sql_id: {ids})\n"
                   f"CREATE INDEX {q(o)}.{ix} ON {q(o)}.{q(n)} ({', '.join(q(x) for x in cols)}) ONLINE;")
            out.append(Recommendation(
                id=f"missing_index:{o}.{n}({','.join(cols)})", category="missing_index", severity=sev,
                title=f"Crear índice en {o}.{n} ({', '.join(cols)})",
                detail=(f"La tabla {o}.{n} (~{c['rows']:,} filas) se recorre completa (TABLE ACCESS FULL) "
                        f"en {len(c['sql_ids'])} consulta(s) del cursor cache, {c['execs']:,} ejecuciones y "
                        f"{el_s:,.1f} s acumulados, filtrando por {', '.join(cols)} (predicado: {c['pred'][:200]}). "
                        "Un índice permitiría leer solo las filas necesarias."),
                object_name=f"{o}.{n}", sql=sql,
                estimated_benefit=f"Hasta {el_s:,.1f} s de tiempo acumulado afectado"))
        return out

    def _rec_indexes(self, idx: list[IndexStat]) -> list[Recommendation]:
        out = []
        for i in idx:
            name = f"{i.schema_name}.{i.index}"
            size_mb = (i.size_bytes or 0) / 1024 / 1024
            if i.duplicate_of:
                out.append(Recommendation(
                    id=f"duplicate_index:{name}", category="duplicate_index",
                    severity="medium" if size_mb >= 100 else "low",
                    title=f"Índice redundante {name}",
                    detail=(f"Las columnas de {name} ({', '.join(i.columns)}) son prefijo de "
                            f"{i.schema_name}.{i.duplicate_of}, que ya puede usarse para las mismas búsquedas. "
                            f"Ocupa {size_mb:,.1f} MB y encarece cada INSERT/UPDATE/DELETE."),
                    object_name=name,
                    sql=(f"-- {name} es redundante con {i.schema_name}.{i.duplicate_of}\n"
                         f"-- Recomendado: probar primero haciéndolo invisible:\n"
                         f"-- ALTER INDEX {q(i.schema_name)}.{q(i.index)} INVISIBLE;\n"
                         f"DROP INDEX {q(i.schema_name)}.{q(i.index)};"),
                    estimated_benefit=f"{size_mb:,.1f} MB y menor coste de escritura"))
            elif i.unused:
                out.append(Recommendation(
                    id=f"unused_index:{name}", category="unused_index",
                    severity="medium" if size_mb >= 100 else "low",
                    title=f"Índice sin uso {name}",
                    detail=(f"{name} ({', '.join(i.columns)}) no aparece en DBA_INDEX_USAGE ni en los planes del "
                            f"cursor cache. Ocupa {size_mb:,.1f} MB. DBA_INDEX_USAGE se actualiza cada ~15 min y "
                            "solo desde que existe el seguimiento: confirme que no lo usan procesos periódicos "
                            "(cierres mensuales, informes) ni que protege una clave foránea antes de borrarlo."),
                    object_name=name,
                    sql=(f"-- Recomendado: hacerlo invisible primero y observar unos días:\n"
                         f"-- ALTER INDEX {q(i.schema_name)}.{q(i.index)} INVISIBLE;\n"
                         f"DROP INDEX {q(i.schema_name)}.{q(i.index)};"),
                    estimated_benefit=f"{size_mb:,.1f} MB y menor coste de escritura"))
        return out

    def _rec_stale(self, conn) -> list[Recommendation]:
        rows = self._opt_rows(conn, f"""
            SELECT s.owner, s.table_name, s.num_rows, s.last_analyzed, s.stale_stats, s.stattype_locked,
                   (SELECT SUM(m.inserts + m.updates + m.deletes) FROM dba_tab_modifications m
                     WHERE m.table_owner = s.owner AND m.table_name = s.table_name
                       AND m.partition_name IS NULL) mods
            FROM dba_tab_statistics s
            JOIN dba_tables t ON t.owner = s.owner AND t.table_name = s.table_name
            WHERE s.owner IN ({_APP_OWNERS}) AND s.object_type = 'TABLE'
              AND t.temporary = 'N' AND t.dropped = 'NO' AND s.table_name NOT LIKE 'BIN$%'
              AND (s.stale_stats = 'YES' OR s.last_analyzed IS NULL)""", label="DBA_TAB_STATISTICS")
        out = []
        for r in rows or []:
            name = f"{r['owner']}.{r['table_name']}"
            never = r["last_analyzed"] is None
            nrows = int(r["num_rows"] or 0)
            mods = int(r["mods"] or 0)
            sev = "medium" if (nrows >= 100000 or mods >= 10000) else "low"
            locked = r["stattype_locked"]
            detail = ("La tabla no tiene estadísticas." if never else
                      f"Estadísticas obsoletas (último análisis {r['last_analyzed']:%Y-%m-%d %H:%M}, "
                      f"~{nrows:,} filas, {mods:,} modificaciones desde entonces).")
            detail += " El optimizador puede estimar mal el número de filas y elegir planes ineficientes."
            if locked:
                detail += f" Atención: las estadísticas están bloqueadas ({locked}); se usa force => TRUE."
            out.append(Recommendation(
                id=f"stale_stats:{name}", category="stale_stats", severity=sev,
                title=f"Actualizar estadísticas de {name}", detail=detail, object_name=name,
                sql=(f"BEGIN\n  DBMS_STATS.GATHER_TABLE_STATS(ownname => '{r['owner']}', "
                     f"tabname => '{r['table_name']}', cascade => TRUE"
                     + (", force => TRUE" if locked else "") + ");\nEND;\n/"),
                estimated_benefit="Mejores estimaciones del optimizador"))
        return out

    def _rec_config(self, conn) -> list[Recommendation]:
        out = []
        m = self._metrics(conn)
        hit = m.get("cache_hit_ratio")
        if hit is not None and (m.get("logical_reads") or 0) > 1_000_000 and hit < 90:
            p = self._opt_rows(conn, "SELECT name, display_value FROM v$parameter "
                                     "WHERE name IN ('sga_target', 'db_cache_size', 'memory_target')",
                               label="V$PARAMETER") or []
            cur = ", ".join(f"{x['name']}={x['display_value']}" for x in p)
            out.append(Recommendation(
                id="config:buffer_cache_hit", category="config", severity="medium" if hit < 80 else "low",
                title=f"Acierto de buffer cache bajo ({hit:.1f}%)",
                detail=(f"Solo el {hit:.1f}% de las lecturas lógicas se resuelven en memoria (valores actuales: "
                        f"{cur}). Antes de ampliar memoria revise las consultas con más lecturas físicas: "
                        "un índice suele ser más eficaz. Consulte V$DB_CACHE_ADVICE para dimensionar."),
                sql=("-- Estimación del beneficio de ampliar el buffer cache (no requiere Tuning Pack):\n"
                     "SELECT size_for_estimate AS mb, estd_physical_read_factor, estd_physical_reads\n"
                     "  FROM v$db_cache_advice WHERE name = 'DEFAULT' AND advice_status = 'ON' ORDER BY 1;\n"
                     "-- Después, por ejemplo (ajuste el valor; requiere memoria disponible en el servidor):\n"
                     "-- ALTER SYSTEM SET sga_target = <nuevo_tamaño> SCOPE = BOTH;"),
                estimated_benefit="Menos lecturas físicas"))
        lim = self._opt_rows(conn, "SELECT resource_name, max_utilization, current_utilization, limit_value "
                                   "FROM v$resource_limit WHERE resource_name IN ('processes', 'sessions')",
                             label="V$RESOURCE_LIMIT") or []
        for x in lim:
            try:
                limit = int(str(x["limit_value"]).strip())
            except ValueError:
                continue
            if limit > 0 and int(x["max_utilization"] or 0) >= 0.85 * limit:
                pname = x["resource_name"]
                out.append(Recommendation(
                    id=f"config:{pname}_limit", category="config", severity="high",
                    title=f"Uso de {pname} cerca del límite ({x['max_utilization']}/{limit})",
                    detail=(f"El pico de {pname} alcanzó {x['max_utilization']} de {limit}. Al llegar al límite "
                            "las nuevas conexiones fallan (ORA-00018/ORA-00020). Revise el tamaño de los pools "
                            "de conexiones o aumente el parámetro."),
                    sql=f"ALTER SYSTEM SET {pname} = {int(limit * 1.5)} SCOPE = SPFILE;  -- requiere reinicio",
                    object_name=pname))
        prm = self._opt_rows(conn, "SELECT name, value FROM v$parameter WHERE name IN "
                                   "('statistics_level', 'optimizer_index_cost_adj', 'cursor_sharing')",
                             label="V$PARAMETER") or []
        pv = {x["name"]: (x["value"] or "") for x in prm}
        if pv.get("statistics_level", "").upper() == "BASIC":
            out.append(Recommendation(
                id="config:statistics_level", category="config", severity="medium",
                title="statistics_level = BASIC",
                detail="Con BASIC se desactivan las estadísticas de segmento, asesores de memoria y el "
                       "seguimiento de modificaciones necesario para detectar estadísticas obsoletas.",
                sql="ALTER SYSTEM SET statistics_level = TYPICAL SCOPE = BOTH;", object_name="statistics_level"))
        if pv.get("optimizer_index_cost_adj") not in (None, "", "100"):
            out.append(Recommendation(
                id="config:optimizer_index_cost_adj", category="config", severity="low",
                title=f"optimizer_index_cost_adj = {pv['optimizer_index_cost_adj']}",
                detail="Un valor distinto de 100 distorsiona el coste de los accesos por índice y suele causar "
                       "planes subóptimos en versiones modernas.",
                sql="ALTER SYSTEM SET optimizer_index_cost_adj = 100 SCOPE = BOTH;",
                object_name="optimizer_index_cost_adj"))
        return out

    def _rec_queries(self, conn) -> list[Recommendation]:
        out = []
        tq = self._top(conn, "elapsed DESC", 10)
        for t in tq[:5]:
            if t.total_time_ms < 5000 and t.mean_time_ms < 1000:
                continue
            sev = "high" if (t.pct_total_time or 0) >= 30 or t.mean_time_ms >= 5000 else "medium"
            preview = re.sub(r"\s+", " ", t.query)[:200]
            out.append(Recommendation(
                id=f"query:{t.query_id}", category="query", severity=sev,
                title=f"Consulta costosa {t.query_id} ({t.total_time_ms / 1000:,.1f} s acumulados)",
                detail=(f"{t.calls:,} ejecuciones, {t.mean_time_ms:,.1f} ms de media, "
                        f"{t.pct_total_time or 0:.1f}% del tiempo de las consultas principales, "
                        f"{t.disk_reads or 0:,} lecturas físicas y {t.buffer_gets or 0:,} lógicas. "
                        f"Usuario {t.username}. SQL: {preview}. Revise su plan con «Explain»."),
                object_name=t.query_id,
                sql=(f"-- Plan de ejecución en el cursor cache:\n"
                     f"SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR('{t.query_id}', NULL, 'TYPICAL'));"),
                estimated_benefit=f"{t.pct_total_time or 0:.1f}% del tiempo de consultas"))
        return out


def _jsonable(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            d[k] = v.isoformat() if isinstance(v, datetime) else v
        out.append(d)
    return out
