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

**Oracle: `DPY-3015: password verifier type 0x939 is not supported by python-oracledb in thin mode`.**
El usuario de Oracle tiene la contraseña guardada solo con el verificador antiguo 10G, que el modo *thin* no admite. Soluciones:
- Usa la imagen Docker de Tyrux: incluye Oracle Instant Client y se conecta en modo *thick*, que sí lo admite. Si ejecutas el backend fuera de Docker, instala [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client.html) y define `DBMON_ORACLE_CLIENT_LIB_DIR` con su carpeta.
- O pide al DBA que regenere la contraseña con un verificador moderno: comprobar que `sec_case_sensitive_logon` no sea `FALSE` (hasta 19c) y ejecutar `ALTER USER <usuario> IDENTIFIED BY <contraseña>;`.

**Tengo una cadena JDBC (`jdbc:oracle:thin:@host:1521/SERVICIO`).**
Rellena el formulario con sus partes: *Host* = `host`, *Puerto* = `1521`, *Service name* = `SERVICIO`.

**Oracle no arranca en la demo.**
Oracle 23ai Free necesita unos 2–3 GB de RAM libres. Con Podman, aumenta la memoria de la máquina virtual (`podman machine set --memory 6144`).
