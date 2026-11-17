# Optimización

La sección **Optimización** junta todas las recomendaciones para la conexión activa, ordenadas por severidad (alta, media, baja, informativa).

![Recomendaciones](https://raw.githubusercontent.com/franmen05/tyrux/main/docs/images/optimize.png)

## Recomendaciones

| PostgreSQL | Oracle |
|---|---|
| Falta índice (deducido de consultas lentas y escaneos secuenciales) | Crear índice (deducido del cursor cache) |
| Índice sin uso | Índice sin uso (`DBA_INDEX_USAGE`) |
| Índice duplicado | Índice redundante |
| Estadísticas desactualizadas (`ANALYZE`) | Actualizar estadísticas (`DBMS_STATS`) |
| Filas muertas / bloat (`VACUUM`) | Consulta costosa |
| Muchos escaneos secuenciales | Acierto de buffer cache bajo |
| Cache hit bajo | Uso de procesos o sesiones cerca del límite |
| Sesiones «idle in transaction» | `statistics_level = BASIC` |
| Conexiones cerca del límite | `optimizer_index_cost_adj` modificado |
| Habilitar `pg_stat_statements` / `track_io_timing` | |

## Bases de datos grandes

- La lista se calcula una vez y el servidor la guarda **5 minutos** por conexión. Cambiar de página, filtrar o generar el script no vuelve a consultar la base de datos. **Recalcular** fuerza un nuevo análisis.
- La lista va **paginada** (25/50/100/200 por página) y se puede filtrar por severidad, categoría y texto.
- Cada comprobación (índices faltantes, índices duplicados o sin uso, tablas, configuración, consultas) se ejecuta por separado. Si una falla o supera el tiempo límite (`statement_timeout` en PostgreSQL, `call_timeout_ms` en Oracle; ambos 15 s por defecto y configurables en las opciones de la conexión), se muestra un aviso de **resultado parcial** y el resto de recomendaciones se presenta igualmente.
- El trabajo de cada comprobación tiene un límite. En PostgreSQL se analizan los planes de las 20 consultas principales durante un máximo de 20 s, y el tamaño de las tablas y los índices solo se calcula para los candidatos. En Oracle se analizan los 2.000 accesos `TABLE ACCESS FULL` más costosos del cursor cache.
- API: `GET /api/connections/{id}/recommendations/page?offset=0&limit=50&severity=&category=&search=&refresh=`.

## Generar el script

1. Marca las recomendaciones que quieras, en una o varias páginas. **Seleccionar todas** incluye todas las páginas que coinciden con los filtros, y después se pueden desmarcar algunas.
2. Pulsa **Generar script**.
3. **Copia** o **descarga** el `.sql`.

![Script generado](https://raw.githubusercontent.com/franmen05/tyrux/main/docs/images/optimize-script.png)

El script se genera pensando en producción:

- PostgreSQL: `CREATE INDEX CONCURRENTLY` / `DROP INDEX CONCURRENTLY`, que no bloquean escrituras (no pueden ir dentro de una transacción).
- Oracle: `CREATE INDEX ... ONLINE` y `DBMS_STATS.GATHER_TABLE_STATS`.

> Tyrux **nunca ejecuta** el script. Revísalo, pruébalo en un entorno de pruebas y aplícalo en una ventana adecuada. Las sugerencias de índices son heurísticas: valida con `EXPLAIN` que realmente se usan.
