"""Profile the two reported problems against a database in the USER'S state.

Their fresh Docker deployment has NO metric_definitions and therefore every
synced row is unlinked (definition_id IS NULL). This script:

  1. copies the live DB and strips it to that state,
  2. times get_unmatched_metrics() cold and warm  -> explains the slow page,
  3. calls the real propose_metric_definitions() with every SequenceMatcher call
     instrumented, wrapped in a hard timeout, so a hang reports WHERE it stalls
     and with what input sizes.

Run: .venv\\Scripts\\python.exe -u scripts\\profile_propose.py
"""
import asyncio
import difflib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

REAL_DB = os.path.join(ROOT, "data", "health_tracker.db")
MIMIC = os.path.join(os.environ.get("TEMP", ROOT), "health_tracker_userstate.db")

for suffix in ("", "-wal", "-shm"):
    p = MIMIC + suffix
    if os.path.exists(p):
        os.remove(p)
shutil.copy2(REAL_DB, MIMIC)

con = sqlite3.connect(MIMIC)
defs = con.execute("SELECT COUNT(*) FROM metric_definitions").fetchone()[0]
rows = con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
unlinked = con.execute(
    "SELECT COUNT(*) FROM health_metrics WHERE definition_id IS NULL").fetchone()[0]
# Reproduce their state: unseeded library, every row unlinked.
con.execute("DELETE FROM metric_definitions")
con.execute("UPDATE health_metrics SET definition_id = NULL")
con.commit()
by_type = dict(con.execute(
    "SELECT metric_type, COUNT(*) FROM health_metrics WHERE definition_id IS NULL "
    "GROUP BY metric_type ORDER BY 2 DESC"))
con.close()
print(f"copied {rows} rows; mimicking their state:")
print(f"  was: {defs} definitions, {unlinked} unlinked -> now: 0 definitions, {rows} unlinked")
for k, v in list(by_type.items())[:6]:
    print(f"    {k:<24} {v}")
print()

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + MIMIC.replace("\\", "/")

import app.models  # noqa: F401,E402

# ── Instrument SequenceMatcher across BOTH modules that use it ──────────────
slow_calls = []


class TimedMatcher:
    """Proxy that reports any SequenceMatcher call over 50 ms."""

    def __init__(self, a, b, *args, **kwargs):
        self._t0 = time.perf_counter()
        self._m = _RealMatcher(a, b, *args, **kwargs)
        self._alen = len(a) if isinstance(a, str) else -1
        self._blen = len(b) if isinstance(b, str) else -1
        # Announce huge inputs on ENTRY so a multi-minute stall is visible while
        # it is still running, not only after it finally returns.
        if max(self._alen, self._blen) > 50_000:
            print(f"      >> SequenceMatcher starting with len(a)={self._alen} "
                  f"len(b)={self._blen} ...", flush=True)
            self._t0 = time.perf_counter()

    def __getattr__(self, item):
        fn = getattr(self._m, item)
        if not callable(fn):
            return fn
        def wrapper(*a, **kw):
            r = fn(*a, **kw)
            el = time.perf_counter() - self._t0
            if el > 0.05:
                slow_calls.append((el, self._alen, self._blen, item))
                print(f"      !! SequenceMatcher.{item} took {el:.1f}s "
                      f"(len(a)={self._alen}, len(b)={self._blen})", flush=True)
            return r
        return wrapper


_RealMatcher = difflib.SequenceMatcher

import app.routers.admin as admin_mod  # noqa: E402
import app.services.metric_normalizer as mn_mod  # noqa: E402
admin_mod.SequenceMatcher = TimedMatcher
mn_mod.SequenceMatcher = TimedMatcher


async def main() -> int:
    from sqlalchemy import select
    from app.database import async_session_factory
    from app.models.user import User
    from app.services.metric_normalizer import metric_normalizer

    # ── 1. Page-load cost ───────────────────────────────────────────────────
    metric_normalizer._loaded = False
    async with async_session_factory() as db:
        t0 = time.perf_counter()
        unmatched = await metric_normalizer.get_unmatched_metrics(db)
        cold = time.perf_counter() - t0
        t0 = time.perf_counter()
        unmatched = await metric_normalizer.get_unmatched_metrics(db)
        warm = time.perf_counter() - t0
    print(f"=== get_unmatched_metrics ({len(unmatched)} types) ===")
    print(f"  cold: {cold:.2f}s   warm: {warm:.2f}s   <- page-load cost\n")

    # ── 2. propose, with a hard timeout ─────────────────────────────────────
    from app.routers.admin import propose_metric_definitions

    async with async_session_factory() as db:
        user = (await db.execute(
            select(User).where(User.username == "james"))).scalar_one()
        print("=== propose_metric_definitions (LLM + post-processing) ===", flush=True)
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                propose_metric_definitions(current_user=user, db=db), timeout=300)
        except asyncio.TimeoutError:
            print(f"  TIMED OUT after {time.perf_counter() - t0:.0f}s "
                  f"(the reported hang)", flush=True)
            return 1
        el = time.perf_counter() - t0
        print(f"  returned in {el:.1f}s: {len(resp.proposals)} proposals, "
              f"unmatched={resp.unmatched_count}")

    if slow_calls:
        print("\nslow SequenceMatcher calls:")
        for el, a, b, meth in sorted(slow_calls, reverse=True)[:5]:
            print(f"  {el:6.1f}s  len(a)={a} len(b)={b}  {meth}")
    else:
        print("\nno SequenceMatcher call exceeded 50 ms")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
