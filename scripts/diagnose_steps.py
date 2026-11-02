"""Read-only diagnostics: why do step counts differ from the Google Health app?

Two known causes (see docs/ and app/services/interval_merge.py):

1. Multi-device double-counting — overlapping raw intervals from phone + watch
   summed twice at ingest.
2. UTC-vs-local day bucketing — days were bucketed by UTC while Google buckets
   by the user's local timezone, shifting evening steps into the next day.

This script measures which one applies and how badly, without writing anything.

DB analysis (always, read-only):
  * per-day Steps for the last N days: daily row vs raw sum vs hourly sum
  * days carrying BOTH a daily row and raw rows
  * legacy metric_type variants (e.g. "Daily Steps" vs "Steps")
  * interval-gap fingerprint of the newest raw day (irregular starts hint at
    several device streams being summed)

Live Google comparison (opt-in, needs the app's .env with FERNET_KEY and an
authorized account; only read-only API calls):
  * Google's own steps dailyRollUp vs the app's value per day  <- direct
    over-count measurement
  * one day's raw points grouped by dataOrigin/dataSource, with the naive sum
    vs the deduped (interval-merge) sum

Usage:
    python scripts/diagnose_steps.py
    python scripts/diagnose_steps.py --db X:\\path\\health_tracker.db
    python scripts/diagnose_steps.py --google
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO / "data" / "health_tracker.db"


def load_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        sys.exit(f"database not found: {path}")
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def db_analysis(con: sqlite3.Connection, days: int) -> dict:
    """Print the DB-side picture. Returns {day: app_value} for the Google compare."""
    print("=== 1. Steps-like metric_types (rows, granularity, range) ===")
    rows = con.execute("""
        SELECT metric_type, granularity, COUNT(*) n,
               MIN(recorded_at) first_at, MAX(recorded_at) last_at
        FROM health_metrics
        WHERE lower(metric_type) LIKE '%step%'
        GROUP BY metric_type, granularity
        ORDER BY metric_type, granularity
    """).fetchall()
    for r in rows:
        print(f"  {dict(r)}")
    if not rows:
        print("  (no step rows in this database)")

    print(f"\n=== 2. Per-day Steps (last {days} days): daily row vs raw sum ===")
    print(f"  {'day':<12} {'daily':>10} {'raw_sum':>10} {'raw_n':>7} "
          f"{'hourly_sum':>10} {'raw_first':<20} {'raw_last':<20}")
    per_day = con.execute("""
        SELECT date(recorded_at) AS day,
               MAX(CASE WHEN granularity = 'daily' THEN value END) AS daily_value,
               SUM(CASE WHEN granularity <> 'daily' THEN value END) AS raw_sum,
               SUM(CASE WHEN granularity <> 'daily' THEN 1 ELSE 0 END) AS raw_n,
               MIN(CASE WHEN granularity <> 'daily' THEN recorded_at END) AS raw_first,
               MAX(CASE WHEN granularity <> 'daily' THEN recorded_at END) AS raw_last
        FROM health_metrics
        WHERE lower(metric_type) = 'steps' AND recorded_at >= date('now', ?)
        GROUP BY date(recorded_at) ORDER BY day
    """, (f"-{days} days",)).fetchall()
    hourly = {
        r["day"]: r["hsum"]
        for r in con.execute("""
            SELECT date(recorded_at) AS day, SUM(value) hsum
            FROM metric_hourly
            WHERE lower(metric_type) = 'steps' AND recorded_at >= date('now', ?)
            GROUP BY date(recorded_at)
        """, (f"-{days} days",)).fetchall()
    }
    app_values: dict[str, float] = {}
    for r in per_day:
        app_val = r["daily_value"] if r["daily_value"] is not None else (r["raw_sum"] or 0)
        app_values[r["day"]] = float(app_val)
        print(f"  {r['day']:<12} {str(r['daily_value'] or ''):>10} "
              f"{str(round(r['raw_sum'], 1) if r['raw_sum'] is not None else ''):>10} "
              f"{str(r['raw_n'] or ''):>7} {str(round(hourly[r['day']], 1) if r['day'] in hourly else ''):>10} "
              f"{str(r['raw_first'] or ''):<20} {str(r['raw_last'] or ''):<20}")

    print("\n=== 3. Days with BOTH a daily row and raw rows (normal for closed")
    print("     days: the daily row wins in reads, raw backs intraday) ===")
    both = con.execute("""
        SELECT date(recorded_at) AS day,
               SUM(CASE WHEN granularity = 'daily' THEN 1 ELSE 0 END) daily_n,
               SUM(CASE WHEN granularity <> 'daily' THEN 1 ELSE 0 END) raw_n
        FROM health_metrics WHERE lower(metric_type) = 'steps'
        GROUP BY day HAVING daily_n > 0 AND raw_n > 0 ORDER BY day DESC LIMIT 20
    """).fetchall()
    for r in both:
        print(f"  {dict(r)}")
    if not both:
        print("  (none)")

    print("\n=== 4. Same-minute duplicate raw rows (multi-stream fingerprint) ===")
    dup = con.execute("""
        SELECT substr(recorded_at, 1, 16) minute, COUNT(*) c
        FROM health_metrics
        WHERE lower(metric_type) = 'steps' AND granularity <> 'daily'
        GROUP BY minute HAVING c > 1 ORDER BY minute DESC LIMIT 10
    """).fetchall()
    for r in dup:
        print(f"  {dict(r)}")
    if not dup:
        print("  (none — or overlaps start at different instants, see --google)")

    print("\n=== 5. Interval-gap profile of the newest raw day ===")
    day = con.execute("""
        SELECT MAX(date(recorded_at)) FROM health_metrics
        WHERE lower(metric_type) = 'steps' AND granularity <> 'daily'
    """).fetchone()[0]
    if not day:
        print("  (no raw rows)")
        return app_values
    print(f"  day = {day}")
    prev = None
    odd = 0
    for r in con.execute("""
        SELECT recorded_at, value FROM health_metrics
        WHERE lower(metric_type) = 'steps' AND granularity <> 'daily' AND date(recorded_at) = ?
        ORDER BY recorded_at
    """, (day,)).fetchall():
        ts = r["recorded_at"]
        if prev is not None:
            gap = (datetime.fromisoformat(ts) - datetime.fromisoformat(prev)).total_seconds()
            if gap != 60 and odd < 15:
                print(f"  gap {gap:>7.0f}s  {prev} -> {ts}  (value={r['value']})")
                odd += 1
        prev = ts
    if not odd:
        print("  (regular 60s intervals)")
    return app_values


def google_compare(con: sqlite3.Connection, app_values: dict, lookback_days: int) -> None:
    """Live Google Health API comparison (read-only calls)."""
    print("\n=== 6. Google dailyRollUp vs app value (direct over-count measure) ===")
    # The app modules need DATABASE_URL pointed at the inspected DB and .env
    # loaded (FERNET_KEY decrypts the stored OAuth tokens).
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + con.execute(
        "PRAGMA database_list").fetchone()["file"].replace("\\", "/")

    import asyncio

    from app.services.google_health import GoogleHealthService
    from app.services.interval_merge import IntervalSample, merge_interval_samples

    def origin_of(point: dict) -> str:
        meta = point.get("metadata") or {}
        origin = meta.get("dataOrigin") or meta.get("dataSource") or point.get("dataSource")
        if isinstance(origin, dict):
            device = origin.get("device") or {}
            origin = (origin.get("name") or origin.get("id")
                      or device.get("displayName") or device.get("uid")
                      or origin.get("platform"))
        return str(origin) if origin else "unknown"

    async def run() -> None:
        svc = GoogleHealthService()
        users = [r["key"].rsplit("_", 1)[1] for r in con.execute(
            "SELECT key FROM app_settings WHERE key LIKE 'google_health_tokens_%'"
        ).fetchall()]
        if not users:
            print("  no stored Google tokens — skipping live comparison")
            return
        user_id = int(users[0])
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)

        rollups = await svc.fetch_daily_rollup(
            user_id=user_id, data_type="steps",
            start_time=start, end_time=end)
        if not rollups:
            print("  Google returned no steps rollups for the window")
        print(f"  {'day':<12} {'app':>10} {'google':>10} {'ratio':>7} {'note'}")
        for point in rollups:
            date_info = (point.get("civilStartTime") or {}).get("date") or {}
            if not date_info:
                continue
            day = f"{date_info.get('year'):04d}-{date_info.get('month'):02d}-{date_info.get('day'):02d}"
            try:
                google_val = float(point.get("steps", {}).get("countSum"))
            except (TypeError, ValueError):
                continue
            app_val = app_values.get(day)
            ratio = (app_val / google_val) if (app_val is not None and google_val) else None
            note = ""
            if ratio is not None:
                if ratio > 1.05:
                    note = "APP HIGHER (double-count?)"
                elif ratio < 0.95:
                    note = "app lower (day boundary / missing data)"
            print(f"  {day:<12} "
                  f"{round(app_val, 1) if app_val is not None else '-':>10} "
                  f"{round(google_val, 1):>10} "
                  f"{round(ratio, 3) if ratio is not None else '-':>7} {note}")

        # Per-origin breakdown of one day's raw points.
        print("\n=== 7. Raw steps by data origin (multi-device overlap) ===")
        raw_day = con.execute("""
            SELECT date(recorded_at) FROM health_metrics
            WHERE lower(metric_type) = 'steps' AND granularity <> 'daily'
            GROUP BY date(recorded_at) ORDER BY COUNT(*) DESC LIMIT 1
        """).fetchone()[0]
        if not raw_day:
            raw_day = (end - timedelta(days=2)).date().isoformat()
            print(f"  (no raw rows locally; probing {raw_day} on the API only)")
        d = datetime.fromisoformat(raw_day).replace(tzinfo=timezone.utc)
        points = await svc.fetch_health_data(
            user_id=user_id, data_type="steps",
            start_time=d, end_time=d + timedelta(days=1))
        by_origin: dict[str, list] = {}
        for p in points:
            payload = p.get("steps") or {}
            iv = payload.get("interval") or {}
            try:
                s = datetime.fromisoformat(iv["startTime"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(iv["endTime"].replace("Z", "+00:00"))
                v = float(payload["count"])
            except (KeyError, TypeError, ValueError):
                continue
            by_origin.setdefault(origin_of(p), []).append(
                IntervalSample("steps", v, s, e, origin_of(p)))
        if not by_origin:
            print("  no raw points")
            return
        naive = 0.0
        all_samples: list = []
        for origin, samples in sorted(by_origin.items()):
            total = sum(s.value for s in samples)
            naive += total
            all_samples.extend(samples)
            print(f"  {origin:<40} {len(samples):>6} pts  sum={round(total, 1)}")
        merged = merge_interval_samples(all_samples)
        merged_total = sum(v for _l, v, _t in merged)
        print(f"  {'naive sum (all devices)':<40} {len(all_samples):>6} pts  sum={round(naive, 1)}")
        print(f"  {'deduped (interval merge)':<40} {len(merged):>6} rows sum={round(merged_total, 1)}")
        if naive > 0:
            print(f"  over-count if summed naively: {round((naive / merged_total - 1) * 100, 1)}%"
                  if merged_total else "")
        if len(by_origin) == 1:
            print("  only one data origin reported — overlap double-count unlikely")

    asyncio.run(run())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help=f"database to inspect (default: {DEFAULT_DB})")
    ap.add_argument("--days", type=int, default=60, help="lookback for DB analysis")
    ap.add_argument("--google", action="store_true",
                    help="also compare against the live Google Health API")
    ap.add_argument("--google-days", type=int, default=14,
                    help="lookback for the Google rollup comparison")
    args = ap.parse_args()

    con = load_db(args.db)
    try:
        app_values = db_analysis(con, args.days)
        if args.google:
            google_compare(con, app_values, args.google_days)
    finally:
        con.close()


if __name__ == "__main__":
    main()
