"""Time each idempotent cleanup statement in app/database.py init_db().

These re-run on EVERY app startup. Measure them against the current table size.
"""
import os
import sqlite3
import sys
import time

p = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"
con = sqlite3.connect(p)
con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA synchronous=NORMAL")
n = con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
print(f"{p}: {n} health_metrics rows\n")

STATEMENTS = [
    ("dedupe (id NOT IN GROUP BY)", """
        DELETE FROM health_metrics
        WHERE id NOT IN (
            SELECT MAX(id) FROM health_metrics
            GROUP BY user_id, metric_type, recorded_at, source
        )
    """),
    ("tag daily rollup rows", """
        UPDATE health_metrics SET granularity = 'daily'
        WHERE id IN (
            SELECT h.id FROM health_metrics h
            WHERE h.source = 'google_health_connect'
              AND substr(h.recorded_at, 12, 8) = '00:00:00'
              AND (
                NOT EXISTS (
                    SELECT 1 FROM health_metrics h2
                    WHERE h2.user_id = h.user_id
                      AND h2.metric_type = h.metric_type
                      AND date(h2.recorded_at) = date(h.recorded_at)
                      AND h2.id <> h.id
                )
                OR (
                    (
                        lower(h.metric_type) LIKE '%steps%'
                        OR lower(h.metric_type) LIKE '%distance%'
                        OR lower(h.metric_type) LIKE '%calor%'
                        OR lower(h.metric_type) LIKE '%minutes%'
                        OR lower(h.metric_type) LIKE '%heart rate%'
                        OR lower(h.metric_type) LIKE '%weight%'
                        OR lower(h.metric_type) LIKE '%body fat%'
                    )
                    AND (
                        h.value >= 5.0 * (
                            SELECT coalesce(MAX(h2.value), 0) FROM health_metrics h2
                            WHERE h2.user_id = h.user_id
                              AND h2.metric_type = h.metric_type
                              AND date(h2.recorded_at) = date(h.recorded_at)
                              AND h2.id <> h.id
                              AND substr(h2.recorded_at, 12, 8) <> '00:00:00'
                        )
                        OR CAST(strftime('%s', h.created_at) AS INTEGER) >= 3600 + (
                            SELECT coalesce(MAX(CAST(strftime('%s', h2.created_at) AS INTEGER)), 0)
                            FROM health_metrics h2
                            WHERE h2.user_id = h.user_id
                              AND h2.metric_type = h.metric_type
                              AND date(h2.recorded_at) = date(h.recorded_at)
                              AND h2.id <> h.id
                              AND substr(h2.recorded_at, 12, 8) <> '00:00:00'
                        )
                    )
                )
              )
        )
    """),
    ("delete superseded raw rows", """
        DELETE FROM health_metrics
        WHERE granularity = 'raw'
          AND source = 'google_health_connect'
          AND EXISTS (
            SELECT 1 FROM (
                SELECT user_id AS u, metric_type AS m,
                       substr(recorded_at, 1, 10) AS d
                FROM health_metrics
                WHERE granularity = 'daily'
                  AND source = 'google_health_connect'
            ) daily_days
            WHERE daily_days.u = health_metrics.user_id
              AND daily_days.m = health_metrics.metric_type
              AND daily_days.d = substr(health_metrics.recorded_at, 1, 10)
          )
    """),
    ("create unique index", """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_health_metric_point
        ON health_metrics (user_id, metric_type, recorded_at, source)
    """),
    ("create day-lookup index", """
        CREATE INDEX IF NOT EXISTS ix_health_metrics_day_lookup
        ON health_metrics (user_id, metric_type, granularity, recorded_at)
    """),
]

total = 0.0
for label, sql in STATEMENTS:
    t0 = time.perf_counter()
    con.execute(sql)
    con.commit()
    el = time.perf_counter() - t0
    total += el
    print(f"  {label:32} {el:8.2f}s")
print(f"\n  {'TOTAL on every startup':32} {total:8.2f}s")
con.close()
