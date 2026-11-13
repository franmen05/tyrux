# Tyrux · DB Monitor – Playwright E2E

UI tests against the running app (default `http://localhost:8099`) and the demo Postgres (connection id 1, monitor @ 2 s).

## Setup
```bash
cd e2e
npm install
npx playwright install chromium
```

## Run
```bash
npm test                         # all specs, headless chromium, serial (1 worker)
npx playwright test tests/charts.spec.ts
npx playwright test --headed --debug
E2E_ORACLE=1 npm test            # also runs tests/oracle.spec.ts (skipped otherwise)
npm run report                   # open the HTML report
```

## Environment variables
| var | default | meaning |
|---|---|---|
| `E2E_BASE_URL` | `http://localhost:8099` | app under test |
| `E2E_ORACLE` | unset | `1` enables `oracle.spec.ts` |
| `E2E_PG_HOST` / `E2E_PG_PORT` | `host.containers.internal` / `55433` | demo Postgres as seen **from the app container** |
| `E2E_ORACLE_HOST` / `E2E_ORACLE_PORT` | `host.containers.internal` / `1521` | demo Oracle (service `FREEPDB1`, `shop`/`shop`) |

## Notes
- Tests create connections named `e2e-<timestamp>-<n>` and delete them in `finally`; `global-setup.ts` also removes leftovers
  from crashed runs and makes sure the demo monitor (connection 1) runs at 2 s. Connection 1 is never deleted.
- Every test fails on `pageerror` or `console.error` (browser "Failed to load resource" lines for intentional 4xx/5xx are ignored).
- `visual.spec.ts` writes screenshots (light + dark) to `e2e/screenshots/` for manual review; it uses a tall viewport because
  the app scrolls inside `<main>`.
- Failures keep screenshot + trace in `test-results/`.
