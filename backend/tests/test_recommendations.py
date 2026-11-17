"""Optimización on large databases: pagination, cache, cross-page script and partial failures."""
import pytest
from fastapi.testclient import TestClient

from app import collectors, rec_cache
from app.collectors.base import CollectorError
from app.collectors.oracle import OracleCollector
from app.collectors.postgres import PostgresCollector
from app.schemas import ConnectionConfig, Recommendation
from fake_collector import FakeCollector

PAYLOAD = dict(name="big", engine="postgres", host="localhost", port=5432, database="shop",
               username="dbmon", password="dbmon")
SEVS = ["high", "medium", "low", "info"]
N = 130


def many_recs(self):
    self._check()
    FakeCollector.computed += 1
    out = []
    for i in range(N):
        sev = SEVS[i % 4]
        cat = "unused_index" if i % 2 else "stale_stats"
        out.append(Recommendation(
            id=f"{cat}:public.t{i:03d}", category=cat, severity=sev, title=f"Rec {i:03d}",
            detail=f"detalle {i}", object_name=f"public.t{i:03d}",
            sql="" if i % 10 == 9 else f"-- sql {i:03d};"))
    out.sort(key=lambda r: SEVS.index(r.severity))
    self.rec_warnings.append("Índices duplicados / sin uso: no se pudo completar (timeout).")
    return out


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DBMON_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DBMON_SECRET_KEY", raising=False)
    monkeypatch.setattr(collectors, "get_collector", FakeCollector)
    monkeypatch.setattr(FakeCollector, "recommendations", many_recs)
    FakeCollector.instances.clear()
    FakeCollector.fail = None
    FakeCollector.computed = 0
    rec_cache.clear()
    from app.main import app
    with TestClient(app) as c:
        yield c
    FakeCollector.fail = None
    rec_cache.clear()


@pytest.fixture
def base(client):
    r = client.post("/api/connections", json=PAYLOAD)
    assert r.status_code == 201
    return f"/api/connections/{r.json()['id']}"


def test_page_pagination_counts_and_warnings(client, base):
    p1 = client.get(f"{base}/recommendations/page?limit=50").json()
    assert p1["total"] == p1["total_all"] == N and p1["offset"] == 0 and p1["limit"] == 50
    assert len(p1["items"]) == 50
    assert p1["selectable_total"] == N - N // 10
    assert p1["by_severity"] == {"high": 33, "medium": 33, "low": 32, "info": 32}
    assert sum(p1["by_category"].values()) == N
    assert p1["warnings"] == ["Índices duplicados / sin uso: no se pudo completar (timeout)."]
    assert p1["generated_at"]
    p3 = client.get(f"{base}/recommendations/page?offset=100&limit=50").json()
    assert len(p3["items"]) == 30
    ids = [r["id"] for r in p1["items"]] + [r["id"] for r in p3["items"]]
    assert len(set(ids)) == 80
    # pages are sorted by severity overall
    allp = client.get(f"{base}/recommendations/page?limit=500").json()["items"]
    assert [SEVS.index(r["severity"]) for r in allp] == sorted(SEVS.index(r["severity"]) for r in allp)
    assert client.get(f"{base}/recommendations/page?offset=1000").json()["items"] == []


def test_page_filters_and_validation(client, base):
    hi = client.get(f"{base}/recommendations/page?severity=high&limit=500").json()
    assert hi["total"] == 33 and all(r["severity"] == "high" for r in hi["items"])
    assert hi["total_all"] == N
    cat = client.get(f"{base}/recommendations/page?category=unused_index&severity=medium").json()
    assert cat["total"] and all(r["category"] == "unused_index" and r["severity"] == "medium" for r in cat["items"])
    s = client.get(f"{base}/recommendations/page?search=T007").json()
    assert [r["id"] for r in s["items"]] == ["unused_index:public.t007"]
    for bad in ("limit=0", "limit=501", "offset=-1", "severity=bogus", "category=nope"):
        assert client.get(f"{base}/recommendations/page?{bad}").status_code == 422, bad
    assert client.get("/api/connections/999/recommendations/page").status_code == 404


def test_cache_shared_by_pages_list_and_script(client, base):
    client.get(f"{base}/recommendations/page")
    client.get(f"{base}/recommendations/page?offset=50")
    client.get(f"{base}/recommendations")
    client.post(f"{base}/optimize/script", json={"recommendation_ids": ["stale_stats:public.t000"]})
    assert FakeCollector.computed == 1
    client.get(f"{base}/recommendations/page?refresh=true")
    assert FakeCollector.computed == 2
    # editing the connection invalidates the cached entry
    client.put(base, json={**PAYLOAD, "database": "other"})
    client.get(f"{base}/recommendations/page")
    assert FakeCollector.computed == 3


def test_script_with_ids_from_several_pages(client, base):
    p1 = client.get(f"{base}/recommendations/page?limit=50").json()["items"]
    p3 = client.get(f"{base}/recommendations/page?offset=100&limit=50").json()["items"]
    chosen = [p1[0]["id"], p3[-1]["id"]]
    body = client.post(f"{base}/optimize/script", json={"recommendation_ids": chosen}).json()
    assert body["count"] == 2
    for rid in chosen:
        assert f"Id: {rid}" in body["script"]


