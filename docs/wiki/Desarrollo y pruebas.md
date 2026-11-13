# Desarrollo y pruebas

## Backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8080
uv run pytest -q
```

Las pruebas de integración contra Postgres y Oracle se saltan solas si las bases demo no están levantadas.

## Frontend

```bash
cd frontend
npm install
npm run dev                                   # http://localhost:5173, proxy /api → :8080
DBMON_API=http://localhost:8099 npm run dev   # si el backend corre en otro puerto
```

Los `data-testid` que usan las pruebas están documentados en `frontend/TESTIDS.md`.

## Pruebas de UI (Playwright)

Necesitan la app corriendo y la base demo Postgres con carga:

```bash
docker compose --profile workload-pg up -d
cd e2e
npm install
npx playwright install chromium
E2E_BASE_URL=http://localhost:8080 npm test
E2E_ORACLE=1 npm test                         # incluye las pruebas de Oracle
```

Cubren navegación, conexiones, gráficos, sección técnica, optimización, manejo de errores, tema claro/oscuro y Oracle.

## Capturas de la documentación

Las imágenes de `docs/images/` (README y wiki) se regeneran con:

```bash
cd e2e
DOCS_SHOTS=1 E2E_ORACLE=1 npx playwright test docs-screenshots
```

## Imagen Docker

```bash
docker build -t tyrux .
```

Es multi-etapa: compila el frontend con Node 22 y lo sirve desde la imagen Python 3.12, que corre con un usuario sin privilegios.
