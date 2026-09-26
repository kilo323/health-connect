"""Verify find_similar_definition() no longer suggests nonsense pairs.

Covers the three real unmatched metrics, plus positive controls to prove the
gate does not suppress genuine duplicates.
"""
import asyncio
import json
import os
import sqlite3
import sys
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "data", "health_tracker.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + DB.replace("\\", "/")
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

import app.models  # noqa: F401,E402
from app.database import async_session_factory  # noqa: E402
from app.services.metric_normalizer import (  # noqa: E402
    metric_normalizer, _DUPLICATE_THRESHOLD, _FUZZY_THRESHOLD,
)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
UNMATCHED = [
    {"metric_type": mt, "unit": unit}
    for mt, unit in con.execute(
        "SELECT h.metric_type, MIN(h.unit) FROM health_metrics h "
        "WHERE h.source='google_health_connect' AND h.definition_id IS NULL "
        "GROUP BY h.metric_type ORDER BY h.metric_type")
]
con.close()

# (query, unit, expect_suggestion, why)
CASES = [
    ("weight", "kg", False, "kg vs bpm - must not suggest Average Heart Rate"),
    ("body_temperature", "\u00b0C", False, "degC vs bpm"),
    ("body_fat_percentage", "%", False, "% vs minutes"),
    # Positive controls: a genuine duplicate should still be surfaced.
    ("Resting Heart Rate", "bpm", True, "alias of Average Heart Rate"),
    ("avg heart rate", "bpm", True, "alias of Average Heart Rate"),
    ("Sleep Hours", "hours", True, "Sleep Duration"),
    ("sleep hours", "hours", True, "alias of Sleep Duration"),
]


async def main() -> int:
    print(f"thresholds: fuzzy(auto-link)={_FUZZY_THRESHOLD}, "
          f"duplicate(suggestion)={_DUPLICATE_THRESHOLD}\n")
    async with async_session_factory() as db:
        await metric_normalizer.load(db)
        print(f"{'query':<22} {'unit':<8} {'-> suggestion':<24} "
              f"{'score':>7}  expected")
        print("-" * 92)
        failures = 0
        for query, unit, expect, why in CASES:
            d, score = metric_normalizer.find_similar_definition(query, unit=unit)
            got = d.name if d else None
            ok = (d is not None) == expect
            if not ok:
                failures += 1
            print(f"  {query:<20} {unit:<8} {str(got or '-'):<24} "
                  f"{score * 100:6.1f}%  {'OK' if ok else 'WRONG'}  ({why})")

        # Report the real unmatched metrics the way the endpoint will.
        print("\n=== proposals for the real unmatched metrics ===")
        for um in UNMATCHED:
            d, score = metric_normalizer.find_similar_definition(
                um["metric_type"], unit=um["unit"])
            shown = f"Looks like {d.name} ({score*100:.0f}%)" if d else "no suggestion"
            print(f"  {um['metric_type']:<22} unit={um['unit']:<6} -> {shown}")

    print(f"\nRESULT: {'PASS' if failures == 0 else f'FAIL ({failures})'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