def test_script_select_all_across_pages(client, base):
    body = client.post(f"{base}/optimize/script", json={"select_all": True}).json()
    assert body["count"] == N - N // 10   # every rec with SQL, not only the visible page
    body = client.post(f"{base}/optimize/script", json={
        "select_all": True, "severity": "high", "exclude_ids": ["stale_stats:public.t000"]}).json()
    high_with_sql = sum(1 for i in range(N) if i % 4 == 0 and i % 10 != 9)
    assert body["count"] == high_with_sql - 1
    assert "Id: stale_stats:public.t000" not in body["script"]


def test_collector_error_is_502_and_not_cached(client, base):
    FakeCollector.fail = "timeout de conexión"
    r = client.get(f"{base}/recommendations/page")
    assert r.status_code == 502 and r.json()["detail"] == "timeout de conexión"
    FakeCollector.fail = None
    assert client.get(f"{base}/recommendations/page").status_code == 200


# ------------------------------------------------------------------ partial failures in collectors
def _cfg(engine):
    return ConnectionConfig(id=1, name="x", engine=engine, host="h", port=1, database="d",
                            username="u", password="p")


def _rec(rid, sev="low"):
    return Recommendation(id=rid, category="stale_stats", severity=sev, title=rid, detail=rid, sql="ANALYZE x;")


def test_postgres_sections_fail_independently(monkeypatch):
    c = PostgresCollector(_cfg("postgres"))
    monkeypatch.setattr(c, "_get_conn", lambda: None)
    monkeypatch.setattr(c, "_pgss", lambda: {"error": None})

    def timeout(*a, **k):
        raise CollectorError("canceling statement due to statement timeout")
    monkeypatch.setattr(c, "top_queries", timeout)             # plan-based + slow queries sections
    monkeypatch.setattr(c, "_index_recs", timeout)
    monkeypatch.setattr(c, "_table_recs", lambda plan_tables: [_rec("stale_stats:public.a"),
                                                               _rec("bloat:public.a", "high")])
    monkeypatch.setattr(c, "_config_recs", lambda: [_rec("config:track_io_timing", "medium")])
    recs, warnings = c.recommendations_report()
    assert [r.id for r in recs] == ["bloat:public.a", "config:track_io_timing", "stale_stats:public.a"]
    assert len(warnings) == 3 and all("statement timeout" in w for w in warnings)
    assert any(w.startswith("Índices duplicados") for w in warnings)

    # nothing could be computed -> error (HTTP 502), not an empty page
    monkeypatch.setattr(c, "_pgss", lambda: {"error": "missing"})
    monkeypatch.setattr(c, "_q1", timeout)                     # extension section
    monkeypatch.setattr(c, "_table_recs", timeout)
    monkeypatch.setattr(c, "_config_recs", timeout)
    with pytest.raises(CollectorError, match="ninguna recomendación"):
        c.recommendations()
    # sections that do not apply (no pg_stat_statements) do not count as computed
    monkeypatch.setattr(c, "_config_recs", lambda: [_rec("config:track_io_timing", "medium")])
    recs, warnings = c.recommendations_report()
    assert [r.id for r in recs] == ["config:track_io_timing"] and len(warnings) == 3


def test_oracle_sections_fail_independently(monkeypatch):
    c = OracleCollector(_cfg("oracle"))
    monkeypatch.setattr(c, "_get_conn", lambda: object())
    monkeypatch.setattr(c, "_flush_monitoring", lambda conn: None)

    def boom(conn):
        raise CollectorError("La consulta superó el tiempo límite de 15 s y se canceló.")

    def with_dict_warning(conn):
        c._warnings.append("No accesible: DBA_INDEX_USAGE (ORA-00942).")
        c._warnings.append("Tiempo límite superado al consultar DBA_SEGMENTS (DPY-4024); se omitieron esos datos.")
        return [_rec("stale_stats:SHOP.T")]
    monkeypatch.setattr(c, "_rec_missing_indexes", boom)
    monkeypatch.setattr(c, "_index_data", lambda conn: (_ for _ in ()).throw(RuntimeError("bad row")))
    monkeypatch.setattr(c, "_rec_stale", with_dict_warning)
    monkeypatch.setattr(c, "_rec_config", lambda conn: [])
    monkeypatch.setattr(c, "_rec_queries", lambda conn: [_rec("query:abc", "high")])
    recs, warnings = c.recommendations_report()
    ids = [r.id for r in recs]
    assert ids[0] == "query:abc" and "stale_stats:SHOP.T" in ids
    priv = next(r for r in recs if r.id == "config:privileges")
    assert "DBA_INDEX_USAGE" in priv.detail and "DBA_SEGMENTS" not in priv.detail
    assert any(w.startswith("Índices faltantes") and "tiempo límite" in w for w in warnings)
    assert any(w.startswith("Índices duplicados") and "bad row" in w for w in warnings)
    assert any("DBA_SEGMENTS" in w for w in warnings)

    for name in ("_rec_stale", "_rec_config", "_rec_queries"):
        monkeypatch.setattr(c, name, boom)
    with pytest.raises(CollectorError, match="ninguna recomendación"):
        c.recommendations()
