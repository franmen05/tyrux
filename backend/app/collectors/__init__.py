from app.collectors.base import Collector, CollectorError
from app.schemas import ConnectionConfig


def get_collector(cfg: ConnectionConfig) -> Collector:
    if cfg.engine == "postgres":
        from app.collectors.postgres import PostgresCollector
        return PostgresCollector(cfg)
    if cfg.engine == "oracle":
        from app.collectors.oracle import OracleCollector
        return OracleCollector(cfg)
    raise CollectorError(f"Motor no soportado: {cfg.engine}")


__all__ = ["Collector", "CollectorError", "get_collector"]
