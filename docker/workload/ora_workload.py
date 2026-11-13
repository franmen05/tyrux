#!/usr/bin/env python3
"""Slow-query workload for the Tyrux (DB Monitor) Oracle demo (schema SHOP in FREEPDB1).

Only depends on python-oracledb (thin mode):  pip install oracledb

    ORA_HOST=localhost ORA_PORT=1521 ORA_SERVICE=FREEPDB1 ORA_USER=shop ORA_PASSWORD=shop \
        python ora_workload.py --seconds 60 --concurrency 4

Each worker loops over a weighted mix of deliberately slow statements:
  * full scans of ORDERS by CUSTOMER_ID (missing index)          -> missing_index recommendation
  * filter by STATUS + CREATED_AT range, sorted (missing index)  -> missing_index (composite)
  * a big join + GROUP BY + ORDER BY (sort / hash work)
  * a long call: DBMS_SESSION.SLEEP inside PL/SQL                -> longest active query
  * occasional lock contention: one session updates a row and holds the lock while another
    session tries to update the same row                         -> locks / blocked sessions
Lock contention runs in a dedicated thread so blockers are visible for a few seconds at a time.
"""
from __future__ import annotations

import argparse
import os
import random
import threading
import time

import oracledb


def connect() -> oracledb.Connection:
    host = os.environ.get("ORA_HOST", "localhost")
    port = os.environ.get("ORA_PORT", "1521")
    service = os.environ.get("ORA_SERVICE", "FREEPDB1")
    conn = oracledb.connect(
        user=os.environ.get("ORA_USER", "shop"),
        password=os.environ.get("ORA_PASSWORD", "shop"),
        dsn=f"{host}:{port}/{service}",
        tcp_connect_timeout=5,
    )
    conn.module = "shop-workload"
    return conn


def q_orders_by_customer(cur, rnd):
    cur.execute("SELECT order_id, status, total, created_at FROM orders WHERE customer_id = :c "
                "ORDER BY created_at DESC", c=rnd.randint(1, 20000))
    cur.fetchall()


def q_pending_recent(cur, rnd):
    cur.execute("SELECT order_id, customer_id, total FROM orders "
                "WHERE status = :s AND created_at >= DATE '2023-01-01' + :d "
                "ORDER BY created_at", s=rnd.choice(["PENDING", "CANCELLED"]), d=rnd.randint(0, 900))
    cur.fetchall()


def q_customer_report(cur, rnd):
    cur.execute("""
        SELECT c.country, p.category, COUNT(*) n, SUM(oi.quantity * oi.unit_price) revenue
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN products p ON p.product_id = oi.product_id
        WHERE o.created_at >= DATE '2023-01-01' + :d
        GROUP BY c.country, p.category
        ORDER BY revenue DESC""", d=rnd.randint(0, 600))
    cur.fetchall()


def q_top_customers(cur, rnd):
    cur.execute("""
        SELECT customer_id, COUNT(*) n, SUM(total) t FROM orders
        WHERE status = 'DELIVERED' GROUP BY customer_id ORDER BY t DESC FETCH FIRST 20 ROWS ONLY""")
    cur.fetchall()


def q_sleep(cur, rnd):
    cur.execute("BEGIN DBMS_SESSION.SLEEP(:s); END;", s=rnd.choice([3, 5, 8]))


def q_product_lookup(cur, rnd):
    cur.execute("SELECT o.order_id, oi.quantity FROM order_items oi JOIN orders o ON o.order_id = oi.order_id "
                "WHERE oi.order_id = :o", o=rnd.randint(1, 250000))
    cur.fetchall()


MIX = [
    (q_orders_by_customer, 40),
    (q_pending_recent, 20),
    (q_customer_report, 8),
    (q_top_customers, 8),
    (q_product_lookup, 20),
    (q_sleep, 4),
]


def worker(n: int, deadline: float, stats: dict, lock: threading.Lock) -> None:
    rnd = random.Random(n)
    funcs, weights = zip(*MIX)
    conn = connect()
    cur = conn.cursor()
    while time.time() < deadline:
        f = rnd.choices(funcs, weights)[0]
        try:
            f(cur, rnd)
            with lock:
                stats[f.__name__] = stats.get(f.__name__, 0) + 1
        except oracledb.Error as e:
            print(f"[worker {n}] {f.__name__}: {e}")
            time.sleep(1)
    conn.close()


def lock_contention(deadline: float, hold: float = 6.0) -> None:
    """Session A updates a row and holds it; session B blocks on the same row until A commits."""
    a, b = connect(), connect()
    a.module = b.module = "shop-locks"
    rnd = random.Random(99)
    while time.time() + hold < deadline:
        cid = rnd.randint(1, 20000)
        ca = a.cursor()
        ca.execute("UPDATE customers SET phone = phone WHERE customer_id = :c", c=cid)

        def blocked():
            try:
                cb = b.cursor()
                cb.execute("UPDATE customers SET phone = phone WHERE customer_id = :c", c=cid)
                b.rollback()
            except oracledb.Error as e:
                print(f"[locks] {e}")

        t = threading.Thread(target=blocked, daemon=True)
        t.start()
        time.sleep(hold)
        a.rollback()
        t.join(timeout=10)
        time.sleep(rnd.uniform(2, 5))
    a.close()
    b.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--no-locks", action="store_true", help="disable the lock contention thread")
    args = ap.parse_args()
    deadline = time.time() + args.seconds
    stats: dict[str, int] = {}
    mtx = threading.Lock()
    threads = [threading.Thread(target=worker, args=(i, deadline, stats, mtx), daemon=True)
               for i in range(args.concurrency)]
    if not args.no_locks:
        threads.append(threading.Thread(target=lock_contention, args=(deadline,), daemon=True))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=args.seconds + 30)
    print("executed:", ", ".join(f"{k}={v}" for k, v in sorted(stats.items())))


if __name__ == "__main__":
    main()
