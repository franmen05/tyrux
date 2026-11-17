from datetime import datetime, timezone

from app.index_compare import build_copy_script, compare_indexes, count_by_status, parse_column
from app.schemas import ConnectionConfig, IndexStat, TableStat


def pg(table, index, cols, unique=False, primary=False, schema="public", definition=None):
    definition = definition or (f"CREATE {'UNIQUE ' if unique else ''}INDEX {index} ON {schema}.{table} "
                                f"USING btree ({', '.join(cols)})")
    return IndexStat(schema_name=schema, table=table, index=index, columns=cols, definition=definition,
                     is_unique=unique or primary, is_primary=primary)


def ora(table, index, cols, unique=False, primary=False, schema="SHOP", definition=None):
    definition = definition or (f"CREATE {'UNIQUE ' if unique else ''}INDEX {schema}.{index} ON "
                                f"{schema}.{table} ({', '.join(cols)})")
    return IndexStat(schema_name=schema, table=table, index=index, columns=cols, definition=definition,
                     is_unique=unique or primary, is_primary=primary)


def tables(*names, schema="public"):
    return [TableStat(schema_name=schema, table=n) for n in names]


def by_index(items):
    return {(i.source_index or i.target_index): i for i in items}


def run(src, dst, se="postgres", te="postgres", tbls=None, default="public"):
    return compare_indexes(src, dst, source_engine=se, target_engine=te,
                           target_tables=tbls if tbls is not None else tables("orders", "customers"),
                           target_default_schema=default)


def test_parse_column():
    assert parse_column("customer_id", "postgres").key == "customer_id"
    c = parse_column('"CustomerId"', "postgres")
    assert c.ident.name == "CustomerId" and c.render("postgres") == '"CustomerId"'
    assert c.render("oracle") == '"CustomerId"'
    assert parse_column("lower((email)::text)", "postgres").ident is None
    c = parse_column("CREATED_AT DESC", "oracle")
    assert c.desc and c.key == "created_at desc" and c.render("postgres") == "created_at DESC"
    assert parse_column('UPPER("EMAIL")', "oracle").ident is None
    assert parse_column("order", "postgres").render("postgres") == '"order"'


def test_statuses_same_engine():
    src = [pg("orders", "orders_pkey", ["id"], primary=True),
           pg("orders", "orders_customer_idx", ["customer_id"]),
           pg("orders", "orders_status_idx", ["status", "created_at"]),
           pg("orders", "orders_date_idx", ["created_at"]),
           pg("customers", "customers_email_key", ["email"], unique=True)]
    dst = [pg("orders", "orders_pkey", ["id"], primary=True, schema="app"),
           pg("orders", "idx_cust", ["customer_id"], schema="app"),          # same, other name
           pg("orders", "orders_status_idx", ["status"], schema="app"),      # different columns
           pg("customers", "customers_name_idx", ["name"], schema="app")]    # extra
    items = run(src, dst, tbls=tables("orders", "customers", schema="app"))
    m = by_index(items)
    assert m["orders_pkey"].status == "same" and not m["orders_pkey"].copiable
    assert m["orders_customer_idx"].status == "same" and "idx_cust" in m["orders_customer_idx"].note
    assert m["orders_status_idx"].status == "different" and m["orders_status_idx"].copiable
    assert "Columnas" in m["orders_status_idx"].note
    assert "-- DROP INDEX CONCURRENTLY IF EXISTS app.orders_status_idx;" in m["orders_status_idx"].sql
    assert m["orders_date_idx"].status == "missing"
    assert m["orders_date_idx"].sql == ("CREATE INDEX CONCURRENTLY IF NOT EXISTS orders_date_idx "
                                        "ON app.orders (created_at);")
    assert m["customers_email_key"].sql.startswith("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS")
    assert m["customers_name_idx"].status == "extra" and not m["customers_name_idx"].copiable
    assert m["customers_name_idx"].id.startswith("dst:")
    assert count_by_status(items) == {"missing": 2, "different": 1, "extra": 1, "same": 2}
    assert [i.status for i in items] == ["missing", "missing", "different", "extra", "same", "same"]


def test_uniqueness_difference_and_case_insensitive_match():
    src = [pg("orders", "orders_ref_idx", ["ref"], unique=True)]
    dst = [ora("ORDERS", "ORDERS_REF_IDX", ["REF"])]
    item = run(src, dst, te="oracle", tbls=tables("ORDERS", schema="SHOP"))[0]
    assert item.status == "different" and "Unicidad" in item.note


