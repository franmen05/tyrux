-- Tyrux (DB Monitor) - Oracle demo schema for gvenzl/oracle-free (23ai Free).
--
-- Scripts in /container-entrypoint-initdb.d run once, on the first start of the container,
-- via sqlplus as SYSDBA connected to the CDB root. We therefore switch to the default PDB.
-- The image creates APP_USER (shop) in FREEPDB1 when APP_USER / APP_USER_PASSWORD are set;
-- the CREATE USER IF NOT EXISTS below only matters if the script is run without them.
--
-- What it builds (schema SHOP):
--   CUSTOMERS (20k), PRODUCTS (2k), ORDERS (250k), ORDER_ITEMS (~500k)
--   Deliberately MISSING indexes: ORDERS(CUSTOMER_ID) and ORDERS(STATUS, CREATED_AT)
--   UNUSED index:    IX_CUSTOMERS_PHONE        (no workload query touches PHONE)
--   DUPLICATE index: IX_ORDER_ITEMS_ORDER      (prefix of IX_ORDER_ITEMS_ORDER_PROD)
--   STALE stats:     ORDER_ITEMS (stats gathered, then ~1/3 of the rows modified)

WHENEVER SQLERROR CONTINUE
SET ECHO OFF FEEDBACK ON SERVEROUTPUT ON

ALTER SESSION SET CONTAINER = FREEPDB1;

CREATE USER IF NOT EXISTS shop IDENTIFIED BY shop;
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE, CREATE PROCEDURE TO shop;
ALTER USER shop QUOTA UNLIMITED ON users;
ALTER USER shop DEFAULT TABLESPACE users;

-- Privileges Tyrux needs (demo: broad on purpose).
GRANT SELECT_CATALOG_ROLE TO shop;
GRANT SELECT ANY DICTIONARY TO shop;
-- Lets the collector flush DBA_TAB_MODIFICATIONS (DBMS_STATS.FLUSH_DATABASE_MONITORING_INFO).
GRANT ANALYZE ANY TO shop;

-- Track every index access in DBA_INDEX_USAGE (default is sampled), demo only.
ALTER SYSTEM SET "_iut_stat_collection_type" = 'ALL' SCOPE = BOTH;

ALTER SESSION SET CURRENT_SCHEMA = shop;

CREATE TABLE customers (
  customer_id  NUMBER        NOT NULL,
  name         VARCHAR2(100) NOT NULL,
  email        VARCHAR2(150) NOT NULL,
  phone        VARCHAR2(30),
  country      VARCHAR2(2)   NOT NULL,
  created_at   DATE          NOT NULL,
  CONSTRAINT pk_customers PRIMARY KEY (customer_id)
);

CREATE TABLE products (
  product_id   NUMBER        NOT NULL,
  name         VARCHAR2(100) NOT NULL,
  category     VARCHAR2(30)  NOT NULL,
  price        NUMBER(10,2)  NOT NULL,
  CONSTRAINT pk_products PRIMARY KEY (product_id)
);

CREATE TABLE orders (
  order_id     NUMBER        NOT NULL,
  customer_id  NUMBER        NOT NULL,
  status       VARCHAR2(12)  NOT NULL,
  total        NUMBER(12,2)  NOT NULL,
  created_at   DATE          NOT NULL,
  notes        VARCHAR2(200),
  CONSTRAINT pk_orders PRIMARY KEY (order_id)
);

CREATE TABLE order_items (
  item_id      NUMBER        NOT NULL,
  order_id     NUMBER        NOT NULL,
  product_id   NUMBER        NOT NULL,
  quantity     NUMBER(5)     NOT NULL,
  unit_price   NUMBER(10,2)  NOT NULL,
  CONSTRAINT pk_order_items PRIMARY KEY (item_id)
);

