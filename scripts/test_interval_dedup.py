"""Verification for app/services/interval_merge.py (multi-device dedup).

Synthetic fixtures for the merge semantics used at ingest: per-slice max across
data origins, coalesced back into rows. Runs on stdlib only.

Usage:
    python scripts/test_interval_dedup.py
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "interval_merge",
    Path(__file__).resolve().parents[1] / "app" / "services" / "interval_merge.py",
)
interval_merge = importlib.util.module_from_spec(_spec)
sys.modules["interval_merge"] = interval_merge  # dataclasses resolve via sys.modules
_spec.loader.exec_module(interval_merge)
IntervalSample = interval_merge.IntervalSample
merge_interval_samples = interval_merge.merge_interval_samples

T0 = datetime(2026, 11, 1, 10, 0, 0)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def sample(value, start_m, end_m, origin="phone", label="steps") -> IntervalSample:
    return IntervalSample(label, float(value), at(start_m), at(end_m), origin)


def total(rows, label="steps"):
    return sum(v for l, v, _ in rows if l == label)


def check(name, cond):
    if not cond:
        print(f"FAIL  {name}")
        sys.exit(1)
    print(f"ok    {name}")


# 1. Single origin passes through unchanged (values and starts preserved).
rows = merge_interval_samples([
    sample(10, 0, 5), sample(20, 5, 10), sample(5, 20, 25),
])
check("single-origin passthrough",
      rows == [("steps", 10.0, at(0)), ("steps", 20.0, at(5)), ("steps", 5.0, at(20))])

# 2. Identical intervals from two devices collapse to one row with the max value.
rows = merge_interval_samples([
    sample(10, 0, 5, "phone"), sample(14, 0, 5, "watch"),
])
check("identical intervals -> max",
      rows == [("steps", 14.0, at(0))])

# 3. Partial overlap: merged total must drop the double-counted overlap.
#    phone 10 steps over [0,5), watch 10 over [2,7): naive sum 20.
rows = merge_interval_samples([
    sample(10, 0, 5, "phone"), sample(10, 2, 7, "watch"),
])
check("partial overlap de-double-counted", total(rows) == 14.0)
check("partial overlap bounded by naive sum", total(rows) < 20.0)

# 4. Disjoint intervals from two devices are all kept.
rows = merge_interval_samples([
    sample(10, 0, 5, "phone"), sample(3, 5, 10, "watch"),
])
check("disjoint intervals kept", total(rows) == 13.0)

# 5. Labels merge independently (move-minutes style per-level rows).
rows = merge_interval_samples([
    IntervalSample("Active Minutes (Light)", 4.0, at(0), at(5), "phone"),
    IntervalSample("Active Minutes (Light)", 6.0, at(0), at(5), "watch"),
    IntervalSample("Active Minutes (Moderate)", 2.0, at(0), at(5), "phone"),
])
by_label = {l: v for l, v, _ in rows}
check("per-label merge",
      by_label == {"Active Minutes (Light)": 6.0, "Active Minutes (Moderate)": 2.0})

# 6. Zero-duration samples pass through untouched.
rows = merge_interval_samples([
    sample(7, 3, 3, "phone"), sample(2, 4, 4, "watch"),
])
check("zero-duration passthrough",
      sorted(rows) == sorted([("steps", 7.0, at(3)), ("steps", 2.0, at(4))]))

# 7. Same-device repeated intervals still sum (within one origin the merge is
#    additive across its own slices).
rows = merge_interval_samples([
    sample(4, 0, 2, "phone"), sample(6, 2, 4, "phone"),
])
check("single origin additive", total(rows) == 10.0)

# 8. Three-way overlap: the busiest device wins each slice; total is bounded
#    between the strongest single device and the naive sum.
rows = merge_interval_samples([
    sample(10, 0, 10, "phone"), sample(30, 0, 10, "watch"),
    sample(20, 0, 10, "scale"), sample(5, 10, 20, "phone"),
])
check("three-way overlap", total(rows) == 35.0)

# 9. Dominant-origin semantics: the device with the biggest total wins the
#    overlaps even where a secondary device reports a higher per-slice value —
#    per-device noise must not composite into the total (a per-slice max would
#    give 39 here; Google's own rollups match the dominant total of 30).
rows = merge_interval_samples([
    IntervalSample("steps", 25.0, at(0), at(5), "phone"),
    IntervalSample("steps", 5.0, at(5), at(10), "phone"),
    IntervalSample("steps", 14.0, at(0), at(5), "watch"),
    IntervalSample("steps", 14.0, at(5), at(10), "watch"),
])
check("dominant origin beats per-slice max", total(rows) == 30.0)

print("\nAll interval-merge checks passed.")
