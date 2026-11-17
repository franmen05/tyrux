"""Compare the indexes of two databases and generate the DDL to copy them (pure logic, no I/O).

Matching ignores schema names (they usually differ between environments):
1. same table + same ordered column list + same uniqueness (case-insensitive) -> ``same``;
2. otherwise same index name (case-insensitive) -> ``different``;
3. leftovers in the source -> ``missing``; leftovers in the target -> ``extra``.

DDL is generated in the TARGET engine's dialect from table/columns/uniqueness, so copying between
PostgreSQL and Oracle works for plain column indexes. Indexes that columns cannot express
(expressions, partial, INCLUDE, non-btree) are adapted from the source definition when both engines
are the same, and flagged as not copiable otherwise. Tyrux never executes the generated SQL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.collectors.oracle import q as ora_quote
from app.collectors.postgres import quote_ident as pg_quote
from app.schemas import ConnectionConfig, Engine, IndexCompareItem, IndexStat, TableStat

ENGINE_NAMES = {"postgres": "PostgreSQL", "oracle": "Oracle"}
_MAX_NAME = {"postgres": 63, "oracle": 128}
_RULE = "-- " + "=" * 76
_STATUS_LABEL = {"missing": "falta en destino", "different": "distinto en destino",
                 "extra": "solo en destino", "same": "igual"}

_PG_PLAIN = re.compile(r"^[a-z_][a-z0-9_$]*$")
_PG_QUOTED = re.compile(r'^"((?:[^"]|"")+)"$')
_ORA_PLAIN = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")
_ORDER = re.compile(r"\s+(ASC|DESC)$", re.IGNORECASE)
_IDENT = r'(?:"(?:[^"]|"")+"|[^\s."(]+)'
_QUALIFIED = rf"{_IDENT}(?:\.{_IDENT})?"


# ------------------------------------------------------------------ identifiers
@dataclass(frozen=True)
class Ident:
    """A database identifier as stored in the catalog of ``engine`` (unquoted, original case)."""
    name: str
    engine: Engine

    @property
    def case_sensitive(self) -> bool:
        # A name in the engine's default case was created unquoted: it can be re-cased for the target.
        return self.name != (self.name.lower() if self.engine == "postgres" else self.name.upper())

    def render(self, target: Engine) -> str:
        if target == "postgres":
            return pg_quote(self.name if self.case_sensitive else self.name.lower())
        return ora_quote(self.name if self.case_sensitive else self.name.upper())


@dataclass(frozen=True)
class Column:
    ident: Optional[Ident]   # None for expressions
    expr: Optional[str]
    desc: bool

    @property
    def key(self) -> str:
        base = self.ident.name.lower() if self.ident else re.sub(r'[\s"]', "", self.expr or "").lower()
        return base + (" desc" if self.desc else "")

    def render(self, target: Engine) -> str:
        text = self.ident.render(target) if self.ident else (self.expr or "")
        return text + (" DESC" if self.desc else "")


def parse_column(raw: str, engine: Engine) -> Column:
    """``raw`` is an ``IndexStat.columns`` entry: PG = pg_get_indexdef(col) (quoted when needed);
    Oracle = DBA_IND_COLUMNS.COLUMN_NAME (raw, optional " DESC") or a column expression."""
    s = (raw or "").strip()
    desc = False
    m = _ORDER.search(s)
    if m:
        desc = m.group(1).upper() == "DESC"
        s = s[:m.start()].rstrip()
    if engine == "postgres":
        if _PG_PLAIN.match(s):
            return Column(Ident(s, engine), None, desc)
        m = _PG_QUOTED.match(s)
        if m:
            return Column(Ident(m.group(1).replace('""', '"'), engine), None, desc)
    elif _ORA_PLAIN.match(s):
        return Column(Ident(s, engine), None, desc)
    return Column(None, s, desc)


# ------------------------------------------------------------------ index model
@dataclass
class _Idx:
    stat: IndexStat
    engine: Engine
    columns: list[Column]

    @property
    def table_key(self) -> str:
        return self.stat.table.lower()

    @property
    def name_key(self) -> str:
        return self.stat.index.lower()

    @property
    def signature(self) -> tuple:
        return (self.table_key, tuple(c.key for c in self.columns), bool(self.stat.is_unique))

    def special(self) -> list[str]:
        """Reasons why the column list alone does not describe this index."""
        reasons = []
        if any(c.ident is None for c in self.columns):
            reasons.append("expresiones")
        d = self.stat.definition or ""
        if self.engine == "postgres":
            m = re.search(r"\sUSING\s+(\w+)\s", d)
            if m and m.group(1).lower() != "btree":
                reasons.append(f"método {m.group(1)}")
            if re.search(r"\)\s+INCLUDE\s+\(", d):
                reasons.append("INCLUDE")
            if re.search(r"\)\s+WHERE\s", d):
                reasons.append("parcial (WHERE)")
        elif re.match(r"\s*CREATE\s+BITMAP\s", d, re.IGNORECASE):
            reasons.append("BITMAP")
        return reasons


def _wrap(stats: list[IndexStat], engine: Engine) -> list[_Idx]:
    return [_Idx(s, engine, [parse_column(c, engine) for c in s.columns]) for s in stats]


def _cols_text(idx: _Idx) -> str:
    return ", ".join(idx.stat.columns)


def _unique_text(unique: bool) -> str:
    return "única" if unique else "no única"


# ------------------------------------------------------------------ DDL
@dataclass
class _Target:
    engine: Engine
    schema: str            # raw catalog name in the target engine
    table: Ident
    table_exists: bool


def _resolve_target(idx: _Idx, target_engine: Engine, tables: dict[str, list[TableStat]],
                    default_schema: str) -> _Target:
    found = tables.get(idx.table_key) or []
    if found:
        best = next((t for t in found if t.schema_name.lower() == idx.stat.schema_name.lower()), found[0])
        return _Target(target_engine, best.schema_name, Ident(best.table, target_engine), True)
    schema = idx.stat.schema_name if idx.engine == target_engine else default_schema
    return _Target(target_engine, schema, Ident(idx.stat.table, idx.engine), False)


def _index_name(idx: _Idx, target_engine: Engine) -> str:
    ident = Ident(idx.stat.index, idx.engine)
    name = ident.name if ident.case_sensitive else (
        ident.name.lower() if target_engine == "postgres" else ident.name.upper())
    return name[:_MAX_NAME[target_engine]]


def _schema_sql(t: _Target) -> str:
    return Ident(t.schema, t.engine).render(t.engine)


def build_ddl(idx: _Idx, t: _Target) -> tuple[Optional[str], Optional[str]]:
    """Return ``(sql, note)``; ``sql`` is None when the index cannot be generated for the target."""
    name = _index_name(idx, t.engine)
    name_sql = pg_quote(name) if t.engine == "postgres" else ora_quote(name)
    table_sql = f"{_schema_sql(t)}.{t.table.render(t.engine)}"
    unique = "UNIQUE " if idx.stat.is_unique else ""
    reasons = idx.special()
    note = None

    if reasons and idx.engine == t.engine and idx.stat.definition:
        # Same engine: keep the original definition, only retarget name/table and add ONLINE/CONCURRENTLY.
        d = idx.stat.definition.strip().rstrip(";")
        head = re.compile(rf"^CREATE\s+(UNIQUE\s+|BITMAP\s+)?INDEX\s+{_QUALIFIED}\s+ON\s+(?:ONLY\s+)?{_QUALIFIED}",
                          re.IGNORECASE)
        m = head.match(d)
        if not m:
            return None, "No se pudo adaptar la definición original del índice."
        kind = (m.group(1) or "").strip().upper()
        kind = f"{kind} " if kind else ""
        rest = d[m.end():]
        if t.engine == "postgres":
            return (f"CREATE {kind}INDEX CONCURRENTLY IF NOT EXISTS {name_sql} ON {table_sql}{rest};",
                    f"Definición original adaptada ({', '.join(reasons)}).")
        if t.engine == "oracle":
            return (f"CREATE {kind}INDEX {_schema_sql(t)}.{name_sql} ON {table_sql}{rest} ONLINE;",
                    f"Definición original adaptada ({', '.join(reasons)}).")

    if reasons:
        if reasons == ["BITMAP"] and t.engine == "postgres":
            note = "BITMAP no existe en PostgreSQL: se genera un índice B-tree."
        else:
            return None, (f"No traducible automáticamente a {ENGINE_NAMES[t.engine]} "
                          f"({', '.join(reasons)}).")

    cols = ", ".join(c.render(t.engine) for c in idx.columns)
    if not cols:
        return None, "El índice no tiene columnas conocidas."
    if t.engine == "postgres":
        return f"CREATE {unique}INDEX CONCURRENTLY IF NOT EXISTS {name_sql} ON {table_sql} ({cols});", note
    return f"CREATE {unique}INDEX {_schema_sql(t)}.{name_sql} ON {table_sql} ({cols}) ONLINE;", note


def _drop_sql(stat: IndexStat, engine: Engine) -> str:
    name = f"{Ident(stat.schema_name, engine).render(engine)}.{Ident(stat.index, engine).render(engine)}"
    return f"DROP INDEX CONCURRENTLY IF EXISTS {name};" if engine == "postgres" else f"DROP INDEX {name};"


# ------------------------------------------------------------------ comparison
def compare_indexes(source: list[IndexStat], target: list[IndexStat], *,
                    source_engine: Engine, target_engine: Engine,
                    target_tables: list[TableStat], target_default_schema: str) -> list[IndexCompareItem]:
    src = _wrap(source, source_engine)
    dst = _wrap(target, target_engine)
    tables: dict[str, list[TableStat]] = {}
    for t in target_tables:
        tables.setdefault(t.table.lower(), []).append(t)
    for i in dst:   # a table with indexes exists even if the tables listing filtered it out
        if not any(t.schema_name == i.stat.schema_name for t in tables.get(i.table_key, [])):
            tables.setdefault(i.table_key, []).append(TableStat(schema_name=i.stat.schema_name, table=i.stat.table))

    remaining = list(dst)
    pairs: list[tuple[str, _Idx, Optional[_Idx]]] = []
    unmatched: list[_Idx] = []
    for s in src:
        hit = next((d for d in remaining if d.signature == s.signature and d.name_key == s.name_key), None) \
            or next((d for d in remaining if d.signature == s.signature), None)
        if hit:
            remaining.remove(hit)
            pairs.append(("same", s, hit))
        else:
            unmatched.append(s)
    for s in unmatched:
        hit = next((d for d in remaining if d.name_key == s.name_key), None)
        if hit:
            remaining.remove(hit)
            pairs.append(("different", s, hit))
        else:
            pairs.append(("missing", s, None))

    items: list[IndexCompareItem] = []
    for status, s, d in pairs:
        item = IndexCompareItem(
            id=f"src:{s.stat.schema_name}.{s.stat.index}", status=status, table=s.stat.table,
            source_schema=s.stat.schema_name, source_index=s.stat.index, source_columns=s.stat.columns,
            source_unique=s.stat.is_unique, is_primary=s.stat.is_primary,
            target_schema=d.stat.schema_name if d else None, target_index=d.stat.index if d else None,
            target_columns=d.stat.columns if d else [], target_unique=d.stat.is_unique if d else None)
        notes: list[str] = []
        if status == "same":
            if d.name_key != s.name_key:
                notes.append(f"Existe en destino con otro nombre: {d.stat.index}.")
        else:
            if status == "different":
                if d.table_key != s.table_key:
                    notes.append(f"En destino pertenece a otra tabla: {d.stat.table}.")
                if [c.key for c in d.columns] != [c.key for c in s.columns]:
                    notes.append(f"Columnas: ({_cols_text(s)}) en origen vs ({_cols_text(d)}) en destino.")
                if bool(d.stat.is_unique) != bool(s.stat.is_unique):
                    notes.append(f"Unicidad: {_unique_text(s.stat.is_unique)} en origen vs "
                                 f"{_unique_text(d.stat.is_unique)} en destino.")
            tgt = _resolve_target(s, target_engine, tables, target_default_schema)
            item.target_schema = item.target_schema or tgt.schema
            if s.stat.is_primary:
                notes.append("Clave primaria: créela con ALTER TABLE … ADD PRIMARY KEY; no se copia como índice.")
            elif not tgt.table_exists:
                notes.append(f"La tabla {s.stat.table} no existe en la base de datos de destino.")
            else:
                sql, note = build_ddl(s, tgt)
                if note:
                    notes.append(note)
                if sql and status == "different":
                    keep = "se omitirá" if target_engine == "postgres" else "fallará"
                    sql = (f"-- Ya existe {d.stat.index} en destino con otra definición; el CREATE {keep}\n"
                           f"-- mientras exista. Descomente el DROP para reemplazarlo:\n"
                           f"-- {_drop_sql(d.stat, target_engine)}\n{sql}")
                item.sql = sql
                item.copiable = sql is not None
        item.note = " ".join(notes) or None
        items.append(item)

    for d in remaining:
        items.append(IndexCompareItem(
            id=f"dst:{d.stat.schema_name}.{d.stat.index}", status="extra", table=d.stat.table,
            target_schema=d.stat.schema_name, target_index=d.stat.index, target_columns=d.stat.columns,
            target_unique=d.stat.is_unique, is_primary=d.stat.is_primary,
            note="Solo existe en destino."))

    order = {"missing": 0, "different": 1, "extra": 2, "same": 3}
    items.sort(key=lambda i: (order[i.status], i.table.lower(), (i.source_index or i.target_index or "").lower()))
    return items


def count_by_status(items: list[IndexCompareItem]) -> dict[str, int]:
    counts = {"missing": 0, "different": 0, "extra": 0, "same": 0}
    for i in items:
        counts[i.status] += 1
    return counts


# ------------------------------------------------------------------ script
def _describe(cfg: ConnectionConfig) -> str:
    return f"{cfg.name} [{ENGINE_NAMES.get(cfg.engine, cfg.engine)}] ({cfg.host}:{cfg.port}/{cfg.database})"


def build_copy_script(source: ConnectionConfig, target: ConnectionConfig, items: list[IndexCompareItem],
                      now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        "-- Script de copia de índices generado por Tyrux (DB Monitor)",
        f"-- Origen:  {_describe(source)}",
        f"-- Destino: {_describe(target)}",
        f"-- Fecha: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"-- Índices incluidos: {len(items)}",
        "--",
        "-- Ejecutar en la base de datos de DESTINO. Revise antes de ejecutar. Tyrux nunca ejecuta este script.",
    ]
    if target.engine == "postgres":
        lines.append("-- CREATE INDEX CONCURRENTLY no puede ejecutarse dentro de una transacción.")
    else:
        lines.append("-- CREATE INDEX ... ONLINE requiere Enterprise Edition (quítelo en otras ediciones).")
    for n, it in enumerate(items, 1):
        lines += ["", _RULE,
                  f"-- [{n}] {it.source_schema}.{it.source_index} en tabla {it.table} ({_STATUS_LABEL[it.status]})",
                  f"-- Columnas: {', '.join(it.source_columns)}"
                  + (" | ÚNICO" if it.source_unique else "")]
        if it.note:
            lines.append(f"-- Nota: {it.note}")
        lines.append(_RULE)
        lines.append((it.sql or "-- (sin SQL asociado)").strip())
    return "\n".join(lines) + "\n"
