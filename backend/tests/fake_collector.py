"""In-memory Collector used by the store/API tests (no database needed)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.collectors.base import Collector, CollectorError
from app.schemas import (
    ConfigParam, ExplainResult, MetricSample, Overview, Recommendation, TestResult, TopQuery,
)


class FakeCollector(Collector):
    engine = "postgres"
    instances: list["FakeCollector"] = []
    fail: str | None = None  # set to a message to make every call raise CollectorError

    def __init__(self, cfg):
        super().__init__(cfg)
        self.ticks = 0
        self.closed = False
        FakeCollector.instances.append(self)

    def _check(self):
        if FakeCollector.fail:
            raise CollectorError(FakeCollector.fail)

    def close(self):
        self.closed = True

    def test_connection(self):
        self._check()
        return TestResult(ok=True, message="OK", version="Fake 1.0", latency_ms=1.0)

    def overview(self):
        self._check()
        return Overview(engine=self.cfg.engine, version="Fake 1.0", database=self.cfg.database)

    def sample(self):
        self._check()
        self.ticks += 1
        return MetricSample(ts=datetime.now(timezone.utc), sessions_active=2, sessions_total=5,
                            commits=100 * self.ticks, rollbacks=self.ticks)

    def active_queries(self):
        self._check()
        return []

    def top_queries(self, order_by="total_time", limit=50):
        self._check()
        n = self.ticks + 1
        return [
            TopQuery(query_id="q1", query="select 1", calls=10 * n,
                     total_time_ms=300.0 * n, mean_time_ms=30.0),
            TopQuery(query_id="q2", query="select 2", calls=n,
                     total_time_ms=100.0 * n, mean_time_ms=100.0),
        ][:limit]

    def explain(self, req):
        self._check()
        return ExplainResult(query=req.sql or "", plan_text="Seq Scan")

    def tables(self):
        return []

    def indexes(self):
        return []

    def locks(self):
        return []

    def config(self):
        return [ConfigParam(name="work_mem", value="4MB")]

    def recommendations(self):
        self._check()
        return [
            Recommendation(id="missing_index:public.orders", category="missing_index",
                           severity="high", title="Índice faltante en orders",
                           detail="Muchos seq scans\nen orders", object_name="public.orders",
                           sql="CREATE INDEX CONCURRENTLY orders_customer_idx ON public.orders (customer_id);"),
            Recommendation(id="stale_stats:public.items", category="stale_stats",
                           severity="medium", title="Estadísticas obsoletas",
                           detail="Muchas modificaciones", sql="ANALYZE public.items;"),
        ]
