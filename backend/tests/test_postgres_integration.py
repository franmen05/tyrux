"""Integration tests for PostgresCollector against the live demo DB (docker/postgres-demo).

Start it with (host port configurable through DBMON_TEST_PG_PORT, default 55433):

    docker run -d --name dbmon-demo-pg -e POSTGRES_USER=dbmon -e POSTGRES_PASSWORD=dbmon \
      -e POSTGRES_DB=shop -p 55433:5432 -v "$PWD/docker/postgres-demo":/docker-entrypoint-initdb.d:ro \
      postgres:17 postgres -c shared_preload_libraries=pg_stat_statements \
      -c pg_stat_statements.track=all -c track_io_timing=on

Tests are skipped when the database is unreachable.
"""
from __future__ import annotations

import os
import random
import threading
import time

import psycopg
import pytest

from app.collectors.base import CollectorError
from app.collectors.postgres import PostgresCollector, quote_ident
from app.schemas import ConnectionConfig, ExplainRequest

pytestmark = pytest.mark.integration

HOST = os.environ.get("DBMON_TEST_PG_HOST", "localhost")
PORT = int(os.environ.get("DBMON_TEST_PG_PORT", "55433"))
DB = os.environ.get("DBMON_TEST_PG_DB", "shop")
USER = os.environ.get("DBMON_TEST_PG_USER", "dbmon")
PASSWORD = os.environ.get("DBMON_TEST_PG_PASSWORD", "dbmon")
DSN = dict(host=HOST, port=PORT, dbname=DB, user=USER, password=PASSWORD, connect_timeout=3)


def _cfg(**over) -> ConnectionConfig:
    base = dict(engine="postgres", host=HOST, port=PORT, database=DB, username=USER, password=PASSWORD)
    base.update(over)
    return ConnectionConfig(**base)


@pytest.fixture(scope="module")
def demo_db():
    try:
        conn = psycopg.connect(**DSN, autocommit=True, application_name="dbmon-tests")
    except psycopg.OperationalError as exc:
        pytest.skip(f"demo Postgres not reachable at {HOST}:{PORT}: {exc}")
    # generate a little workload so pg_stat_statements has the slow patterns
    for _ in range(15):
        conn.execute("SELECT id, status, total FROM orders WHERE customer_id = %s ORDER BY created_at DESC",
                     (random.randint(1, 50000),)).fetchall()
        conn.execute("SELECT id, customer_id, total FROM orders WHERE status = %s "
                     "AND created_at > now() - %s::interval ORDER BY created_at DESC LIMIT 100",
                     ("pending", "10 days")).fetchall()
    yield conn
    conn.close()


@pytest.fixture()
def col(demo_db):
    c = PostgresCollector(_cfg())
    yield c
    c.close()


# ------------------------------------------------------------------ connection / errors
def test_test_connection_ok(col):
    r = col.test_connection()
    assert r.ok, r.message
    assert r.version and r.version.startswith("PostgreSQL ")
    assert r.latency_ms is not None and r.latency_ms >= 0


def test_bad_password(demo_db):
    c = PostgresCollector(_cfg(password="wrong-password"))
    r = c.test_connection()
    assert not r.ok
    assert "Autenticación fallida" in r.message
    with pytest.raises(CollectorError, match="Autenticación"):
        c.overview()


def test_unknown_database(demo_db):
    c = PostgresCollector(_cfg(database="does_not_exist_db"))
    with pytest.raises(CollectorError, match="no existe"):
        c.sample()


def test_unreachable_port():
    c = PostgresCollector(_cfg(host="127.0.0.1", port=1))
    r = c.test_connection()
    assert not r.ok and "127.0.0.1:1" in r.message


def test_reconnects_after_backend_killed(col, demo_db):
    col.sample()
    pid = col._conn.info.backend_pid
    demo_db.execute("SELECT pg_terminate_backend(%s)", (pid,))
    time.sleep(0.3)
    s = col.sample()  # must transparently reconnect
    assert s.commits is not None
    assert col._conn.info.backend_pid != pid


