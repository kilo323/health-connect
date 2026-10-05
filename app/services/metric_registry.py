"""Metric cadence/aggregation registry — the single source of truth for how a
metric collapses over time and which view it belongs in.

Two properties per metric:

``aggregation``  How granular rows become one daily value:
                 - ``sum``        steps, distance, calories, minutes, sleep
                 - ``avg``        averages (e.g. an average series)
                 - ``avg_minmax`` heart rate: day = avg, with min/max band
                 - ``latest``     snapshot metrics (weight, labs, SpO2)

``cadence``      Which view the metric belongs in:
                 - ``intraday``   meaningful at hourly/minute resolution
                 - ``daily``      day-over-day is the only honest view
                 - ``event``      sparse point-in-time tests; chart as points,
                                  never as an interpolated line

Precedence: ``metric_definitions.aggregation/cadence`` (admin-editable, seeded
from ``DEFAULTS``) wins; metrics without a definition row fall back to
``infer_aggregation``/``infer_cadence`` so manual entries still behave.

``COMPANIONS`` maps an ``avg_minmax`` parent series to the daily/hourly series
that carry its parts. Heart rate stores raw samples under ``Heart Rate`` while
Google rollups and compaction write ``Heart Rate (Average)/(Minimum)/(Maximum)``
separate metric_types (a single row holds a single value). The read path merges
them back into one continuous series with a min/max band.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.health_data import MetricDefinition

AGGREGATIONS = ("sum", "avg", "avg_minmax", "latest")
CADENCES = ("intraday", "daily", "event")

# Canonical definition name -> defaults, seeded into metric_definitions on
# startup and used as the fallback for metrics with no definition row.
DEFAULTS: dict[str, dict[str, str]] = {
    # ── intraday: hourly/minute detail is the point ──────────────────────────
    "Heart Rate": {"aggregation": "avg_minmax", "cadence": "intraday"},
    "Steps": {"aggregation": "sum", "cadence": "intraday"},
    "Distance": {"aggregation": "sum", "cadence": "intraday"},
    "Active Minutes (Light)": {"aggregation": "sum", "cadence": "intraday"},
    "Active Minutes (Moderate)": {"aggregation": "sum", "cadence": "intraday"},
    "Active Minutes (Vigorous)": {"aggregation": "sum", "cadence": "intraday"},
    "Heart Minutes (Fat Burn)": {"aggregation": "sum", "cadence": "intraday"},
    "Heart Minutes (Cardio)": {"aggregation": "sum", "cadence": "intraday"},
    "Heart Minutes (Peak)": {"aggregation": "sum", "cadence": "intraday"},
    # ── daily: day-over-day only ─────────────────────────────────────────────
    "Calories": {"aggregation": "sum", "cadence": "daily"},
    "Sleep": {"aggregation": "sum", "cadence": "daily"},
    "Oxygen Saturation": {"aggregation": "latest", "cadence": "daily"},
    # Per-minute SpO2 samples (distinct from the daily "Oxygen Saturation").
    "Oxygen Saturation (Raw)": {"aggregation": "avg", "cadence": "intraday"},
    "Weight": {"aggregation": "latest", "cadence": "daily"},
    "Body Fat Percentage": {"aggregation": "latest", "cadence": "daily"},
    # HRV (RMSSD): intraday samples; the honest daily value is an average.
    "Heart Rate Variability": {"aggregation": "avg", "cadence": "intraday"},
    # Derived heart-rate parts: each row is already an aggregate of that day.
    "Heart Rate (Average)": {"aggregation": "avg", "cadence": "intraday"},
    "Heart Rate (Minimum)": {"aggregation": "latest", "cadence": "intraday"},
    "Heart Rate (Maximum)": {"aggregation": "latest", "cadence": "intraday"},
    # ── event: sparse point-in-time tests ────────────────────────────────────
    "Body Temperature": {"aggregation": "latest", "cadence": "event"},
    "VO2 Max": {"aggregation": "latest", "cadence": "event"},
    # Workout sub-metrics (one row per workout session, at its start time).
    "Workout Duration": {"aggregation": "latest", "cadence": "event"},
    "Workout Calories": {"aggregation": "latest", "cadence": "event"},
    "Workout Distance": {"aggregation": "latest", "cadence": "event"},
    "Workout Avg Heart Rate": {"aggregation": "latest", "cadence": "event"},
}

# Lab/panel metrics from document analysis: meaningful on the date of the test.
# Anything not listed defaults to cadence "daily" (see infer_cadence).
EVENT_METRICS = {
    "hemoglobin a1c", "glucose", "insulin", "ldl cholesterol",
    "hdl cholesterol", "triglycerides", "cholesterol", "crp, high sensitivity",
    "egfr", "creatinine", "alt", "hemoglobin", "wbc", "platelet count", "tsh",
    "sodium", "potassium", "vitamin d, 25-hydroxy", "vitamin b12", "ferritin",
    "bmi", "height",
}

# avg_minmax parent -> the series that carry its parts. Keys are canonical
# definition names; lookups go through companions_for() (case-insensitive).
COMPANIONS: dict[str, dict[str, str]] = {
    "Heart Rate": {
        "avg": "Heart Rate (Average)",
        "min": "Heart Rate (Minimum)",
        "max": "Heart Rate (Maximum)",
    },
}

_COMPANIONS_LOWER: dict[str, dict[str, str]] = {
    k.lower(): v for k, v in COMPANIONS.items()
}

# Companion metric_types are folded into their parent series by the read path,
# so listing them separately would show Heart Rate four times on the dashboard.
# Values are LOWERCASE parent names (callers compare against lowercased types).
COMPANION_TYPES: dict[str, str] = {
    name.lower(): parent.lower()
    for parent, parts in COMPANIONS.items()
    for name in parts.values()
}  # {"heart rate (average)": "heart rate", ...}


def is_companion(metric_type: str) -> bool:
    return (metric_type or "").lower() in COMPANION_TYPES


def parent_of(metric_type: str) -> str | None:
    """Lowercase parent metric_type for a companion series, else None."""
    return COMPANION_TYPES.get((metric_type or "").lower())


def companions_for(metric_type: str) -> dict[str, str] | None:
    """{"avg"/"min"/"max": canonical companion name} for an avg_minmax metric."""
    return _COMPANIONS_LOWER.get((metric_type or "").lower())


def infer_aggregation(metric_type: str) -> str:
    """Fallback aggregation for metric types with no definition row."""
    t = (metric_type or "").lower()
    if t in COMPANION_TYPES:
        # The companion series store one aggregate per row already.
        return "avg" if "average" in t or "(avg" in t else "latest"
    if "heart rate" in t and "(" not in t:
        return "avg_minmax"
    if any(k in t for k in ("steps", "distance", "calor", "minutes", "sleep")):
        return "sum"
    return "latest"


def infer_cadence(metric_type: str) -> str:
    """Fallback cadence for metric types with no definition row."""
    t = (metric_type or "").lower()
    if t in COMPANION_TYPES or t in _COMPANIONS_LOWER:
        return "intraday"
    if t in EVENT_METRICS:
        return "event"
    if any(k in t for k in (
        "heart rate", "steps", "distance", "minutes", "active", "pulse",
    )):
        return "intraday"
    return "daily"


def _normalise(agg: str | None, cadence: str | None, name: str) -> tuple[str, str]:
    default = DEFAULTS.get(name, {})
    a = agg if agg in AGGREGATIONS else default.get("aggregation")
    if a not in AGGREGATIONS:
        a = infer_aggregation(name)
    c = cadence if cadence in CADENCES else default.get("cadence")
    if c not in CADENCES:
        c = infer_cadence(name)
    return a, c


def defaults_for(name: str) -> tuple[str, str]:
    """(aggregation, cadence) defaults for a canonical metric name."""
    return _normalise(None, None, name)


async def load_registry(db: AsyncSession) -> dict[str, dict[str, str]]:
    """Lowercased metric_type/alias -> {name, aggregation, cadence}.

    Covers definition names AND aliases because raw rows are stored under the
    canonical name the normalizer resolved at ingest time, while manual entries
    may still use a raw label.
    """
    result = await db.execute(
        select(MetricDefinition.name, MetricDefinition.aliases,
               MetricDefinition.aggregation, MetricDefinition.cadence))
    registry: dict[str, dict[str, str]] = {}
    for name, aliases, agg, cadence in result.all():
        a, c = _normalise(agg, cadence, name)
        entry = {"name": name, "aggregation": a, "cadence": c}
        registry[name.lower()] = entry
        if aliases:
            try:
                parsed = json.loads(aliases)
            except (json.JSONDecodeError, TypeError):
                parsed = []
            if isinstance(parsed, list):
                for alias in parsed:
                    if isinstance(alias, str) and alias.strip():
                        registry.setdefault(alias.strip().lower(), entry)
    return registry


def lookup(registry: dict[str, dict[str, str]], metric_type: str) -> dict[str, str]:
    """Registry entry for a metric_type, falling back to inference."""
    entry = registry.get((metric_type or "").lower())
    if entry:
        return entry
    return {
        "name": metric_type,
        "aggregation": infer_aggregation(metric_type),
        "cadence": infer_cadence(metric_type),
    }