INSERT /*+ APPEND */ INTO customers
SELECT n,
       'Cliente ' || n,
       'cliente' || n || '@example.com',
       '+34 6' || LPAD(MOD(n * 7919, 100000000), 8, '0'),
       CASE MOD(n, 5) WHEN 0 THEN 'ES' WHEN 1 THEN 'MX' WHEN 2 THEN 'AR' WHEN 3 THEN 'CL' ELSE 'CO' END,
       DATE '2020-01-01' + MOD(n, 1800)
FROM (SELECT LEVEL n FROM dual CONNECT BY LEVEL <= 20000);
COMMIT;

INSERT /*+ APPEND */ INTO products
SELECT n,
       'Producto ' || n,
       CASE MOD(n, 6) WHEN 0 THEN 'libros' WHEN 1 THEN 'hogar' WHEN 2 THEN 'deporte'
                      WHEN 3 THEN 'electronica' WHEN 4 THEN 'moda' ELSE 'juguetes' END,
       ROUND(5 + MOD(n * 37, 495) + MOD(n, 100) / 100, 2)
FROM (SELECT LEVEL n FROM dual CONNECT BY LEVEL <= 2000);
COMMIT;

INSERT /*+ APPEND */ INTO orders
SELECT n,
       1 + MOD(n * 7, 20000),
       CASE WHEN MOD(n, 50) = 0 THEN 'PENDING'
            WHEN MOD(n, 50) = 1 THEN 'CANCELLED'
            WHEN MOD(n, 10) < 3 THEN 'SHIPPED'
            ELSE 'DELIVERED' END,
       ROUND(10 + MOD(n * 13, 990) + MOD(n, 100) / 100, 2),
       DATE '2023-01-01' + MOD(n, 1000) + MOD(n, 24) / 24,
       CASE WHEN MOD(n, 4) = 0 THEN 'Entrega en horario de manana, llamar antes' END
FROM (SELECT (a.n - 1) * 500 + b.n n
      FROM (SELECT LEVEL n FROM dual CONNECT BY LEVEL <= 500) a,
           (SELECT LEVEL n FROM dual CONNECT BY LEVEL <= 500) b);
COMMIT;

INSERT /*+ APPEND */ INTO order_items
SELECT (o.order_id - 1) * 2 + k.k,
       o.order_id,
       1 + MOD(o.order_id * 31 + k.k * 17, 2000),
       1 + MOD(o.order_id + k.k, 5),
       ROUND(5 + MOD(o.order_id * 3 + k.k, 495), 2)
FROM orders o, (SELECT LEVEL k FROM dual CONNECT BY LEVEL <= 2) k;
COMMIT;

-- Indexes. ORDERS(CUSTOMER_ID) and ORDERS(STATUS, CREATED_AT) are missing on purpose.
-- IX_CUSTOMERS_PHONE: unused on purpose; IX_ORDER_ITEMS_ORDER: duplicate (prefix) on purpose.
CREATE INDEX ix_customers_phone       ON customers (phone);
CREATE INDEX ix_customers_email       ON customers (email);
CREATE INDEX ix_products_category     ON products (category);
CREATE INDEX ix_order_items_order     ON order_items (order_id);
CREATE INDEX ix_order_items_order_prod ON order_items (order_id, product_id);
CREATE INDEX ix_order_items_product   ON order_items (product_id);

-- Fresh statistics on everything...
BEGIN
  DBMS_STATS.GATHER_SCHEMA_STATS('SHOP', cascade => TRUE, no_invalidate => FALSE);
END;
/

-- ...then make ORDER_ITEMS stale: modify ~1/3 of its rows with conventional DML.
UPDATE order_items SET quantity = quantity + 1 WHERE MOD(item_id, 3) = 0;
COMMIT;

BEGIN
  DBMS_STATS.FLUSH_DATABASE_MONITORING_INFO;
END;
/

SELECT table_name, num_rows, stale_stats FROM dba_tab_statistics WHERE owner = 'SHOP' ORDER BY 1;
