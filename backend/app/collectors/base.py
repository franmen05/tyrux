"""Collector contract. One implementation per engine (postgres.py, oracle.py).

All methods are synchronous (drivers are blocking); the API layer calls them through
asyncio.to_thread. Collectors must be READ-ONLY on the monitored database: they never run
DDL/DML. EXPLAIN ANALYZE is only allowed for single SELECT statements, inside a rolled-back
transaction with a statement timeout.

Raise CollectorError for user-facing failures (bad credentials, missing privileges...).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from app.schemas import (
    ActiveQuery, ConfigParam, ConnectionConfig, ExplainRequest, ExplainResult, IndexStat,
    LockInfo, MetricSample, Overview, Recommendation, TableStat, TestResult, TopQuery,
)

OrderBy = Literal["total_time", "mean_time", "calls", "io"]


class CollectorError(Exception):
    """User-facing error (mapped to HTTP 502 with the message)."""


class Collector(ABC):
    engine: str

    def __init__(self, cfg: ConnectionConfig):
        self.cfg = cfg

    # lifecycle — the sampler keeps one collector alive and calls sample() repeatedly;
    # implementations may cache a connection and must reconnect transparently on failure.
    def close(self) -> None:  # pragma: no cover - default no-op
        pass

    @abstractmethod
    def test_connection(self) -> TestResult: ...

    @abstractmethod
    def overview(self) -> Overview: ...

    @abstractmethod
    def sample(self) -> MetricSample: ...

    @abstractmethod
    def active_queries(self) -> list[ActiveQuery]: ...

    @abstractmethod
    def top_queries(self, order_by: OrderBy = "total_time", limit: int = 50) -> list[TopQuery]:
        """Cumulative statistics since stats reset. Empty list if unavailable."""

    @abstractmethod
    def explain(self, req: ExplainRequest) -> ExplainResult: ...

    @abstractmethod
    def tables(self) -> list[TableStat]: ...

    @abstractmethod
    def indexes(self) -> list[IndexStat]: ...

    @abstractmethod
    def locks(self) -> list[LockInfo]: ...

    @abstractmethod
    def config(self) -> list[ConfigParam]: ...

    @abstractmethod
    def recommendations(self) -> list[Recommendation]:
        """Each Recommendation carries ready-to-run SQL in .sql and a stable .id."""
