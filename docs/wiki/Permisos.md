# Permisos

Tyrux solo lee vistas de diagnóstico. Crea un usuario dedicado con estos permisos mínimos.

## PostgreSQL

```sql
CREATE USER tyrux WITH PASSWORD '...';
GRANT pg_monitor TO tyrux;
```

Para **Top consultas** hace falta la extensión `pg_stat_statements`:

```sql
-- postgresql.conf (requiere reinicio):
--   shared_preload_libraries = 'pg_stat_statements'
--   track_io_timing = on          -- opcional, para tiempos de I/O
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

Sin la extensión, el resto de Tyrux funciona y la sección de optimización recomienda habilitarla.

En servicios gestionados (RDS, Cloud SQL, Azure) `pg_stat_statements` suele estar disponible activándolo en el grupo de parámetros.

## Oracle (12.2+)

```sql
CREATE USER tyrux IDENTIFIED BY "...";
GRANT CREATE SESSION, SELECT_CATALOG_ROLE TO tyrux;
```

- No usa AWR ni ASH, así que **no requiere el Diagnostics Pack**.
- La imagen Docker incluye Oracle Instant Client y usa el modo *thick* de python-oracledb, que admite también usuarios con contraseñas antiguas (verificador 10G). Fuera de Docker, sin Instant Client, usa el modo *thin*, que no necesita nada instalado. El modo activo se ve en `GET /api/health` (`oracle_mode`).
- Si alguna vista del diccionario no es accesible, Tyrux lo indica como recomendación informativa y sigue con el resto.
