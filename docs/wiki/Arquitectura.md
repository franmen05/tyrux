# Arquitectura

```
Navegador ──HTTP──▶ Tyrux (un contenedor)
                    ├── Frontend: React + Vite (servido como estático)
                    ├── API: FastAPI  /api/...
                    │     ├── Colector PostgreSQL (psycopg 3)
                    │     ├── Colector Oracle (python-oracledb, thick con Instant Client / thin)
                    │     └── Muestreador asíncrono (monitoreo continuo)
                    └── SQLite (WAL) en /data: conexiones cifradas + historial
                                │
                                ▼
                    Tus bases PostgreSQL / Oracle (solo lectura)
```

## Componentes

| Parte | Tecnología | Carpeta |
|---|---|---|
| API | Python 3.12, FastAPI, Pydantic | `backend/app/` |
| Colectores | psycopg 3, python-oracledb | `backend/app/collectors/` |
| Almacenamiento propio | SQLite en modo WAL | `backend/app/store.py` |
| Cifrado de contraseñas | Fernet (`cryptography`) | `backend/app/crypto.py` |
| Monitoreo continuo | asyncio (`MonitorManager`) | `backend/app/sampler.py` |
| Interfaz | React 18, TypeScript, Tailwind 4, Recharts, TanStack Query | `frontend/` |
| Pruebas de UI | Playwright | `e2e/` |
| Bases demo y carga | Postgres 17, Oracle 23ai Free, scripts Python | `docker/` |

## Colectores

Cada motor implementa la misma interfaz (`collectors/base.py`): `test_connection`, `overview`, `sample`, `active_queries`, `top_queries`, `explain`, `tables`, `indexes`, `locks`, `config` y `recommendations`. Añadir otro motor consiste en implementar esa clase y registrarla en `get_collector`.

Las consultas de Tyrux se identifican en la base (`/* dbmon */` en PostgreSQL, `module = 'dbmon'` en Oracle) y se excluyen de sus propios resultados.

## API

Todo bajo `/api`; la documentación interactiva de FastAPI está en `/docs`.

| Recurso | Endpoints |
|---|---|
| Conexiones | `GET/POST /connections`, `PUT/DELETE /connections/{id}`, `POST /connections/test`, `POST /connections/{id}/test` |
| Diagnóstico | `GET /connections/{id}/overview`, `active-queries`, `top-queries`, `tables`, `indexes`, `locks`, `config`, `recommendations`; `POST /connections/{id}/explain` |
| Optimización | `POST /connections/{id}/optimize/script` |
| Monitoreo | `GET/POST/DELETE /connections/{id}/monitor`, `GET /connections/{id}/metrics`, `GET /connections/{id}/metrics/top-queries`, `DELETE /connections/{id}/metrics` |

El diseño completo está en [`docs/superpowers/specs/`](https://github.com/franmen05/tyrux/tree/main/docs/superpowers/specs).
