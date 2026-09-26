"""Reproduce the bogus 'merge target' suggestion for unmatched metrics.

find_similar_definition() has no minimum-score gate, so it returns the least-bad
definition for any input. This shows the actual scores.
"""
import asyncio
import json
import os
import sqlite3
import sys
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "health_tracker.db")

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
defs = []
for name, unit, category, aliases in con.execute(
        "SELECT name, unit, category, aliases FROM metric_definitions ORDER BY name"):
    try:
        als = json.loads(aliases or "[]")
    except (json.JSONDecodeError, TypeError):
        als = []
    defs.append((name, unit, category, [a.lower() for a in als if isinstance(a, str)]))
unmatched = [r[0] for r in con.execute(
    "SELECT DISTINCT metric_type FROM health_metrics "
    "WHERE source='google_health_connect' AND definition_id IS NULL")]
con.close()

print(f"{len(defs)} definitions, unmatched: {unmatched}\n")


def best(query):
    q = query.strip().lower()
    bs, bd = 0.0, None
    for name, unit, cat, als in defs:
        for cand in [name.lower()] + als:
            s = SequenceMatcher(None, q, cand).ratio()
            if s > bs:
                bs, bd = s, (name, unit, cat, cand)
    return bs, bd


print("=== what find_similar_definition() returns today (no threshold) ===")
for um in unmatched:
    s, d = best(um)
    print(f"  {um:<24} -> {d[0]:<24} {s*100:5.1f}%   (matched via alias {d[3]!r}, "
          f"unit={d[1]})")

print("\n=== also what the LLM-proposed name would score ===")
for um, proposed in (("weight", "Weight"),
                     ("body_fat_percentage", "Body Fat Percentage"),
                     ("body_temperature", "Body Temperature")):
    s, d = best(proposed)
    print(f"  {proposed:<24} -> {d[0]:<24} {s*100:5.1f}%   (unit={d[1]})")

print("\n=== what a unit-compatible match looks like ===")
for um, unit in (("weight", "kg"), ("body_fat_percentage", "%"),
                 ("body_temperature", "\u00b0C")):
    s, d = best(um)
    ok = d and (d[1] or "").strip().lower() == unit.strip().lower()
    print(f"  {um:<24} best={d[0] if d else None:<24} unit={d[1] if d else None:<6} "
          f"compatible={bool(ok)}")
