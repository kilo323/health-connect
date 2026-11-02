"""DESTRUCTIVE: rebuild authoritative daily rows from Google's own rollups.

Normally not needed for recent data: the sync refreshes the rollup of every
closed local day automatically (see `sync_health_data`), and admins can run the
same repair from Admin -> Sync Configuration. Use this CLI for history beyond
the sync window (days that closed before the rollup-refresh deployed) or after
restoring from a backup.

Google's ``dailyRollUp`` values are its deduplicated, account-timezone totals —
the same numbers the Google Health app shows. The script backfills them as
``granularity='daily'`` rows (stamped at UTC midnight of the civil date, the
same marker convention the sync uses), which the read path prefers over raw
sums. Raw rows are kept for intraday charts unless ``--delete-raw`` is given,
in which case the raw rows of each rebuilt LOCAL day are removed.

The shared implementation lives in app/services/rollup_repair.py (also exposed
as an admin endpoint); preview is the default and --yes is required to write.

Usage:
    python scripts/destructive/rebuild_daily_steps.py                  # preview
    python scripts/destructive/rebuild_daily_steps.py --yes            # write daily rows
    python scripts/destructive/rebuild_daily_steps.py --yes --delete-raw
    python scripts/destructive/rebuild_daily_steps.py --metric distance --days 365
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO / "data" / "health_tracker.db"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB,
                    help=f"database to repair (default: {DEFAULT_DB})")
    ap.add_argument("--metric", default="steps",
                    help="data type to rebuild (steps, distance, calories, ...)")
    ap.add_argument("--days", type=int, default=120, help="lookback window")
    ap.add_argument("--user-id", type=int, default=None,
                    help="user id (default: the user with stored Google tokens)")
    ap.add_argument("--delete-raw", action="store_true",
                    help="also delete the superseded raw rows of each rebuilt day")
    ap.add_argument("--yes", action="store_true", help="actually write (default: preview)")
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"database not found: {args.db}")

    # Point the app engine at the target DB and load .env (FERNET_KEY) before
    # importing app modules.
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + args.db.resolve().as_posix().replace("\\", "/")

    from sqlalchemy import select

    from app.database import async_session_factory
    from app.models.settings import AppSettings
    from app.services.rollup_repair import rebuild_daily_rollups

    async def run() -> None:
        async with async_session_factory() as db:
            if args.user_id is not None:
                user_id = args.user_id
            else:
                keys = (await db.execute(
                    select(AppSettings.key).where(
                        AppSettings.key.like("google_health_tokens_%")))).scalars().all()
                if not keys:
                    sys.exit("no stored Google tokens; pass --user-id and authorize first")
                user_id = int(keys[0].rsplit("_", 1)[1])

            print(f"database: {args.db}")
            print(f"user {user_id}, metric {args.metric}, window last {args.days} days")
            print(f"mode: {'WRITE' if args.yes else 'PREVIEW'}"
                  f"{' + delete raw' if args.delete_raw and args.yes else ''}\n")

            summary = await rebuild_daily_rollups(
                db, user_id=user_id, data_types=[args.metric], days=args.days,
                delete_raw=args.delete_raw, apply=args.yes)

            for row in summary["rows"]:
                delta = "" if row["delta"] is None else f"delta {row['delta']:+}"
                current = "-" if row["current"] is None else round(row["current"], 1)
                print(f"  {row['day']}  {row['metric_type']:<16} "
                      f"rollup={round(row['rollup'], 1):<10} "
                      f"current={current:<10} {delta}")

            if args.yes:
                print(f"\nwrote {summary['rows_written']} daily row(s)"
                      f"{' and deleted superseded raw rows' if args.delete_raw else ''}")
            else:
                print("\nPREVIEW ONLY - nothing was changed. Re-run with --yes to apply.")

    asyncio.run(run())


if __name__ == "__main__":
    main()
