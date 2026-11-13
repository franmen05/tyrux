"""Oracle collector tests.

* Pure helper tests run everywhere (no database).
* `@pytest.mark.integration` tests need the demo container (docker/oracle-demo, schema SHOP in FREEPDB1):

    docker run -d --name dbmon-demo-oracle -e ORACLE_PASSWORD=dbmon -e APP_USER=shop \
      -e APP_USER_PASSWORD=shop -p 1521:1521 \
      -v "$PWD/docker/oracle-demo":/container-entrypoint-initdb.d:ro \
      docker.io/gvenzl/oracle-free:23-slim-faststart

  Connection from env ORA_HOST / ORA_PORT / ORA_SERVICE / ORA_USER / ORA_PASSWORD
  (defaults localhost / 1521 / FREEPDB1 / shop / shop). Skipped when unreachable.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from app.collectors.base import CollectorError
from app.collectors.oracle import (
    OracleCollector, add_gather_hint, bind_names, check_single_select, find_duplicates, index_name_for,
    plan_findings, predicate_columns, strip_sql,
)
from app.schemas import ConnectionConfig, ExplainRequest


# =============================================================== pure helpers (no DB)
class TestHelpers:
    def test_predicate_columns_equality_then_range(self):
        p = ("(\"STATUS\"=:S AND \"CREATED_AT\">=TO_DATE(' 2023-01-01 00:00:00', "
             "'syyyy-mm-dd hh24:mi:ss')+:D)")
        assert predicate_columns(p) == ["STATUS", "CREATED_AT"]
        assert predicate_columns('"CREATED_AT">SYSDATE-1 AND "CUSTOMER_ID"=5') == ["CUSTOMER_ID", "CREATED_AT"]

    def test_predicate_columns_alias_and_skips(self):
        assert predicate_columns('"O"."CUSTOMER_ID"=:B1') == ["CUSTOMER_ID"]
        assert predicate_columns("UPPER(\"EMAIL\")='X'") == []
        assert predicate_columns("\"NAME\" LIKE '%abc'") == []
        assert predicate_columns("\"NAME\" LIKE 'abc%'") == ["NAME"]
        assert predicate_columns('"A" IS NULL AND "B"<>1') == []
        assert predicate_columns("(\"STATUS\"='A' OR \"STATUS\"='B')") == ["STATUS"]
        assert predicate_columns(None) == []

    def test_bind_names(self):
        assert bind_names("select * from t where a = :c and b = :C and s = ':x' /* :z */ and d = :1") == ["C", "1"]
        assert bind_names("select 1 from dual") == []

    def test_index_name(self):
        assert index_name_for("ORDERS", ["CUSTOMER_ID"]) == "IX_ORDERS_CUSTOMER_ID"
        long = index_name_for("A_VERY_LONG_TABLE_NAME", ["FIRST_COLUMN", "SECOND_COLUMN"])
        assert len(long) <= 30 and long.startswith("IX_A_VERY")

    def test_check_single_select(self):
        assert check_single_select("SELECT * FROM shop.orders WHERE customer_id = 5;") is None
        assert check_single_select("WITH x AS (SELECT 1 a FROM dual) SELECT * FROM x") is None
        assert check_single_select("select 'a;b' from dual") is None
        assert "SELECT" in check_single_select("DELETE FROM shop.orders")
        assert check_single_select("SELECT * FROM t FOR UPDATE") is not None
        assert check_single_select("SELECT 1 FROM dual; DROP TABLE x") is not None
        assert check_single_select("SELECT * FROM t WHERE a = :b") is not None
        assert check_single_select("WITH FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END; SELECT f FROM dual") \
            is not None

    def test_strip_and_hint(self):
        assert strip_sql("  select 1 from dual ;; ") == "select 1 from dual"
        assert strip_sql("select 1 from dual\n/") == "select 1 from dual"
        h = add_gather_hint("select a from t;", "tag1")
        assert h == "/* tag1 */ SELECT /*+ GATHER_PLAN_STATISTICS */ a from t"

    def test_find_duplicates(self):
        idx = [
            {"key": "S.PK", "table_key": "S.T", "columns": ["ID"], "unique": True, "type": "NORMAL"},
            {"key": "S.I_A", "table_key": "S.T", "columns": ["A"], "unique": False, "type": "NORMAL"},
            {"key": "S.I_AB", "table_key": "S.T", "columns": ["A", "B"], "unique": False, "type": "NORMAL"},
            {"key": "S.I_ID_X", "table_key": "S.T", "columns": ["ID", "X"], "unique": False, "type": "NORMAL"},
            {"key": "S.J_A", "table_key": "S.U", "columns": ["A"], "unique": False, "type": "NORMAL"},
        ]
        d = find_duplicates(idx)
        assert d == {"S.I_A": "S.I_AB"}

    def test_plan_findings(self):
        rows = [
            {"id": 0, "operation": "SELECT STATEMENT", "cost": 10},
            {"id": 1, "operation": "MERGE JOIN", "options": "CARTESIAN", "cardinality": 5},
            {"id": 2, "operation": "TABLE ACCESS", "options": "FULL", "object_owner": "SHOP",
             "object_name": "ORDERS", "cardinality": 12, "filter_predicates": '"CUSTOMER_ID"=:C',
             "last_starts": 1, "last_output_rows": 5000},
            {"id": 3, "operation": "SORT", "options": "ORDER BY", "max_tempseg_size": 10 * 1024 * 1024},
        ]
        info = {"SHOP.ORDERS": {"num_rows": 250000, "columns": {"CUSTOMER_ID", "STATUS"}}}
        f = plan_findings(rows, info, {"SHOP.ORDERS": [["ORDER_ID"]]},
                          {"executions": 2, "disk_reads": 10000, "buffer_gets": 20000})
        titles = " | ".join(x.title for x in f)
        assert "TABLE ACCESS FULL" in titles and "CARTESIAN" in titles and "TEMP" in titles
        assert "Estimación" in titles and "lecturas físicas" in titles
        full = next(x for x in f if "TABLE ACCESS FULL" in x.title)
        assert full.sql == "CREATE INDEX SHOP.IX_ORDERS_CUSTOMER_ID ON SHOP.ORDERS (CUSTOMER_ID) ONLINE;"
        # already covered by an index -> no CREATE INDEX suggestion
        f2 = plan_findings(rows[2:3], info, {"SHOP.ORDERS": [["CUSTOMER_ID", "STATUS"]]})
        assert f2[0].sql is None


# =============================================================== integration (live demo)
ORA = dict(
    host=os.environ.get("ORA_HOST", "localhost"),
    port=int(os.environ.get("ORA_PORT", "1521")),
    database=os.environ.get("ORA_SERVICE", "FREEPDB1"),
    username=os.environ.get("ORA_USER", "shop"),
    password=os.environ.get("ORA_PASSWORD", "shop"),
)


def _cfg(**over) -> ConnectionConfig:
    return ConnectionConfig(engine="oracle", name="demo", **{**ORA, **over})


def _raw_connect():
    import oracledb
    c = oracledb.connect(user=ORA["username"], password=ORA["password"],
                         dsn=f"{ORA['host']}:{ORA['port']}/{ORA['database']}", tcp_connect_timeout=5)
    c.module = "dbmon-test-workload"
    return c


def _prime():
    """Small deterministic workload so the cursor cache holds the slow statements.

    Oracle Free has a small shared pool and cursors can age out between tests, so tests that
    depend on V$SQL / V$SQL_PLAN contents call this right before asserting.
    """
    conn = _raw_connect()
    cur = conn.cursor()
    for cid in range(1, 26):
        cur.execute("SELECT order_id, status, total FROM orders WHERE customer_id = :c ORDER BY created_at DESC",
                    c=cid)
        cur.fetchall()
    for d in range(0, 10):
        cur.execute("SELECT order_id, customer_id, total FROM orders WHERE status = :s "
                    "AND created_at >= DATE '2023-01-01' + :d ORDER BY created_at", s="PENDING", d=d * 50)
        cur.fetchall()
    for oid in range(1, 20):
        cur.execute("SELECT o.order_id, oi.quantity FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
                    "WHERE oi.order_id = :o", o=oid)
        cur.fetchall()
    conn.close()


@pytest.fixture(scope="module")
def coll():
    c = OracleCollector(_cfg())
    res = c.test_connection()
    if not res.ok:
        pytest.skip(f"Oracle demo no disponible: {res.message}")
    _prime()
    yield c
    c.close()


def _orders_by_customer_sql_id(coll) -> str:
    tq = coll.top_queries("total_time", 200)
    for t in tq:
        if "customer_id = :c" in t.query.lower() and "from orders" in t.query.lower():
            return t.query_id
    pytest.fail("la consulta de ORDERS por customer_id no aparece en top_queries")




@pytest.mark.integration
class TestOracleLive:
    def test_connection_ok(self, coll):
        r = coll.test_connection()
        assert r.ok and r.version.startswith("Oracle") and r.latency_ms is not None

    def test_bad_password(self, coll):
        r = OracleCollector(_cfg(password="wrong-password")).test_connection()
        assert not r.ok and "ORA-01017" in r.message

    def test_bad_service(self, coll):
        r = OracleCollector(_cfg(database="NOPE_SERVICE")).test_connection()
        assert not r.ok and "service" in r.message.lower()

    def test_overview(self, coll):
        o = coll.overview()
        assert o.engine == "oracle" and o.version.startswith("Oracle")
        assert "FREEPDB1" in o.database.upper()
        assert o.uptime_seconds and o.uptime_seconds > 0
        assert o.size_bytes and o.size_bytes > 10 * 1024 * 1024
        assert o.max_connections and o.max_connections > 0
        assert o.cache_hit_ratio is not None and 0 <= o.cache_hit_ratio <= 100
        assert o.commits_total is not None and o.top_queries_available
        assert o.sessions_total >= o.sessions_active

    def test_sample_and_reconnect(self, coll):
        s1 = coll.sample()
        assert s1.logical_reads and s1.logical_reads > 0 and s1.commits is not None
        coll._conn.close()          # simulate a dropped connection: must reconnect transparently
        s2 = coll.sample()
        assert s2.logical_reads >= s1.logical_reads

    def test_active_queries_sees_long_call_and_excludes_self(self, coll):
        conn = _raw_connect()
        t = threading.Thread(target=lambda: conn.cursor().execute("BEGIN DBMS_SESSION.SLEEP(6); END;"))
        t.start()
        try:
            time.sleep(2.5)
            aq = coll.active_queries()
            assert any("DBMS_SESSION.SLEEP" in a.query.upper() for a in aq), aq
            assert all((a.application or "") != "dbmon" for a in aq)
            sleeper = next(a for a in aq if "DBMS_SESSION.SLEEP" in a.query.upper())
            assert "," in sleeper.session_id and sleeper.duration_seconds is not None
            o = coll.overview()
            assert o.longest_query_seconds and o.longest_query_seconds >= 1
        finally:
            t.join()
            conn.close()

    @pytest.mark.parametrize("order_by", ["total_time", "mean_time", "calls", "io"])
    def test_top_queries(self, coll, order_by):
        tq = coll.top_queries(order_by, 20)
        assert tq, "sin consultas en V$SQL"
        assert all(len(t.query_id) == 13 for t in tq)
        key = {"total_time": "total_time_ms", "mean_time": "mean_time_ms", "calls": "calls",
               "io": "disk_reads"}[order_by]
        vals = [getattr(t, key) or 0 for t in tq]
        assert vals == sorted(vals, reverse=True)
        assert abs(sum(t.pct_total_time for t in tq) - 100) < 1
        assert not any("dbms_xplan" in t.query.lower() or "v$sql" in t.query.lower() for t in tq)
        assert all(t.username not in ("SYS", "SYSTEM") for t in tq)

    def test_explain_query_id_takes_precedence_over_sql(self, coll):
        _prime()
        sid = _orders_by_customer_sql_id(coll)
        e = coll.explain(ExplainRequest(query_id=sid, sql="SELECT * FROM shop.products"))
        assert "ORDERS" in e.plan_text and sid in e.plan_text
        assert "customer_id" in e.query.lower()

    def test_explain_by_sql_id(self, coll):
        _prime()
        sid = _orders_by_customer_sql_id(coll)
        e = coll.explain(ExplainRequest(query_id=sid))
        assert "TABLE ACCESS FULL" in e.plan_text and "ORDERS" in e.plan_text
        assert e.plan_json and e.total_cost is not None
        f = next(f for f in e.findings if "TABLE ACCESS FULL" in f.title)
        assert f.sql and "CUSTOMER_ID" in f.sql and f.sql.rstrip().endswith("ONLINE;")

    def test_explain_pasted_sql(self, coll):
        e = coll.explain(ExplainRequest(
            sql="SELECT * FROM shop.orders WHERE status = 'PENDING' AND created_at > DATE '2025-01-01';"))
        assert "TABLE ACCESS FULL" in e.plan_text and e.total_cost
        f = next(f for f in e.findings if "TABLE ACCESS FULL" in f.title)
        assert "STATUS, CREATED_AT" in f.sql

    def test_explain_pasted_sql_with_binds(self, coll):
        e = coll.explain(ExplainRequest(sql="SELECT * FROM shop.orders WHERE customer_id = :c"))
        assert "ORDERS" in e.plan_text

    def test_explain_cartesian(self, coll):
        e = coll.explain(ExplainRequest(sql="SELECT * FROM shop.products p, shop.customers c"))
        assert any("CARTESIAN" in f.title for f in e.findings), e.plan_text

    def test_explain_analyze(self, coll):
        e = coll.explain(ExplainRequest(
            sql="SELECT order_id, total FROM shop.orders WHERE customer_id = 42", analyze=True))
        assert "A-Rows" in e.plan_text and "Filas obtenidas" in e.plan_text
        assert any(r.get("last_output_rows") is not None for r in e.plan_json)

    def test_explain_analyze_rejects_dml(self, coll):
        with pytest.raises(CollectorError):
            coll.explain(ExplainRequest(sql="DELETE FROM shop.orders WHERE 1=0", analyze=True))
        with pytest.raises(CollectorError):
            coll.explain(ExplainRequest(sql="SELECT * FROM shop.orders FOR UPDATE", analyze=True))

    def test_explain_errors(self, coll):
        with pytest.raises(CollectorError):
            coll.explain(ExplainRequest(query_id="zzzzzzzzzzzzz"))
        with pytest.raises(CollectorError):
            coll.explain(ExplainRequest(sql="SELECT * FROM shop.no_such_table"))
        with pytest.raises(CollectorError):
            coll.explain(ExplainRequest())

    def test_tables(self, coll):
        t = {x.table: x for x in coll.tables() if x.schema_name == "SHOP"}
        assert {"CUSTOMERS", "PRODUCTS", "ORDERS", "ORDER_ITEMS"} <= set(t)
        o = t["ORDERS"]
        assert o.rows_estimate and o.rows_estimate >= 200000
        assert o.size_bytes and o.total_size_bytes >= o.size_bytes
        assert o.seq_scans and o.seq_scans > 0
        assert o.last_analyzed is not None
        assert t["ORDER_ITEMS"].modifications_since_analyze is None or \
            t["ORDER_ITEMS"].modifications_since_analyze > 0

    def test_indexes(self, coll):
        ix = {x.index: x for x in coll.indexes() if x.schema_name == "SHOP"}
        assert ix["PK_ORDERS"].is_primary and ix["PK_ORDERS"].is_unique and not ix["PK_ORDERS"].unused
        assert ix["IX_ORDER_ITEMS_ORDER"].duplicate_of == "IX_ORDER_ITEMS_ORDER_PROD"
        assert ix["IX_ORDER_ITEMS_ORDER_PROD"].columns == ["ORDER_ID", "PRODUCT_ID"]
        assert ix["IX_CUSTOMERS_PHONE"].unused
        assert ix["IX_CUSTOMERS_PHONE"].definition.startswith("CREATE INDEX SHOP.IX_CUSTOMERS_PHONE")
        assert ix["PK_ORDERS"].size_bytes and ix["PK_ORDERS"].size_bytes > 0
        assert not any(x.table == "ORDERS" and x.columns[:1] == ["CUSTOMER_ID"] for x in ix.values())

    def test_locks(self, coll):
        a, b = _raw_connect(), _raw_connect()
        a.cursor().execute("UPDATE customers SET phone = phone WHERE customer_id = 1")
        t = threading.Thread(target=lambda: (b.cursor().execute(
            "UPDATE customers SET phone = phone WHERE customer_id = 1"), b.rollback()))
        t.start()
        try:
            time.sleep(2)
            locks = coll.locks()
            assert locks, "no se detectó el bloqueo"
            lk = locks[0]
            assert lk.blocked_user == "SHOP" and lk.blocking_user == "SHOP"
            assert lk.lock_type and "TX" in lk.lock_type
            assert lk.object_name == "SHOP.CUSTOMERS"
            assert lk.wait_seconds is not None and lk.wait_seconds >= 1
            assert "UPDATE customers" in (lk.blocked_query or "")
            assert coll.overview().sessions_blocked >= 1
        finally:
            a.rollback()
            t.join(timeout=10)
            a.close()
            b.close()

    def test_config(self, coll):
        cfg = {c.name: c for c in coll.config()}
        for n in ("sga_target", "pga_aggregate_target", "processes", "sessions", "open_cursors",
                  "optimizer_mode", "cursor_sharing", "statistics_level"):
            assert n in cfg, n
        assert cfg["optimizer_mode"].value.upper() == "ALL_ROWS"
        assert cfg["open_cursors"].recommended and cfg["sga_target"].category == "Memoria"

    def test_recommendations(self, coll):
        _prime()
        recs = {r.id: r for r in coll.recommendations()}
        ids = sorted(recs)
        mi = recs.get("missing_index:SHOP.ORDERS(CUSTOMER_ID)")
        assert mi, ids
        assert mi.sql.strip().endswith("CREATE INDEX SHOP.IX_ORDERS_CUSTOMER_ID ON SHOP.ORDERS (CUSTOMER_ID) ONLINE;")
        assert "missing_index:SHOP.ORDERS(STATUS,CREATED_AT)" in recs, ids
        dup = recs.get("duplicate_index:SHOP.IX_ORDER_ITEMS_ORDER")
        assert dup and "DROP INDEX SHOP.IX_ORDER_ITEMS_ORDER;" in dup.sql and "INVISIBLE" in dup.sql
        un = recs.get("unused_index:SHOP.IX_CUSTOMERS_PHONE")
        assert un and "DROP INDEX SHOP.IX_CUSTOMERS_PHONE;" in un.sql
        st = recs.get("stale_stats:SHOP.ORDER_ITEMS")
        assert st and "DBMS_STATS.GATHER_TABLE_STATS(ownname => 'SHOP', tabname => 'ORDER_ITEMS'" in st.sql
        assert st.sql.rstrip().endswith("/")
        assert not any(r.startswith("unused_index:SHOP.PK_") for r in recs)
        # the index that makes IX_ORDER_ITEMS_ORDER redundant must not be proposed for dropping too
        assert "unused_index:SHOP.IX_ORDER_ITEMS_ORDER_PROD" not in recs
        # (STATUS) alone is merged into the composite (STATUS, CREATED_AT) candidate
        assert "missing_index:SHOP.ORDERS(STATUS)" not in recs
        assert all(r.detail for r in recs.values())
        sev = [r.severity for r in recs.values()]
        order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        assert [order[s] for s in sev] == sorted(order[s] for s in sev)
