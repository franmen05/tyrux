#!/usr/bin/env python3
"""Synthetic slow workload for the Tyrux (DB Monitor) Postgres demo database.

Only depends on psycopg 3. Connection from the standard libpq env vars:
PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE (defaults: localhost 55432 dbmon dbmon shop).

    python pg_workload.py --seconds 60 --concurrency 4

Runs a mix of deliberately slow queries (seq scans by customer_id, sorts by status/created_at,
joins, aggregates), an occasional long pg_sleep, and occasionally a lock-contention scenario
between two sessions (one holds a row lock in an open transaction while another waits for it).
All writes are rolled back, so the data does not change.
"""
from __future__ import annotations

import argparse
import os
import random
import signal
import sys
import threading
import time

import psycopg

STOP = threading.Event()


def conninfo() -> dict:
    return {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "55432")),
        "user": os.environ.get("PGUSER", "dbmon"),
        "password": os.environ.get("PGPASSWORD", "dbmon"),
        "dbname": os.environ.get("PGDATABASE", "shop"),
        "application_name": "dbmon-workload",
        "connect_timeout": 5,
    }


# (weight, sql, params factory)
QUERIES = [
    # seq scan on orders: no index on customer_id
    (30, "SELECT id, status, total, created_at FROM orders WHERE customer_id = %s ORDER BY created_at DESC",
     lambda: (random.randint(1, 50000),)),
    # seq scan + sort: no index on (status, created_at)
    (15, "SELECT id, customer_id, total FROM orders WHERE status = %s AND created_at > now() - %s::interval "
         "ORDER BY created_at DESC LIMIT 100",
     lambda: (random.choice(["pending", "shipped", "cancelled"]), f"{random.randint(1, 60)} days")),
    # join + aggregate per customer
    (10, "SELECT c.id, c.name, count(*) AS n, sum(o.total) AS spent FROM customers c "
         "JOIN orders o ON o.customer_id = c.id WHERE c.country = %s GROUP BY c.id, c.name "
         "ORDER BY spent DESC LIMIT 20",
     lambda: (random.choice(["ES", "MX", "AR", "CO", "CL", "PE", "US"]),)),
    # big sort (may spill with the default work_mem)
    (5, "SELECT customer_id, total, notes FROM orders ORDER BY notes NULLS FIRST, total DESC LIMIT 50", lambda: ()),
    # join through order_items (uses the order_id indexes)
    (10, "SELECT p.category, sum(oi.quantity * oi.price) FROM orders o JOIN order_items oi ON oi.order_id = o.id "
         "JOIN products p ON p.id = oi.product_id WHERE o.customer_id = %s GROUP BY p.category",
     lambda: (random.randint(1, 50000),)),
    # cheap index lookups so the PK indexes get used
    (20, "SELECT * FROM orders WHERE id = %s", lambda: (random.randint(1, 300000),)),
    (10, "SELECT * FROM customers WHERE email = %s", lambda: (f"cliente{random.randint(1, 50000)}@example.com",)),
]
WEIGHTS = [q[0] for q in QUERIES]


def worker(idx: int, stats: dict) -> None:
    while not STOP.is_set():
        try:
            with psycopg.connect(**conninfo(), autocommit=True) as conn:
                while not STOP.is_set():
                    r = random.random()
                    if r < 0.01:
                        conn.execute("SELECT pg_sleep(%s) /* long query */", (random.uniform(3, 8),))
                    else:
                        _, sql, params = random.choices(QUERIES, weights=WEIGHTS)[0]
                        conn.execute(sql, params()).fetchall()
                    stats["queries"] += 1
                    time.sleep(random.uniform(0.0, 0.05))
        except Exception as exc:  # keep going on errors (server restart, etc.)
            stats["errors"] += 1
            print(f"[worker {idx}] {type(exc).__name__}: {exc}", file=sys.stderr)
            STOP.wait(1)


def lock_scenario(stats: dict) -> None:
    """Every ~20-40 s: session A locks a customer row for a few seconds, session B waits on it."""
    while not STOP.wait(random.uniform(20, 40)):
        try:
            cid = random.randint(1, 50000)
            hold = random.uniform(4, 8)
            with psycopg.connect(**conninfo()) as a, psycopg.connect(**conninfo()) as b:
                a.execute("UPDATE customers SET last_login = last_login WHERE id = %s", (cid,))

                def waiter() -> None:
                    try:
                        b.execute("SET lock_timeout = '15s'")
                        b.execute("UPDATE customers SET last_login = now() WHERE id = %s", (cid,))
                    except Exception:
                        pass
                    finally:
                        b.rollback()

                t = threading.Thread(target=waiter, daemon=True)
                t.start()
                STOP.wait(hold)
                a.rollback()
                t.join(20)
                stats["locks"] += 1
        except Exception as exc:
            stats["errors"] += 1
            print(f"[locks] {type(exc).__name__}: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = forever)")
    ap.add_argument("--concurrency", type=int, default=4, help="number of query workers")
    ap.add_argument("--no-locks", action="store_true", help="disable the lock contention scenario")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    stats = {"queries": 0, "errors": 0, "locks": 0}
    threads = [threading.Thread(target=worker, args=(i, stats), daemon=True) for i in range(args.concurrency)]
    if not args.no_locks:
        threads.append(threading.Thread(target=lock_scenario, args=(stats,), daemon=True))
    for t in threads:
        t.start()
    ci = conninfo()
    print(f"workload: {args.concurrency} workers against {ci['host']}:{ci['port']}/{ci['dbname']}", flush=True)
    start = time.monotonic()
    try:
        while not STOP.is_set():
            if args.seconds and time.monotonic() - start >= args.seconds:
                break
            STOP.wait(5)
            print(f"  {time.monotonic() - start:6.0f}s queries={stats['queries']} "
                  f"locks={stats['locks']} errors={stats['errors']}", flush=True)
    except KeyboardInterrupt:
        pass
    STOP.set()
    for t in threads:
        t.join(10)
    print(f"done: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
