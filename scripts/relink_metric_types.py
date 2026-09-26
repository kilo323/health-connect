"""Re-normalize already-synced rows in place after seeding metric definitions.

Why this exists
---------------
On an unseeded database every synced row is stored with the RAW Google label as
metric_type ("steps", "heart_rate", "Active Minutes (Light)") and definition_id
NULL. Seeding the library fixes future syncs, but NOT rows already in the
database -- and re-syncing to repair them is a trap:

    _upsert_metric conflicts on (user_id, metric_type, recorded_at, source)

Once definitions exist, the sync computes the CANONICAL name ("Daily Steps").
That does not match the stored "steps" row for the same timestamp, so the upsert
INSERTs a second row instead of updating it. Re-syncing an unseeded database
therefore doubles the table rather than relinking it.

This script rewrites existing rows in place: it maps each metric_type through the
normalizer, renames it to the canonical name, and sets definition_id. Nothing is
deleted and no rows are added.

    python scripts/relink_metric_types.py            # dry run, prints the plan
    python scripts/relink_metric_types.py --apply    # do it

Renaming can in principle collide on the unique index (granularity is not part of
it), so collisions are detected and reported BEFORE anything is written, and the
whole run is one transaction.
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

# The database URL must be set BEFORE app.database is imported, otherwise the
# engine binds to the default database regardless of any later override.
#   python scripts/relink_metric_types.py [db_path] [--apply]
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
_db = _args[0] if _args else os.path.join(ROOT, "data", "health_tracker.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _db.replace("\\", "/")

import asyncio  # noqa: E402

from sqlalchemy import func, select  # noqa: E402

from app.database import async_session_factory  # noqa: E402
from app.models.health_data import HealthMetric  # noqa: E402
from app.services.metric_normalizer import metric_normalizer  # noqa: E402


async def main() -> int:
    apply_changes = "--apply" in sys.argv
    print(f"database: {_db}   mode: {'APPLY' if apply_changes else 'DRY RUN'}\n")

    async with async_session_factory() as db:
        await metric_normalizer.load(db)
        if not metric_normalizer._by_name and not metric_normalizer._by_alias:
            print("ERROR: the metric library is empty. Run "
                  "scripts/seed_metric_definitions.py first.")
            return 2

        # One representative unit per stored metric_type, so the normalizer gets
        # the same hint the sync used.
        units = dict((await db.execute(
            select(HealthMetric.metric_type, func.min(HealthMetric.unit))
            .where(HealthMetric.source == "google_health_connect")
            .group_by(HealthMetric.metric_type))).all())

        rows = (await db.execute(
            select(HealthMetric.id, HealthMetric.metric_type, HealthMetric.unit,
                   HealthMetric.recorded_at, HealthMetric.definition_id)
            .where(HealthMetric.source == "google_health_connect"))).all()

        # Build the rename plan per metric_type.
        plan: dict[str, dict] = {}
        unlinked_types: set[str] = set()
        for _id, mtype, unit, _ts, did in rows:
            if mtype in plan:
                continue
            hint = unit or units.get(mtype) or "count"
            norm = await metric_normalizer.normalize(db, mtype, hint)
            if norm.definition:
                plan[mtype] = {
                    "canonical": norm.canonical_name,
                    "definition_id": norm.definition.id,
                    "linked": True,
                }
            else:
                plan[mtype] = {"canonical": mtype, "definition_id": did, "linked": False}
                unlinked_types.add(mtype)

        counts = defaultdict(int)
        for _id, mtype, _u, _t, _d in rows:
            counts[mtype] += 1

        print(f"rows to process: {len(rows)}\n")
        print(f"{'stored metric_type':<38} {'-> canonical':<26} {'rows':>8}  def_id")
        for mtype in sorted(plan):
            p = plan[mtype]
            arrow = "" if p["linked"] else "   (no definition - left as-is)"
            print(f"  {mtype[:36]:<36} -> {p['canonical'][:24]:<24} "
                  f"{counts[mtype]:>7}  {p['definition_id']}{arrow}")

        # Collision check: granularity is not part of the unique index, so two
        # rows of the same metric collapsing onto one canonical name at the same
        # recorded_at would violate it.
        renames = {k: v["canonical"] for k, v in plan.items()
                   if v["linked"] and v["canonical"] != k}
        if renames:
            seen: dict[tuple, str] = {}
            collisions: list[str] = []
            for _id, mtype, _u, ts, _d in rows:
                new = renames.get(mtype)
                if new is None:
                    continue
                key = (1, new, ts)
                if key in seen and seen[key] != mtype:
                    collisions.append(
                        f"{seen[key]!r} and {mtype!r} both -> {new!r} at {ts}")
                seen.setdefault(key, mtype)
            # Also: a rename could land on a metric_type that already exists
            # unrenamed.
            for old, new in renames.items():
                if new in plan and new not in renames:
                    for _id, mtype, _u, ts, _d in rows:
                        if mtype == new:
                            collisions.append(
                                f"{old!r} -> {new!r} but {new!r} rows already exist "
                                f"(first at {ts})")
                            break
            if collisions:
                print(f"\n!! {len(collisions)} potential unique-index collision(s):")
                for c in collisions[:10]:
                    print("   !", c)
                print("   Nothing was written. Resolve these first.")
                return 1
            print(f"\ncollision check: OK ({len(renames)} rename(s))")

        still = {m: counts[m] for m in sorted(unlinked_types)}
        if still:
            print("\nmetric types with no definition (left unchanged):")
            for m, c in still.items():
                print(f"  {m:<38} {c:>7} rows")

        if not apply_changes:
            print("\nDRY RUN - nothing written. Re-run with --apply.")
            return 0

        for old, p in plan.items():
            if not p["linked"] or p["canonical"] == old:
                continue
            res = await db.execute(
                HealthMetric.__table__.update()
                .where(HealthMetric.metric_type == old)
                .values(metric_type=p["canonical"], definition_id=p["definition_id"]))
            print(f"  renamed {old} -> {p['canonical']} ({res.rowcount} rows)")

        # Link rows whose metric_type was already canonical but definition_id NULL.
        for mtype, p in plan.items():
            if p["linked"] and p["canonical"] == mtype:
                res = await db.execute(
                    HealthMetric.__table__.update()
                    .where(HealthMetric.metric_type == mtype)
                    .where(HealthMetric.definition_id.is_(None))
                    .values(definition_id=p["definition_id"]))
                if res.rowcount:
                    print(f"  linked {res.rowcount} row(s) for {mtype}")

        await db.commit()

        total = (await db.execute(
            select(func.count(HealthMetric.id))
            .where(HealthMetric.source == "google_health_connect"))).scalar_one()
        linked = (await db.execute(
            select(func.count(HealthMetric.id))
            .where(HealthMetric.source == "google_health_connect")
            .where(HealthMetric.definition_id.isnot(None)))).scalar_one()
        print(f"\ndone. google rows: {total}, linked: {linked} "
              f"({100.0 * linked / total:.1f}%), unlinked: {total - linked}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
