# DB Monitor — Design

## Intent
A service is slow. The user needs a web tool, easy to use, to (a) diagnose a PostgreSQL or Oracle
database on demand — which queries take longest and *why* — and (b) monitor it over time with a
sampling interval from seconds up to minutes, showing when things degrade. It must also generate SQL
to optimize the database (create/drop indexes, refresh statistics, etc.).

## Goals
- Engines: PostgreSQL (12+) and Oracle (12.2+, incl. 23ai Free).
- Connections configured from the UI (stored in the tool's own SQLite DB, passwords encrypted).
- Two UI areas: **Gráficos** (dashboard with charts) and **Técnico** (tables, plans, indexes, locks, config).
- **Optimización**: recommendations + generated SQL script (copy/download).
- Sampler: per-connection monitoring, interval 1 s … 3600 s, optional duration, history in SQLite.
- Runs in Docker; `docker-compose.yml` included (with optional demo Postgres and Oracle profiles).
- UI in Spanish.

## Non-goals / anti-goals
- **The tool never executes DDL/DML on the monitored DB.** It is read-only; it only *generates* SQL.
  (EXPLAIN ANALYZE is opt-in and allowed only for SELECT statements.)
- No user auth / multi-tenant in v1 (local/internal tool).
- No dependency on Oracle Diagnostics/Tuning Pack (no AWR/ASH). Only V$ / DBA_ views.
- No Oracle Instant Client: use `python-oracledb` thin mode.

## Approaches considered
1. **FastAPI (Python) + React SPA, single container, SQLite** — chosen. python-oracledb thin and
   psycopg 3 are the best drivers for each engine; one image serves API + static UI.
2. Node/NestJS + React — oracledb for Node needs Instant Client for many features; heavier image.
3. Grafana + exporters — great charts, but no "why is this query slow" or SQL generation, and
   configuring DBs from the UI is clumsy. Rejected.

## Stack (builder's calls already made)
- Backend: Python 3.12, FastAPI, uvicorn, psycopg[binary] 3, oracledb (thin), sqlite3 stdlib,
  cryptography (Fernet). Managed with `uv` (`backend/pyproject.toml`). Tests: pytest.
- Blocking driver calls run via `asyncio.to_thread`. Each call opens a short-lived connection
  (simple, robust); the sampler keeps one connection per monitored target and reconnects on error.
- Frontend: React 18 + TypeScript + Vite, Tailwind CSS, Recharts, React Router, TanStack Query.
- Secret key: env `DBMON_SECRET_KEY` (Fernet key) or auto-generated into `/data/secret.key`.
- SQLite file: env `DBMON_DATA_DIR` (default `./data`), file `dbmon.sqlite`.
- Ports: app `8080`. Dev: vite `5173` proxies `/api` → `8080`. Demo Postgres host port `55432`,
  demo Oracle host port `1521`.

## Layout
```
backend/
  pyproject.toml
  app/
    main.py            # FastAPI app, mounts /api routers, serves frontend/dist at /
    schemas.py         # Pydantic models = API contract (shared, do not change shapes without need)
    store.py           # SQLite: connections, monitor sessions, samples, query snapshots
    crypto.py          # Fernet encrypt/decrypt for passwords
    sampler.py         # asyncio monitoring tasks
    api/connections.py, api/diagnostics.py, api/monitor.py, api/optimize.py
    collectors/
      base.py          # Collector ABC (contract)
      __init__.py      # get_collector(cfg) factory
      postgres.py
      oracle.py
  tests/
frontend/              # Vite app
docker/
  postgres-demo/       # init SQL: pg_stat_statements, demo schema with missing/unused indexes
  oracle-demo/         # init SQL for gvenzl/oracle-free: demo user + schema
  workload/            # script that generates slow queries against demo DBs
e2e/                   # Playwright UI tests
Dockerfile             # multi-stage: node build → python runtime
docker-compose.yml     # app + profiles: demo-postgres, demo-oracle
```

## API contract (all under /api, JSON; models in backend/app/schemas.py)
Connections
- `GET    /api/connections` → `ConnectionOut[]`
- `POST   /api/connections` (`ConnectionIn`) → `ConnectionOut`
- `PUT    /api/connections/{id}` (`ConnectionIn`, empty password = keep) → `ConnectionOut`
- `DELETE /api/connections/{id}` → 204
- `POST   /api/connections/test` (`ConnectionIn`) → `TestResult`
- `POST   /api/connections/{id}/test` → `TestResult`

Diagnostics (on demand, live)
- `GET  /api/connections/{id}/overview` → `Overview`
- `GET  /api/connections/{id}/active-queries` → `ActiveQuery[]`
- `GET  /api/connections/{id}/top-queries?order_by=total_time|mean_time|calls|io&limit=50` → `TopQuery[]`
- `POST /api/connections/{id}/explain` (`ExplainRequest`) → `ExplainResult`
- `GET  /api/connections/{id}/tables` → `TableStat[]`
- `GET  /api/connections/{id}/indexes` → `IndexStat[]`
- `GET  /api/connections/{id}/locks` → `LockInfo[]`
- `GET  /api/connections/{id}/config` → `ConfigParam[]`
- `GET  /api/connections/{id}/recommendations` → `Recommendation[]`
- `POST /api/connections/{id}/optimize/script` (`ScriptRequest`) → `ScriptResult`

Monitoring
- `GET    /api/connections/{id}/monitor` → `MonitorStatus`
- `POST   /api/connections/{id}/monitor` (`MonitorStart`) → `MonitorStatus`
- `DELETE /api/connections/{id}/monitor` → `MonitorStatus`
- `GET    /api/connections/{id}/metrics?minutes=15` (or `from`/`to` ISO) → `MetricSeries`
- `GET    /api/connections/{id}/metrics/top-queries?minutes=15` → `TopQuery[]` (deltas in window)
- `DELETE /api/connections/{id}/metrics` → 204 (clear history)

Errors: driver/connection errors → HTTP 502 `{"detail": "<message>"}`; unknown id → 404;
feature unavailable (e.g. pg_stat_statements missing) → 200 with empty list plus a
`Recommendation` of category `extension` explaining how to enable it, and `Overview.warnings`.

## Sampler
- One asyncio task per monitored connection. Each tick: `collector.sample()` → `MetricSample`
  stored in SQLite; every tick also stores `collector.top_queries(limit=25)` cumulative snapshot
  (only when interval ≥ 5 s, else every 5th tick) so the API can compute per-window deltas.
- Counters (`commits`, `rollbacks`, `physical_reads`, `logical_reads`, `deadlocks`) stored raw;
  `/metrics` returns per-second rates computed between consecutive samples (reset-safe: negative → 0).
- Retention: env `DBMON_RETENTION_HOURS` (default 24); pruned hourly.
- Optional `duration_minutes`; task stops itself when reached. Active monitors are resumed on startup.

## "Why is it slow" (explain + findings)
- Postgres: `EXPLAIN (FORMAT JSON)`; for normalized pg_stat_statements text with `$n` params use
  `EXPLAIN (GENERIC_PLAN, FORMAT JSON)` on PG16+, otherwise report that literal SQL is needed.
  `analyze=true` only if statement is a single SELECT; wrapped in a transaction that is rolled back,
  with `statement_timeout`.
- Oracle: `DBMS_XPLAN.DISPLAY_CURSOR(sql_id, NULL, 'TYPICAL')` for cursor-cache queries;
  `EXPLAIN PLAN FOR` + `DBMS_XPLAN.DISPLAY` for pasted SQL.
- Heuristic `Finding`s from the plan: sequential/full scan on big table with filter (→ suggest index
  on filtered columns), nested loop with large row estimates, sort/hash spilling, row estimate far
  from actual (stale stats), high disk reads vs buffer gets, lock waits.

## Recommendations (both engines; each carries ready SQL)
- `missing_index`: tables with many seq/full scans and large size; columns from plan filter predicates.
- `unused_index`: idx_scan = 0 since stats reset (PG) / not in DBA_INDEX_USAGE (Oracle), excluding PK/unique.
- `duplicate_index`: same table + same leading columns.
- `stale_stats`: last analyze old / many modifications → `ANALYZE` / `DBMS_STATS.GATHER_TABLE_STATS`.
- `bloat` (PG): high dead tuples → `VACUUM (ANALYZE)`.
- `config`: e.g. low cache hit ratio, too many idle connections.
- `extension`: pg_stat_statements not installed → `CREATE EXTENSION` + `shared_preload_libraries` note.
Generated DDL uses `CREATE INDEX CONCURRENTLY` (PG) / `ONLINE` (Oracle), with header comments.

## Required privileges (documented in README, shown in UI on failure)
- Postgres: role `pg_monitor` (+ pg_stat_statements in `shared_preload_libraries`).
- Oracle: `SELECT_CATALOG_ROLE` (or SELECT on V$SESSION, V$SQL, V$SQL_PLAN, V$SYSSTAT, V$LOCK,
  DBA_TABLES, DBA_INDEXES, DBA_IND_COLUMNS, DBA_INDEX_USAGE, DBA_SEGMENTS, V$PARAMETER).

## UI
- Left sidebar: connection selector + nav: Conexiones, Gráficos, Técnico, Optimización.
- **Conexiones**: list, add/edit form (engine toggle changes default port 5432/1521, "Base de datos" vs
  "Service name"), Probar conexión, delete.
- **Gráficos**: KPI cards (sesiones activas, TPS, cache hit %, tamaño DB, consulta más larga);
  monitor bar: interval presets 1s/5s/10s/30s/1m/5m + custom, duration, Iniciar/Detener, time
  window 5m/15m/1h/6h/24h; line charts: sesiones (activas/inactivas/bloqueadas), commits/s &
  rollbacks/s, lecturas lógicas vs físicas /s, cache hit %; bar chart of top queries by total time
  in the window. Without monitoring running, charts show an empty-state with a "Iniciar monitoreo" CTA.
- **Técnico** tabs: Consultas activas (auto-refresh), Top consultas (sortable; click → Explain drawer
  with plan + findings), Índices (filter unused/duplicate), Tablas, Bloqueos, Configuración.
- **Optimización**: recommendations grouped by severity with checkbox; "Generar script" →
  SQL viewer with Copiar / Descargar .sql.
- Elements that tests rely on carry `data-testid` attributes (see e2e).

## Verification
- pytest for store, crypto, rate computation, script generation, and collectors against the live
  demo containers (marked `integration`).
- Playwright e2e (run by a Sonnet agent) against the running app + demo Postgres; Oracle as well
  if the Oracle Free container starts on this machine (Podman VM has 2 GB RAM — may be tight).
