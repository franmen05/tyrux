"""Background monitoring: one asyncio task per monitored connection."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app import collectors, store
from app.schemas import MonitorStatus

log = logging.getLogger("dbmon.sampler")

SNAPSHOT_LIMIT = 25
PRUNE_EVERY_SECONDS = 3600


@dataclass
class _Monitor:
    connection_id: int
    interval: float
    started_at: datetime
    ends_at: Optional[datetime]
    last_error: Optional[str] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MonitorManager:
    def __init__(self) -> None:
        self._monitors: dict[int, _Monitor] = {}
        self._prune_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ lifecycle
    async def startup(self) -> None:
        await asyncio.to_thread(store.init_db)
        for s in await asyncio.to_thread(store.active_sessions):
            if s.ends_at and s.ends_at <= _now():
                await asyncio.to_thread(store.end_session, s.connection_id)
            elif await asyncio.to_thread(store.get_connection, s.connection_id):
                self._spawn(_Monitor(s.connection_id, s.interval_seconds, s.started_at, s.ends_at))
                log.info("resumed monitor for connection %s", s.connection_id)
        self._prune_task = asyncio.create_task(self._prune_loop())

    async def shutdown(self) -> None:
        """Cancel all tasks but keep sessions active so they resume on next startup."""
        tasks = [m.task for m in self._monitors.values() if m.task]
        if self._prune_task:
            tasks.append(self._prune_task)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._monitors.clear()
        self._prune_task = None

    # ------------------------------------------------------------ control
    def is_running(self, connection_id: int) -> bool:
        return connection_id in self._monitors

    async def start(self, connection_id: int, interval_seconds: float,
                    duration_minutes: Optional[float] = None,
                    ends_at: Optional[datetime] = None) -> MonitorStatus:
        await self._cancel(connection_id)
        started = _now()
        if duration_minutes:
            ends_at = started + timedelta(minutes=duration_minutes)
        await asyncio.to_thread(store.save_session, connection_id, interval_seconds, started,
                                ends_at)
        self._spawn(_Monitor(connection_id, interval_seconds, started, ends_at))
        return await self.status(connection_id)

    async def stop(self, connection_id: int) -> MonitorStatus:
        await self._cancel(connection_id)
        await asyncio.to_thread(store.end_session, connection_id)
        return await self.status(connection_id)

    async def restart(self, connection_id: int) -> None:
        """Restart a running monitor (e.g. after its connection settings changed)."""
        mon = self._monitors.get(connection_id)
        if mon:
            await self.start(connection_id, mon.interval, ends_at=mon.ends_at)

    async def status(self, connection_id: int) -> MonitorStatus:
        mon = self._monitors.get(connection_id)
        if mon:
            interval, started, ends, error = mon.interval, mon.started_at, mon.ends_at, mon.last_error
        else:
            s = await asyncio.to_thread(store.get_session, connection_id)
            interval, started, ends, error = (
                (s.interval_seconds, s.started_at, s.ends_at, None) if s else (None, None, None, None)
            )
        samples = await asyncio.to_thread(store.count_samples, connection_id, started)
        last = await asyncio.to_thread(store.last_sample_at, connection_id)
        return MonitorStatus(
            connection_id=connection_id, running=mon is not None, interval_seconds=interval,
            started_at=started, ends_at=ends, samples=samples if started else 0,
            last_sample_at=last, last_error=error,
        )

    # ------------------------------------------------------------ internals
    def _spawn(self, mon: _Monitor) -> None:
        self._monitors[mon.connection_id] = mon
        mon.task = asyncio.create_task(self._run(mon), name=f"monitor-{mon.connection_id}")

    async def _cancel(self, connection_id: int) -> None:
        mon = self._monitors.pop(connection_id, None)
        if mon and mon.task:
            mon.task.cancel()
            await asyncio.gather(mon.task, return_exceptions=True)

    async def _run(self, mon: _Monitor) -> None:
        loop = asyncio.get_running_loop()
        snapshot_every = 1 if mon.interval >= 5 else 5
        collector = None
        tick = 0
        next_at = loop.time()
        try:
            while not (mon.ends_at and _now() >= mon.ends_at):
                try:
                    if collector is None:
                        cfg = await asyncio.to_thread(store.get_config, mon.connection_id)
                        if cfg is None:
                            break  # connection deleted
                        collector = collectors.get_collector(cfg)
                    sample = await asyncio.to_thread(collector.sample)
                    await asyncio.to_thread(store.insert_sample, mon.connection_id, sample)
                    if tick % snapshot_every == 0:
                        queries = await asyncio.to_thread(collector.top_queries, "total_time",
                                                          SNAPSHOT_LIMIT)
                        await asyncio.to_thread(store.insert_snapshot, mon.connection_id,
                                                sample.ts, queries)
                    mon.last_error = None
                except Exception as exc:  # keep sampling; surface the error in status
                    mon.last_error = str(exc) or type(exc).__name__
                    log.warning("monitor %s: %s", mon.connection_id, mon.last_error)
                tick += 1
                # Fixed cadence: schedule from the previous target, not from "now",
                # so collection time does not stretch the interval. Skip missed ticks.
                next_at += mon.interval
                if next_at < loop.time():
                    next_at = loop.time()
                delay = next_at - loop.time()
                if mon.ends_at:
                    delay = min(delay, max(0.0, (mon.ends_at - _now()).total_seconds()))
                await asyncio.sleep(delay)
            # natural end (duration reached or connection deleted)
            if self._monitors.get(mon.connection_id) is mon:
                del self._monitors[mon.connection_id]
                await asyncio.to_thread(store.end_session, mon.connection_id)
        finally:
            if collector is not None:
                try:
                    collector.close()
                except Exception:  # pragma: no cover
                    log.exception("closing collector")

    async def _prune_loop(self) -> None:
        while True:
            try:
                n = await asyncio.to_thread(store.prune)
                if n:
                    log.info("pruned %s old rows", n)
            except Exception:  # pragma: no cover
                log.exception("prune failed")
            await asyncio.sleep(PRUNE_EVERY_SECONDS)


manager = MonitorManager()
