# Instalación

## Requisitos

- Docker (o Podman con `docker compose`).
- Acceso de red desde el contenedor hasta tus bases de datos.

## Arranque rápido

```bash
git clone https://github.com/franmen05/tyrux.git
cd tyrux
docker compose up -d --build
```

Abre http://localhost:8080 y ve a **Conexiones → Agregar conexión**.

## Variables de entorno

Se pueden poner en el entorno o en un archivo `.env` junto a `docker-compose.yml`.

| Variable | Defecto | Uso |
|---|---|---|
| `DBMON_PORT` | `8080` | Puerto del host para la interfaz |
| `DBMON_SECRET_KEY` | autogenerada | Clave Fernet para cifrar las contraseñas guardadas. Si no se define, se genera una y se guarda en el volumen |
| `DBMON_RETENTION_HOURS` | `24` | Horas de historial de métricas que se conservan |
| `DBMON_LOG_LEVEL` | `INFO` | Nivel de log del backend |
| `DBMON_ORACLE_MODE` | `auto` | Modo de python-oracledb. `auto`: *thick* si se puede cargar Oracle Instant Client (la imagen Docker lo incluye), si no *thin*. `thick` / `thin` lo fuerzan |
| `DBMON_ORACLE_CLIENT_LIB_DIR` | `/opt/oracle/instantclient` (imagen) | Carpeta de Oracle Instant Client. Fuera de Docker, defínela si quieres el modo *thick* |
| `DEMO_PG_PORT` / `DEMO_ORA_PORT` | `55432` / `1521` | Puertos del host para las bases demo |

Si el puerto 8080 está ocupado:

```bash
DBMON_PORT=8099 docker compose up -d
```

> **Importante:** si pierdes la clave (volumen borrado sin `DBMON_SECRET_KEY` fija), las contraseñas guardadas ya no se pueden descifrar y habrá que volver a escribirlas. En producción define `DBMON_SECRET_KEY` explícitamente.
> Para generar una: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## Datos

Las conexiones y el historial de métricas se guardan en SQLite dentro del volumen `dbmon-data` (`/data` en el contenedor).

## Llegar a una base en tu máquina

Desde el contenedor, `localhost` es el propio contenedor. Para una base que corre en el host usa:

- `host.docker.internal` con Docker Desktop (en Linux el compose ya añade `host-gateway`);
- `host.containers.internal` con Podman.

## Bases de datos demo

El compose trae perfiles opcionales con bases sembradas con problemas (índices que faltan, índices sin uso o duplicados, filas muertas) y generadores de carga lenta:

```bash
docker compose --profile demo-postgres up -d   # Postgres 17 con pg_stat_statements
docker compose --profile demo-oracle up -d     # Oracle 23ai Free (≈2–3 GB de RAM libres)
docker compose --profile workload up -d        # ambas demos + generadores de carga
```

Conexiones de ejemplo dentro de la red de compose:

| Motor | Host | Puerto | Base / service | Usuario / clave |
|---|---|---|---|---|
| PostgreSQL | `demo-postgres` | 5432 | `shop` | `dbmon` / `dbmon` |
| Oracle | `demo-oracle` | 1521 | `FREEPDB1` | `shop` / `shop` |