# ------------------------------------------------------------------ overview / sample / activity
def test_overview(col):
    o = col.overview()
    assert o.engine == "postgres" and o.database == DB
    assert o.version.startswith("PostgreSQL ")
    assert o.size_bytes and o.size_bytes > 10_000_000
    assert o.uptime_seconds and o.uptime_seconds > 0
    assert o.max_connections and o.max_connections > 0
    assert o.cache_hit_ratio is not None and 0 <= o.cache_hit_ratio <= 100
    assert o.commits_total and o.commits_total > 0
    assert o.top_queries_available is True
    assert o.sessions_total >= 1  # at least the fixture connection


def test_sample_counters_grow(col, demo_db):
    s1 = col.sample()
    for _ in range(5):
        demo_db.execute("SELECT count(*) FROM orders").fetchone()
    s2 = col.sample()
    assert s2.ts > s1.ts
    assert s2.commits >= s1.commits
    assert s2.logical_reads >= s1.logical_reads  # PG < 15 flushes stats with a delay
    assert s2.physical_reads >= s1.physical_reads
    assert s2.deadlocks is not None and s2.size_bytes


def test_active_queries_and_exclusion_of_self(col):
    done = threading.Event()

    def slow():
        with psycopg.connect(**DSN, autocommit=True, application_name="dbmon-tests-sleeper") as c:
            c.execute("SELECT pg_sleep(3)")
        done.set()

    t = threading.Thread(target=slow, daemon=True)
    t.start()
    time.sleep(1.0)
    active = col.active_queries()
    sleepers = [a for a in active if a.application == "dbmon-tests-sleeper"]
    assert sleepers, active
    a = sleepers[0]
    assert a.state == "active" and "pg_sleep" in a.query
    assert a.duration_seconds and a.duration_seconds > 0.5
    assert all(q.application != "dbmon" for q in active)
    s = col.sample()
    assert s.sessions_active >= 1 and s.longest_query_seconds and s.longest_query_seconds > 0.5
    t.join(5)


# ------------------------------------------------------------------ top queries / explain
def test_top_queries(col):
    for order in ("total_time", "mean_time", "calls", "io"):
        tq = col.top_queries(order, 10)
        assert tq, order
        assert len(tq) <= 10
    tq = col.top_queries("total_time", 20)
    times = [t.total_time_ms for t in tq]
    assert times == sorted(times, reverse=True)
    assert abs(sum(t.pct_total_time or 0 for t in tq) - 100) < 1
    assert all(not t.query.startswith("/* dbmon */") for t in tq)
    assert any("customer_id" in t.query for t in tq)
    by_calls = col.top_queries("calls", 5)
    assert [t.calls for t in by_calls] == sorted([t.calls for t in by_calls], reverse=True)
    sample = next(t for t in tq if t.buffer_gets)
    assert sample.cache_hit_ratio is not None and sample.query_id.lstrip("-").isdigit()


def test_top_queries_bad_order(col):
    with pytest.raises(CollectorError):
        col.top_queries("nope", 5)  # type: ignore[arg-type]


def _customer_query_id(col) -> str:
    for t in col.top_queries("total_time", 50):
        if "FROM orders WHERE customer_id = $1" in t.query:
            return t.query_id
    pytest.fail("customer_id query not found in pg_stat_statements")


def test_explain_by_query_id_generic_plan(col):
    r = col.explain(ExplainRequest(query_id=_customer_query_id(col)))
    assert "Seq Scan" in r.plan_text and "orders" in r.plan_text
    assert r.total_cost and r.total_cost > 0
    assert isinstance(r.plan_json, list)
    idx = [f for f in r.findings if f.sql and "CREATE INDEX CONCURRENTLY" in f.sql]
    assert idx and "customer_id" in idx[0].sql
    assert any(f.title == "Plan genérico" for f in r.findings)


