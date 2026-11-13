# Limitaciones y preguntas frecuentes

## Limitaciones conocidas

- **Sin autenticación.** Tyrux no tiene usuarios ni login. No lo expongas a internet; ponlo detrás de una VPN o de un proxy con autenticación.
- **Oracle sin historial AWR/ASH.** Solo ve lo que hay en el cursor cache y en las vistas `V$` en este momento; el histórico es el que acumula el propio monitoreo de Tyrux.
- **Esperas en Oracle.** El análisis del plan no correlaciona el tiempo de espera: una consulta lenta por bloqueos puede mostrar un plan sin problemas. Revisa la pestaña **Bloqueos** y los eventos de espera en **Consultas activas**.
- **PostgreSQL necesita `pg_stat_statements`** para el top de consultas.
- **Sugerencias heurísticas.** Las recomendaciones de índices se deducen de los planes y las estadísticas; valídalas antes de aplicarlas.

## Preguntas frecuentes

**¿Puede Tyrux modificar mi base?**
No. Solo ejecuta consultas de lectura. El único caso que ejecuta una consulta tuya es `EXPLAIN ANALYZE`, que solo admite un `SELECT` y lo hace dentro de una transacción de solo lectura que se revierte.

**¿Cuánta carga añade el monitoreo?**
Cada muestra lanza unas pocas consultas ligeras sobre vistas de estadísticas. Con intervalos de 1–2 s es apreciable en bases muy cargadas; para vigilancia prolongada usa 30 s o más.

**«No se puede conectar» con `localhost`.**
Dentro del contenedor, `localhost` es el propio contenedor. Usa `host.docker.internal` (Docker) o `host.containers.internal` (Podman). Ver [[Instalación]].

**El top de consultas de PostgreSQL sale vacío.**
Falta `pg_stat_statements` o el usuario no tiene `pg_monitor`. Ver [[Permisos]].

**Las contraseñas guardadas dejaron de funcionar tras reinstalar.**
Se perdió la clave de cifrado del volumen. Define `DBMON_SECRET_KEY` fija y vuelve a guardar las contraseñas.

**Oracle no arranca en la demo.**
Oracle 23ai Free necesita unos 2–3 GB de RAM libres. Con Podman, aumenta la memoria de la máquina virtual (`podman machine set --memory 6144`).
