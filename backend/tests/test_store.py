import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import crypto, store
from app.schemas import ConnectionIn, MetricSample, TopQuery


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DBMON_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DBMON_SECRET_KEY", raising=False)
    return tmp_path


def conn_in(**kw):
    base = dict(name="pg", engine="postgres", host="localhost", port=5432, database="shop",
                username="dbmon", password="s3cret")
    return ConnectionIn(**{**base, **kw})


def test_secret_key_generated_and_reused(data_dir):
    token = crypto.encrypt("hola")
    key_file = data_dir / "secret.key"
    assert key_file.exists()
    assert key_file.stat().st_mode & 0o077 == 0
    crypto._cache.clear()
    assert crypto.decrypt(token) == "hola"


def test_secret_key_from_env(data_dir, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("DBMON_SECRET_KEY", Fernet.generate_key().decode())
    assert crypto.decrypt(crypto.encrypt("x")) == "x"
    assert not (data_dir / "secret.key").exists()


def test_connection_crud_and_encryption_at_rest(data_dir):
    c = store.create_connection(conn_in())
    assert c.id and c.name == "pg" and c.created_at.tzinfo is not None
    raw = sqlite3.connect(data_dir / "dbmon.sqlite").execute(
        "SELECT password_enc FROM connections").fetchone()[0]
    assert "s3cret" not in raw
    assert store.get_config(c.id).password == "s3cret"

    # empty password on update keeps the old one
    updated = store.update_connection(c.id, conn_in(name="pg2", password=""))
    assert updated.name == "pg2"
    assert store.get_config(c.id).password == "s3cret"
    store.update_connection(c.id, conn_in(password="nueva"))
    assert store.get_config(c.id).password == "nueva"

    assert [x.id for x in store.list_connections()] == [c.id]
    assert store.update_connection(999, conn_in()) is None
    assert store.get_config(999) is None
    assert store.delete_connection(c.id)
    assert not store.delete_connection(c.id)
    assert store.list_connections() == []


def test_wal_mode(data_dir):
    store.init_db()
    mode = sqlite3.connect(data_dir / "dbmon.sqlite").execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_samples_snapshots_and_delete_cascade():
    c = store.create_connection(conn_in())
    now = datetime.now(timezone.utc)
    for i in range(3):
        store.insert_sample(c.id, MetricSample(ts=now - timedelta(seconds=10 - i), commits=i,
                                               cache_hit_ratio=99.5, sessions_active=i))
    samples = store.get_samples(c.id, now - timedelta(seconds=9), now)
    assert [s.commits for s in samples] == [1, 2]
    assert isinstance(samples[0].sessions_active, int)
    assert samples[0].cache_hit_ratio == 99.5
    with_prev = store.get_samples(c.id, now - timedelta(seconds=9), now, include_previous=True)
    assert [s.commits for s in with_prev] == [0, 1, 2]
    assert store.count_samples(c.id) == 3
    assert store.last_sample_at(c.id) == samples[-1].ts

    store.insert_snapshot(c.id, now, [TopQuery(query_id="a", query="q", calls=3,
                                                total_time_ms=1.5, username="u")])
    snaps = store.get_snapshots(c.id, now - timedelta(minutes=1), now)
    assert snaps[0][1].calls == 3 and snaps[0][1].username == "u"

    store.save_session(c.id, 5, now, None)
    assert [s.connection_id for s in store.active_sessions()] == [c.id]
    store.end_session(c.id)
    assert store.active_sessions() == [] and store.get_session(c.id).interval_seconds == 5

    store.delete_connection(c.id)
    assert store.count_samples(c.id) == 0
    assert store.get_snapshots(c.id, now - timedelta(minutes=1), now) == []
    assert store.get_session(c.id) is None


def test_prune_and_clear(monkeypatch):
    c = store.create_connection(conn_in())
    now = datetime.now(timezone.utc)
    store.insert_sample(c.id, MetricSample(ts=now - timedelta(hours=30)))
    store.insert_sample(c.id, MetricSample(ts=now))
    store.insert_snapshot(c.id, now - timedelta(hours=30), [TopQuery(query_id="a", query="q")])
    monkeypatch.setenv("DBMON_RETENTION_HOURS", "24")
    assert store.prune() == 2
    assert store.count_samples(c.id) == 1
    store.clear_history(c.id)
    assert store.count_samples(c.id) == 0