def test_explain_analyze_literal_sql(col):
    r = col.explain(ExplainRequest(sql="SELECT * FROM orders WHERE customer_id = 42 ORDER BY created_at;",
                                   analyze=True))
    assert "actual time=" in r.plan_text and "Execution Time" in r.plan_text
    assert "Rows Removed by Filter" in r.plan_text
    assert any(f.severity == "high" and "orders" in f.title for f in r.findings)


def test_explain_sort_spill(col):
    r = col.explain(ExplainRequest(sql="SELECT * FROM orders ORDER BY notes, total", analyze=True))
    assert any(f.title == "Ordenación en disco" for f in r.findings), r.plan_text


def test_explain_analyze_rejects_writes(col, demo_db):
    before = demo_db.execute("SELECT count(*) FROM products").fetchone()[0]
    r = col.explain(ExplainRequest(sql="DELETE FROM products WHERE id < 10", analyze=True))
    assert r.plan_text == "" and r.findings[0].title == "EXPLAIN ANALYZE no permitido"
    r = col.explain(ExplainRequest(sql="WITH d AS (DELETE FROM products RETURNING *) SELECT * FROM d",
                                   analyze=True))
    assert r.plan_text == ""
    # volatile function that writes is caught by the READ ONLY transaction
    with pytest.raises(CollectorError):
        col.explain(ExplainRequest(sql="SELECT nextval('orders_id_seq')", analyze=True))
    assert demo_db.execute("SELECT count(*) FROM products").fetchone()[0] == before
    # plain EXPLAIN of a DELETE is fine (it is not executed)
    r = col.explain(ExplainRequest(sql="DELETE FROM products WHERE id < 10"))
    assert "Delete on" in r.plan_text
    assert demo_db.execute("SELECT count(*) FROM products").fetchone()[0] == before


def test_explain_params_need_literals_for_analyze(col):
    r = col.explain(ExplainRequest(sql="SELECT * FROM orders WHERE customer_id = $1", analyze=True))
    assert r.plan_text == "" and "literales" in r.findings[0].title


def test_explain_refuses_multi_and_ddl(col):
    assert col.explain(ExplainRequest(sql="SELECT 1; SELECT 2")).findings[0].title == "Varias sentencias"
    assert col.explain(ExplainRequest(sql="DROP TABLE orders")).plan_text == ""
    with pytest.raises(CollectorError):
        col.explain(ExplainRequest())
    with pytest.raises(CollectorError):
        col.explain(ExplainRequest(query_id="123456789"))


# ------------------------------------------------------------------ tables / indexes / locks / config
def test_tables(col):
    tables = {t.table: t for t in col.tables()}
    assert {"orders", "customers", "products", "order_items"} <= set(tables)
    o = tables["orders"]
    assert o.schema_name == "public" and o.rows_estimate and o.rows_estimate >= 200_000
    assert o.total_size_bytes > o.size_bytes > 0
    assert o.seq_scans and o.seq_scans > 0 and o.seq_rows_read
    c = tables["customers"]
    assert c.dead_rows and c.dead_rows >= 10_000
    assert c.modifications_since_analyze is not None


def test_indexes(col):
    idx = {i.index: i for i in col.indexes()}
    assert idx["orders_pkey"].is_primary and idx["orders_pkey"].is_unique and not idx["orders_pkey"].unused
    assert idx["orders_total_idx"].unused and idx["orders_total_idx"].columns == ["total"]
    assert "CREATE INDEX" in idx["orders_total_idx"].definition
    assert idx["customers_email_idx"].duplicate_of == "public.customers_email_key"
    assert idx["order_items_order_id_idx"].duplicate_of == "public.order_items_order_product_idx"
    assert idx["order_items_order_product_idx"].columns == ["order_id", "product_id"]
    assert idx["customers_email_key"].duplicate_of is None
    assert idx["customers_email_key"].is_unique and not idx["customers_email_key"].unused


