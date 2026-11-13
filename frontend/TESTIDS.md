# data-testid reference

Routes: `/connections`, `/charts`, `/technical`, `/optimize` (SPA, BrowserRouter). `/` redirects to `/charts`;
pages other than Conexiones show an empty-state with `goto-connections` when no connection exists.

## Global (sidebar / layout)
| testid | Notes |
|---|---|
| `nav-connections`, `nav-charts`, `nav-technical`, `nav-optimize` | Sidebar links |
| `connection-select` | `<select>` of connections (value = id). Persisted in localStorage `dbmonitor.connectionId` |
| `app-title` / `app-subtitle` | Sidebar brand: "Tyrux" / "DB Monitor" |
| `engine-badge` | Text "PostgreSQL" / "Oracle" for the selected connection |
| `monitoring-indicator` | Only present when selected connection `monitoring` is true ("Monitoreando"); refreshed every 15 s and on start/stop |
| `error-banner` | Any error state (backend `detail` text inside); has `retry-btn` button when retry is possible |
| `empty-state` | Generic empty state (no connections, no points, no locks, no data...) |
| `loading` | Spinner / skeleton wrapper while loading |
| `toast`, `toast-container` | Toasts (copy, saved, deleted...) |
| `goto-connections` | Button in "Selecciona una conexión" empty state |

## Conexiones
| testid | Notes |
|---|---|
| `add-connection-btn` | Opens form modal |
| `empty-add-btn` | CTA inside empty state |
| `connection-row` | One per connection (card). Contains `connection-name` |
| `conn-select-btn` | Per row ("Usar"), only on non-selected rows |
| `conn-edit-btn`, `conn-row-test-btn`, `conn-delete-btn` | Per-row actions (scope with `connection-row`) |
| `conn-row-test-result` | Per-row test result line |
| `connection-form` | Modal dialog |
| `conn-engine-postgres`, `conn-engine-oracle` | Engine toggle buttons (`aria-pressed`). Port auto-swaps 5432/1521 if untouched; label "Base de datos"/"Service name" |
| `conn-name`, `conn-host`, `conn-port`, `conn-database`, `conn-username`, `conn-password` | Inputs (password placeholder "sin cambios" on edit) |
| `conn-ssl` | Checkbox |
| `conn-test-btn` | "Probar conexión" (POST /connections/test) |
| `conn-test-result` | Result box: ok/error + message + version + latency |
| `conn-save-btn` | "Guardar" |
| `confirm-dialog`, `confirm-accept`, `confirm-cancel` | Delete confirmation |

## Gráficos
| testid | Notes |
|---|---|
| `kpi-sessions`, `kpi-cache`, `kpi-size`, `kpi-longest`, `kpi-maxconn`, `kpi-version` | KPI cards; `kpi-*-value` is the value element (e.g. `kpi-sessions-value` = "3 / 10") |
| `overview-warnings` | Warning banner (only when `warnings` non-empty) |
| `monitor-bar` | Container |
| `monitor-interval-1`, `-5`, `-10`, `-30`, `-60`, `-300` | Preset buttons (seconds; 60 = "1 min", 300 = "5 min"), `aria-pressed` |
| `monitor-interval-custom` | Custom seconds input (1-3600); overrides preset when filled |
| `monitor-duration` | Duration in minutes (optional) |
| `monitor-start-btn`, `monitor-stop-btn` | Iniciar / Detener (disabled when state doesn't allow) |
| `monitor-status` | Status text; attribute `data-running="true|false"` |
| `monitor-last-error` | Shown when `last_error` is set |
| `window-5`, `window-15`, `window-60`, `window-360`, `window-1440` | Time window (minutes) buttons |
| `clear-metrics-btn` | "Borrar historial" (window.confirm dialog) |
| `chart-sessions`, `chart-tps`, `chart-reads`, `chart-cache`, `chart-longest` | Chart cards (only rendered when there are points) |
| `chart-top-queries` | Top-10 card (always rendered; `top-queries-empty` inside when no data) |
| `empty-start-btn` | CTA in the "Inicia el monitoreo para ver la evolución" empty state (focuses the start button) |

## Técnico
| testid | Notes |
|---|---|
| `tab-active`, `tab-top`, `tab-indexes`, `tab-tables`, `tab-locks`, `tab-config` | Tabs (`aria-selected`) |
| `table-filter` | Text filter input inside every DataTable; headers are `th-<column key>` (click to sort) |
| `active-query-row`, `active-duration`, `blocked-by-badge`, `active-auto-refresh`, `active-refresh-btn` | Consultas activas |
| `top-query-row` | Row click opens the Explain drawer |
| `top-order` | `<select>` values total_time / mean_time / calls / io |
| `analyze-sql-btn` | Opens drawer in free-SQL mode |
| `explain-drawer` | Drawer; `drawer-close` closes it |
| `explain-sql-input` | Textarea (free mode only) |
| `explain-query` | Query text (row mode) |
| `explain-analyze` | EXPLAIN ANALYZE checkbox |
| `explain-run-btn` | "Analizar" |
| `explain-findings` | Findings section; items `explain-finding` (`data-severity`), `finding-copy-btn`, `explain-no-findings` |
| `explain-plan` | `<pre>` with plan_text |
| `index-filter-all`, `index-filter-unused`, `index-filter-duplicate` | Index filter buttons |
| `index-row` | Index row |
| `table-row` | Table row (Tablas tab) |
| `lock-row` | Lock pair (Bloqueos tab); empty state "Sin bloqueos" uses `empty-state` |
| `config-row` | Config parameter row |

## Optimización
| testid | Notes |
|---|---|
| `no-exec-notice` | "La herramienta no ejecuta cambios; revisa el script antes de aplicarlo." |
| `rec-group-high/medium/low/info` | Severity groups (only rendered when non-empty) |
| `recommendation-item` | Card; attrs `data-rec-id`, `data-severity` |
| `rec-checkbox` | Per item (disabled if the rec has no SQL) |
| `rec-sql-toggle`, `rec-sql` | Collapsible SQL |
| `select-all-recs` | Select all |
| `selected-count` | "N de M seleccionadas" |
| `generate-script-btn` | Disabled until something is selected |
| `script-output` | `<pre>` with generated script |
| `copy-script-btn`, `download-script-btn` | Copiar / Descargar .sql (`optimizacion-<engine>.sql`) |
