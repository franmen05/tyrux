import time

import pytest
from fastapi.testclient import TestClient

from app import collectors
from fake_collector import FakeCollector

PAYLOAD = dict(name="demo", engine="postgres", host="localhost", port=5432, database="shop",
               username="dbmon", password="dbmon")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DBMON_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DBMON_SECRET_KEY", raising=False)
    monkeypatch.setattr(collectors, "get_collector", FakeCollector)
    FakeCollector.instances.clear()
    FakeCollector.fail = None
    from app.main import app
    with TestClient(app) as c:
        yield c
    FakeCollector.fail = None


@pytest.fixture
def conn_id(client):
    r = client.post("/api/connections", json=PAYLOAD)
    assert r.status_code == 201
    return r.json()["id"]


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/nope").status_code == 404


def test_connections_crud(client, conn_id):
    body = client.get("/api/connections").json()
    assert len(body) == 1 and "password" not in body[0] and body[0]["monitoring"] is False
    r = client.put(f"/api/connections/{conn_id}", json={**PAYLOAD, "name": "x", "password": ""})
    assert r.status_code == 200 and r.json()["name"] == "x"
    from app import store
    assert store.get_config(conn_id).password == "dbmon"
    assert client.put("/api/connections/999", json=PAYLOAD).status_code == 404
    assert client.delete(f"/api/connections/{conn_id}").status_code == 204
    assert client.delete(f"/api/connections/{conn_id}").status_code == 404
    assert client.get("/api/connections").json() == []


def test_validation(client):
    assert client.post("/api/connections", json={**PAYLOAD, "port": 0}).status_code == 422
    assert client.post("/api/connections", json={**PAYLOAD, "engine": "mysql"}).status_code == 422


def test_test_connection(client, conn_id):
    assert client.post("/api/connections/test", json=PAYLOAD).json()["ok"] is True
    assert client.post(f"/api/connections/{conn_id}/test").json()["version"] == "Fake 1.0"
    FakeCollector.fail = "password authentication failed"
    r = client.post(f"/api/connections/{conn_id}/test")
    assert r.status_code == 200
    assert r.json() == {"ok": False, "message": "password authentication failed",
                        "version": None, "latency_ms": None}
    assert client.post("/api/connections/999/test").status_code == 404


def test_diagnostics_and_error_mapping(client, conn_id):
    base = f"/api/connections/{conn_id}"
    assert client.get(f"{base}/overview").json()["database"] == "shop"
    tq = client.get(f"{base}/top-queries?order_by=calls&limit=1").json()
    assert len(tq) == 1 and tq[0]["pct_total_time"] == 100.0
    assert client.get(f"{base}/top-queries?order_by=bogus").status_code == 422
    assert client.post(f"{base}/explain", json={"sql": "select 1"}).json()["plan_text"] == "Seq Scan"
    for path in ("active-queries", "tables", "indexes", "locks", "config", "recommendations"):
        assert client.get(f"{base}/{path}").status_code == 200, path
    assert all(i.closed for i in FakeCollector.instances)

    assert client.get("/api/connections/999/overview").status_code == 404
    FakeCollector.fail = "conexión rechazada"
    r = client.get(f"{base}/overview")
    assert r.status_code == 502 and r.json() == {"detail": "conexión rechazada"}


def test_driver_exception_maps_to_502(client, conn_id, monkeypatch):
    def boom(self):
        raise OSError("connection timed out")
    monkeypatch.setattr(FakeCollector, "locks", boom)
    r = client.get(f"/api/connections/{conn_id}/locks")
    assert r.status_code == 502 and r.json()["detail"] == "connection timed out"


def test_optimize_script(client, conn_id):
    r = client.post(f"/api/connections/{conn_id}/optimize/script",
                    json={"recommendation_ids": ["stale_stats:public.items",
                                                 "missing_index:public.orders", "unknown"]})
    body = r.json()
    assert r.status_code == 200 and body["engine"] == "postgres" and body["count"] == 2
    script = body["script"]
    assert "Revise antes de ejecutar" in script and "Motor: PostgreSQL" in script
    assert "Conexión: demo" in script
    assert script.index("ANALYZE public.items;") < script.index("CREATE INDEX CONCURRENTLY")
    assert "-- en orders" in script  # multi-line detail is commented
    for line in script.splitlines():
        assert line.startswith("--") or line == "" or line.endswith(";")
    empty = client.post(f"/api/connections/{conn_id}/optimize/script",
                        json={"recommendation_ids": []}).json()
    assert empty["count"] == 0


def test_monitor_lifecycle(client, conn_id):
    base = f"/api/connections/{conn_id}"
    st = client.get(f"{base}/monitor").json()
    assert st["running"] is False and st["samples"] == 0
    st = client.post(f"{base}/monitor", json={"interval_seconds": 1}).json()
    assert st["running"] is True and st["interval_seconds"] == 1
    assert client.get("/api/connections").json()[0]["monitoring"] is True
    time.sleep(3.3)
    st = client.get(f"{base}/monitor").json()
    assert 3 <= st["samples"] <= 5, st
    assert st["last_sample_at"] and st["last_error"] is None

    series = client.get(f"{base}/metrics?minutes=5").json()
    assert series["interval_seconds"] == 1
    pts = series["points"]
    assert pts[0]["commits_per_sec"] is None
    assert pts[-1]["commits_per_sec"] == pytest.approx(100, rel=0.35)

    top = client.get(f"{base}/metrics/top-queries?minutes=5").json()
    assert top and top[0]["query_id"] == "q1"

    st = client.delete(f"{base}/monitor").json()
    assert st["running"] is False and st["samples"] >= 3
    assert FakeCollector.instances[0].closed
    assert client.get("/api/connections").json()[0]["monitoring"] is False

    assert client.delete(f"{base}/metrics").status_code == 204
    assert client.get(f"{base}/metrics").json()["points"] == []
    assert client.post(f"{base}/monitor", json={"interval_seconds": 0.5}).status_code == 422
    assert client.get("/api/connections/999/monitor").status_code == 404


def test_monitor_keeps_running_on_errors_and_auto_stops(client, conn_id):
    base = f"/api/connections/{conn_id}"
    FakeCollector.fail = "sin privilegios"
    client.post(f"{base}/monitor", json={"interval_seconds": 1, "duration_minutes": 0.04})
    time.sleep(1.2)
    st = client.get(f"{base}/monitor").json()
    assert st["running"] is True and st["last_error"] == "sin privilegios"
    FakeCollector.fail = None
    time.sleep(1.8)  # duration 2.4 s
    st = client.get(f"{base}/monitor").json()
    assert st["running"] is False and st["samples"] >= 1


def test_delete_connection_stops_monitor(client, conn_id):
    client.post(f"/api/connections/{conn_id}/monitor", json={"interval_seconds": 1})
    time.sleep(0.3)
    assert client.delete(f"/api/connections/{conn_id}").status_code == 204
    from app.sampler import manager
    assert not manager.is_running(conn_id)


def test_monitor_resumes_after_restart(client, conn_id):
    from app.main import app
    client.post(f"/api/connections/{conn_id}/monitor", json={"interval_seconds": 1})
    client.__exit__(None, None, None)  # shutdown keeps the session active
    with TestClient(app) as c2:
        assert c2.get(f"/api/connections/{conn_id}/monitor").json()["running"] is True