def test_locks_and_idle_in_transaction(col):
    a = psycopg.connect(**DSN, application_name="dbmon-tests-holder")
    b = psycopg.connect(**DSN, application_name="dbmon-tests-waiter")
    try:
        a.execute("UPDATE customers SET last_login = last_login WHERE id = 7")

        def waiter():
            try:
                b.execute("SET lock_timeout = '10s'")
                b.execute("UPDATE customers SET last_login = now() WHERE id = 7")
            except Exception:
                pass
            finally:
                b.rollback()

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(1.0)
        locks = col.locks()
        assert locks, "expected a blocked session"
        lk = next(x for x in locks if x.blocking_session == str(a.info.backend_pid))
        assert lk.blocked_session == str(b.info.backend_pid)
        assert "UPDATE customers" in (lk.blocked_query or "")
        assert lk.lock_type and lk.wait_seconds is not None and lk.wait_seconds > 0.5
        assert "customers" in (lk.object_name or "")
        assert "idle in transaction" in (lk.blocking_query or "")
        o = col.overview()
        assert o.sessions_blocked >= 1 and o.warnings
        act = {q.session_id: q for q in col.active_queries()}
        assert act[str(b.info.backend_pid)].blocked_by == [str(a.info.backend_pid)]
        assert act[str(a.info.backend_pid)].state == "idle in transaction"
    finally:
        a.rollback()
        t.join(5)
        a.close()
        b.close()


def test_config(col):
    cfg = {p.name: p for p in col.config()}
    for name in ("shared_buffers", "work_mem", "effective_cache_size", "max_connections",
                 "random_page_cost", "autovacuum", "shared_preload_libraries", "track_io_timing"):
        assert name in cfg, name
    assert "pg_stat_statements" in cfg["shared_preload_libraries"].value
    assert cfg["shared_buffers"].recommended and cfg["shared_buffers"].description
    assert cfg["track_io_timing"].value in ("on", "off")


# ------------------------------------------------------------------ recommendations
def test_recommendations(col):
    recs = col.recommendations()
    by_id = {r.id: r for r in recs}
    assert len(by_id) == len(recs), "ids must be unique"

    mi = by_id.get("missing_index:public.orders(customer_id)")
    assert mi, [r.id for r in recs]
    assert mi.category == "missing_index"
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in mi.sql and "(customer_id)" in mi.sql
    assert "missing_index:public.orders(status,created_at)" in by_id

    un = by_id["unused_index:public.orders_total_idx"]
    assert un.category == "unused_index" and "DROP INDEX CONCURRENTLY IF EXISTS public.orders_total_idx" in un.sql

    assert by_id["duplicate_index:public.customers_email_idx"].sql == \
        "DROP INDEX CONCURRENTLY IF EXISTS public.customers_email_idx;"
    assert "duplicate_index:public.order_items_order_id_idx" in by_id
    # the index that makes another one redundant must not be proposed for dropping
    assert "unused_index:public.order_items_order_product_idx" not in by_id
    assert not any(r.category in ("unused_index", "duplicate_index") and
                   ("pkey" in r.id or r.id.endswith("customers_email_key")) for r in recs)

    bloat = by_id["bloat:public.customers"]
    assert "VACUUM (ANALYZE) public.customers;" in bloat.sql
    assert by_id["stale_stats:public.customers"].sql == "ANALYZE public.customers;"

    sev = {"high": 0, "medium": 1, "low": 2, "info": 3}
    assert [sev[r.severity] for r in recs] == sorted(sev[r.severity] for r in recs)
    assert all(r.detail and r.title for r in recs)
    assert "extension:pg_stat_statements" not in by_id


def test_quote_ident():
    assert quote_ident("orders") == "orders"
    assert quote_ident("Order") == '"Order"'
    assert quote_ident("user") == '"user"'
    assert quote_ident('we"ird') == '"we""ird"'
