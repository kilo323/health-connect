"""Merge raw interval samples across data origins (multi-device dedup).

Google Health can report the same real-world activity from several devices at
once (e.g. watch + phone). Their intervals overlap and each carries the device's
own count for that window, so summing every point double-counts the overlap —
the totals then exceed what the Google Health app shows (it merges the device
streams itself).

The merge model is *dominant origin*: within a merge scope (one label, one
local day) the origin with the largest total wins every time slice it covers,
and the other origins contribute only where the dominant one has no data.
This matches Google's own dedup almost exactly — verified against live
dailyRollUp totals (2026-11-01): dominant-merge landed within 0.2-6% of Google
where naive sums overshot by 45-130% and a per-slice max envelope still
overshot by 7-54% (it composites each device's noise). A single-origin input
passes through unchanged, so data from one device is never reshaped.

Pure stdlib on purpose: ``scripts/test_interval_dedup.py`` exercises it without
the app's dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class IntervalSample:
    """One device's count for a half-open interval [start, end)."""
    label: str
    value: float
    start: datetime
    end: datetime
    origin: str = "unknown"


def merge_interval_samples(
    samples: list[IntervalSample],
) -> list[tuple[str, float, datetime]]:
    """Merge overlapping samples per label.

    Returns ``(label, value, start)`` rows sorted by ``(label, start)``, with
    the merged value covering the coalesced span. Zero-duration samples and
    labels with a single origin pass through as-is (single-origin rows keep
    their exact values and starts).
    """
    by_label: dict[str, list[IntervalSample]] = {}
    for s in samples:
        by_label.setdefault(s.label, []).append(s)

    rows: list[tuple[str, float, datetime]] = []
    for label, group in sorted(by_label.items()):
        pointlike = [s for s in group if s.end <= s.start]
        intervals = [s for s in group if s.end > s.start]
        # Unmergeable (zero-duration) samples pass through untouched.
        rows.extend((label, s.value, s.start) for s in pointlike)

        origins = {s.origin for s in intervals}
        if len(origins) <= 1:
            rows.extend((label, s.value, s.start) for s in intervals)
            continue

        rows.extend(_merge_multi_origin(label, intervals))

    rows.sort(key=lambda r: (r[0], r[2]))
    return rows


def _merge_multi_origin(
    label: str, intervals: list[IntervalSample]
) -> list[tuple[str, float, datetime]]:
    """Sweep-line merge: per slice the dominant origin wins where present.

    'Dominant' is the origin with the largest total value in this merge scope
    (ties broken by name); the others contribute only where the dominant origin
    has no coverage.
    """
    totals: dict[str, float] = {}
    for s in intervals:
        totals[s.origin] = totals.get(s.origin, 0.0) + s.value
    rank = {o: i for i, o in enumerate(sorted(totals, key=lambda o: (-totals[o], o)))}

    # Per-origin contribution rate (value per second) is constant between
    # boundaries. Events: (time, origin, +rate at start / -rate at end).
    events: list[tuple[datetime, str, float]] = []
    for s in intervals:
        rate = s.value / (s.end - s.start).total_seconds()
        events.append((s.start, s.origin, rate))
        events.append((s.end, s.origin, -rate))
    events.sort(key=lambda e: e[0])

    rows: list[tuple[str, float, datetime]] = []
    active: dict[str, float] = {}
    run_origin: str | None = None
    run_value = 0.0
    run_start: datetime | None = None
    prev_t: datetime | None = None

    def close_run():
        nonlocal run_origin, run_value, run_start
        if run_origin is not None and run_start is not None:
            rows.append((label, round(run_value, 6), run_start))
        run_origin, run_value, run_start = None, 0.0, None

    i = 0
    while i < len(events):
        t = events[i][0]
        # Attribute the span [prev_t, t) before applying events at t.
        if prev_t is not None and t > prev_t:
            present = [o for o, r in active.items() if r > 0]
            if present:
                winner = min(present, key=lambda o: rank[o])
                span = (t - prev_t).total_seconds()
                if winner == run_origin:
                    run_value += active[winner] * span
                else:
                    close_run()
                    run_origin = winner
                    run_value = active[winner] * span
                    run_start = prev_t
            else:
                close_run()
        while i < len(events) and events[i][0] == t:
            _, origin, delta = events[i]
            active[origin] = active.get(origin, 0.0) + delta
            if active[origin] <= 1e-12:
                active.pop(origin, None)
            i += 1
        prev_t = t

    close_run()
    return rows
