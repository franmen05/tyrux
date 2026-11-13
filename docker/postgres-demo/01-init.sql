-- Tyrux (DB Monitor) demo database (runs in /docker-entrypoint-initdb.d of postgres:17 as user dbmon, db shop).
-- A small e-commerce schema with deliberate performance problems for the tool to find:
--   * MISSING index on orders(customer_id)         -> seq scans for "orders of a customer"
--   * MISSING index on orders(status, created_at)  -> seq scans + sorts for "recent pending orders"
--   * UNUSED index   orders_total_idx              -> never used by the workload
--   * DUPLICATE index customers_email_idx          -> same column as the UNIQUE constraint
--   * DUPLICATE (prefix) index order_items_order_id_idx -> prefix of order_items_order_product_idx
--   * Dead tuples on customers (autovacuum disabled + UPDATE of a slice) -> bloat / stale stats

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
GRANT pg_monitor TO dbmon;

CREATE TABLE customers (
    id          serial PRIMARY KEY,
    name        text        NOT NULL,
    email       text        NOT NULL UNIQUE,
    country     text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_login  timestamptz
) WITH (autovacuum_enabled = false);

CREATE TABLE products (
    id        serial PRIMARY KEY,
    name      text           NOT NULL,
    category  text           NOT NULL,
    price     numeric(10, 2) NOT NULL
);

CREATE TABLE orders (
    id           bigserial PRIMARY KEY,
    customer_id  int            NOT NULL REFERENCES customers (id),
    status       text           NOT NULL,
    created_at   timestamptz    NOT NULL,
    total        numeric(12, 2) NOT NULL,
    notes        text
);

CREATE TABLE order_items (
    id          bigserial PRIMARY KEY,
    order_id    bigint         NOT NULL REFERENCES orders (id),
    product_id  int            NOT NULL REFERENCES products (id),
    quantity    int            NOT NULL,
    price       numeric(10, 2) NOT NULL
);

INSERT INTO customers (name, email, country, created_at)
SELECT 'Cliente ' || g,
       'cliente' || g || '@example.com',
       (ARRAY['ES', 'MX', 'AR', 'CO', 'CL', 'PE', 'US'])[1 + (g % 7)],
       now() - (g % 1000) * interval '1 day'
FROM generate_series(1, 50000) AS g;

INSERT INTO products (name, category, price)
SELECT 'Producto ' || g,
       (ARRAY['libros', 'electronica', 'hogar', 'deporte', 'moda'])[1 + (g % 5)],
       round((5 + random() * 495)::numeric, 2)
FROM generate_series(1, 5000) AS g;

INSERT INTO orders (customer_id, status, created_at, total, notes)
SELECT 1 + (random() * 49999)::int,
       CASE WHEN r < 0.70 THEN 'delivered'
            WHEN r < 0.85 THEN 'shipped'
            WHEN r < 0.95 THEN 'pending'
            ELSE 'cancelled' END,
       now() - random() * interval '730 days',
       round((10 + random() * 990)::numeric, 2),
       CASE WHEN random() < 0.2 THEN repeat('nota ', 10) END
FROM (SELECT g, random() AS r FROM generate_series(1, 300000) AS g) s;

INSERT INTO order_items (order_id, product_id, quantity, price)
SELECT o.id,
       1 + ((o.id * 7 + k * 13) % 5000)::int,
       1 + (k % 3),
       round((5 + random() * 200)::numeric, 2)
FROM orders o
CROSS JOIN generate_series(1, 2) AS k;

-- Indexes: useful ones...
CREATE INDEX order_items_order_product_idx ON order_items (order_id, product_id);
-- ...a duplicate (prefix of the previous one)...
CREATE INDEX order_items_order_id_idx ON order_items (order_id);
-- ...a duplicate of the UNIQUE constraint customers_email_key...
CREATE INDEX customers_email_idx ON customers (email);
-- ...and one the workload never uses.
CREATE INDEX orders_total_idx ON orders (total);

ANALYZE;

-- Dead tuples: customers has autovacuum disabled; update ~40% of the rows and do not vacuum.
UPDATE customers SET last_login = now() - (id % 30) * interval '1 day' WHERE id % 5 IN (0, 1);