def test_primary_key_missing_is_not_copiable():
    items = run([pg("orders", "orders_pkey", ["id"], primary=True)], [])
    assert items[0].status == "missing" and not items[0].copiable and "Clave primaria" in items[0].note


def test_missing_table_in_target_not_copiable():
    items = run([pg("invoices", "inv_idx", ["n"])], [], tbls=tables("orders"))
    assert not items[0].copiable and "no existe" in items[0].note


def test_cross_engine_pg_to_oracle():
    src = [pg("orders", "orders_customer_idx", ["customer_id", '"MixedCol"']),
           pg("orders", "orders_lower_idx", ["lower((status)::text)"]),
           pg("orders", "orders_partial_idx", ["status"],
              definition="CREATE INDEX orders_partial_idx ON public.orders USING btree (status) WHERE (status = 'open'::text)")]
    items = run(src, [], te="oracle", tbls=tables("ORDERS", schema="SHOP"), default="SHOP")
    m = by_index(items)
    assert m["orders_customer_idx"].sql == ('CREATE INDEX SHOP.ORDERS_CUSTOMER_IDX ON SHOP.ORDERS '
                                            '(CUSTOMER_ID, "MixedCol") ONLINE;')
    assert not m["orders_lower_idx"].copiable and "expresiones" in m["orders_lower_idx"].note
    assert not m["orders_partial_idx"].copiable and "parcial" in m["orders_partial_idx"].note


def test_cross_engine_oracle_to_pg():
    src = [ora("ORDERS", "ORD_DATE_IDX", ["CREATED_AT DESC", "ID"]),
           ora("ORDERS", "ORD_BM", ["STATUS"], definition="CREATE BITMAP INDEX SHOP.ORD_BM ON SHOP.ORDERS (STATUS)")]
    items = run(src, [], se="oracle", tbls=tables("orders", schema="shop"))
    m = by_index(items)
    assert m["ORD_DATE_IDX"].sql == ("CREATE INDEX CONCURRENTLY IF NOT EXISTS ord_date_idx "
                                     "ON shop.orders (created_at DESC, id);")
    assert m["ORD_BM"].copiable and "B-tree" in m["ORD_BM"].note


def test_same_engine_adapts_special_definitions():
    pg_expr = pg("orders", "orders_lower_idx", ["lower((status)::text)"],
                 definition="CREATE INDEX orders_lower_idx ON public.orders USING btree (lower((status)::text)) "
                            "WHERE (status IS NOT NULL)")
    item = run([pg_expr], [], tbls=tables("orders", schema="app"))[0]
    assert item.copiable
    assert item.sql == ("CREATE INDEX CONCURRENTLY IF NOT EXISTS orders_lower_idx ON app.orders USING btree "
                        "(lower((status)::text)) WHERE (status IS NOT NULL);")

    ora_expr = ora("ORDERS", "ORD_UP", ['UPPER("STATUS")'],
                   definition='CREATE INDEX SHOP.ORD_UP ON SHOP.ORDERS (UPPER("STATUS"))')
    item = run([ora_expr], [], se="oracle", te="oracle", tbls=tables("ORDERS", schema="PROD"))[0]
    assert item.sql == 'CREATE INDEX PROD.ORD_UP ON PROD.ORDERS (UPPER("STATUS")) ONLINE;'

    gin = pg("orders", "orders_tags_idx", ["tags"],
             definition="CREATE INDEX orders_tags_idx ON public.orders USING gin (tags)")
    item = run([gin], [], te="oracle", tbls=tables("ORDERS", schema="SHOP"))[0]
    assert not item.copiable and "gin" in item.note


def test_build_copy_script():
    src_cfg = ConnectionConfig(id=1, name="prod", engine="postgres", host="h1", port=5432,
                               database="shop", username="u", password="p")
    dst_cfg = ConnectionConfig(id=2, name="qa", engine="oracle", host="h2", port=1521,
                               database="FREEPDB1", username="shop", password="p")
    items = run([pg("orders", "orders_customer_idx", ["customer_id"])], [], te="oracle",
                tbls=tables("ORDERS", schema="SHOP"))
    script = build_copy_script(src_cfg, dst_cfg, items, now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    assert script.startswith("-- Script de copia de índices generado por Tyrux")
    assert "-- Origen:  prod [PostgreSQL] (h1:5432/shop)" in script
    assert "-- Destino: qa [Oracle] (h2:1521/FREEPDB1)" in script
    assert "-- Fecha: 2026-01-02 03:04:05 UTC" in script
    assert "-- Índices incluidos: 1" in script
    assert "CREATE INDEX SHOP.ORDERS_CUSTOMER_IDX ON SHOP.ORDERS (CUSTOMER_ID) ONLINE;" in script
