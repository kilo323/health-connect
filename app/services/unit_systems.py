"""Mapping between unit names and the metric / imperial measurement systems.

Units themselves are data (``metric_definitions.unit`` plus the
``unit_conversions`` map), but deciding *which* of those units belongs to which
measurement system is a property of the unit name, so it lives here.

Nothing in this module looks at metric semantics -- it never inspects a metric
name. A definition is only affected when its canonical unit is a recognised
mass, length or temperature unit and another unit of the same kind is reachable
through ``unit_conversions``.
"""

from __future__ import annotations

import json

METRIC = "metric"
IMPERIAL = "imperial"
UNIT_SYSTEMS = (METRIC, IMPERIAL)

MASS = "mass"
LENGTH = "length"
TEMPERATURE = "temperature"

# Candidate units per (class, system), most-preferred first. A definition uses
# the first candidate that is actually reachable through unit_conversions, so
# this ordering only matters when a metric offers several of them.
_SYSTEM_CANDIDATES: dict[str, dict[str, tuple[str, ...]]] = {
    MASS: {
        METRIC: ("kilograms", "grams", "milligrams"),
        IMPERIAL: ("pounds", "ounces"),
    },
    LENGTH: {
        # A metric stored in km or cm still reads best as m, so metric leads
        # with meters; a distance metric offering only km falls through to it.
        METRIC: ("meters", "kilometers", "centimeters", "millimeters"),
        IMPERIAL: ("miles", "feet", "inches", "yards"),
    },
    TEMPERATURE: {
        METRIC: ("celsius",),
        IMPERIAL: ("fahrenheit",),
    },
}

# Common spellings, abbreviations and Unicode degree variants for the units we
# classify. Mirrors normalizeUnit() in the frontend's useUnitConversion hook so
# both sides agree on which unit a definition already uses.
_UNIT_ALIASES: dict[str, str] = {
    # mass
    "g": "grams",
    "gm": "grams",
    "gram": "grams",
    "gramme": "grams",
    "grammes": "grams",
    "kg": "kilograms",
    "kilo": "kilograms",
    "kilos": "kilograms",
    "kilogram": "kilograms",
    "kilogramme": "kilograms",
    "kilogrammes": "kilograms",
    "mg": "milligrams",
    "milligram": "milligrams",
    "lb": "pounds",
    "lbs": "pounds",
    "pound": "pounds",
    "oz": "ounces",
    "ounce": "ounces",
    # length
    "mm": "millimeters",
    "millimetre": "millimeters",
    "millimetres": "millimeters",
    "cm": "centimeters",
    "centimetre": "centimeters",
    "centimetres": "centimeters",
    "m": "meters",
    "meter": "meters",
    "metre": "meters",
    "metres": "meters",
    "km": "kilometers",
    "kilometer": "kilometers",
    "kilometre": "kilometers",
    "kilometres": "kilometers",
    "mi": "miles",
    "mile": "miles",
    "in": "inches",
    "inch": "inches",
    "yd": "yards",
    "yard": "yards",
    "ft": "feet",
    "foot": "feet",
    "'": "feet",
    "\"": "inches",
    # temperature (DEGREE SIGN, and the ASCII "deg" spellings)
    "°c": "celsius",
    "o c": "celsius",
    "deg c": "celsius",
    "degrees celsius": "celsius",
    "c": "celsius",
    "°f": "fahrenheit",
    "o f": "fahrenheit",
    "deg f": "fahrenheit",
    "degrees fahrenheit": "fahrenheit",
    "f": "fahrenheit",
}

_UNIT_CLASS: dict[str, str] = {
    "kilograms": MASS,
    "grams": MASS,
    "milligrams": MASS,
    "pounds": MASS,
    "ounces": MASS,
    "meters": LENGTH,
    "kilometers": LENGTH,
    "centimeters": LENGTH,
    "millimeters": LENGTH,
    "miles": LENGTH,
    "feet": LENGTH,
    "inches": LENGTH,
    "yards": LENGTH,
    "celsius": TEMPERATURE,
    "fahrenheit": TEMPERATURE,
}


def normalize_unit(unit: str | None) -> str:
    """Lowercase a unit and fold aliases onto a single canonical spelling."""
    if not unit:
        return ""
    key = unit.strip().lower()
    return _UNIT_ALIASES.get(key, key)


def unit_class(unit: str | None) -> str | None:
    """Return the dimension of a unit (mass/length/temperature) or None.

    Unit-agnostic units such as ``bpm``, ``%``, ``count``, ``minutes`` and
    ``kcal`` deliberately return None: the measurement system does not apply to
    them.
    """
    return _UNIT_CLASS.get(normalize_unit(unit))


# How many base units (kg / m / °C) one of these units is worth. Exact values,
# used to derive every factor so the table cannot drift out of consistency.
_TO_BASE: dict[str, float] = {
    "kilograms": 1.0,
    "grams": 1e-3,
    "milligrams": 1e-6,
    "pounds": 0.45359237,
    "ounces": 0.028349523125,
    "meters": 1.0,
    "kilometers": 1e3,
    "centimeters": 1e-2,
    "millimeters": 1e-3,
    "miles": 1609.344,
    "feet": 0.3048,
    "inches": 0.0254,
    "yards": 0.9144,
    "celsius": 1.0,
    "fahrenheit": 1.0 / 1.8,
}


def default_factor(canonical_unit: str, alternate_unit: str) -> float | None:
    """The factor for `1 canonical_unit = factor * alternate_unit`, or None.

    None when the two units are of different or unknown dimensions. Note this
    is the *alternates per canonical* direction, matching what
    ``unit_conversions`` stores.
    """
    canonical = normalize_unit(canonical_unit)
    alternate = normalize_unit(alternate_unit)
    if canonical not in _TO_BASE or alternate not in _TO_BASE:
        return None
    if unit_class(canonical) != unit_class(alternate):
        return None
    return _TO_BASE[canonical] / _TO_BASE[alternate]


def stored_units() -> set[str]:
    """The unit the Google sync writes into health_metrics.unit for each metric.

    Derived from the scheduler's own map so a conversion map can always cover
    the unit the value is actually stored in. Imported lazily: the scheduler
    pulls in APScheduler, which the read path has no reason to load.
    """
    from .scheduler import UNIT_MAP

    return set(UNIT_MAP.values())


def seed_missing_factors(
    canonical_unit: str | None,
    conversions: dict,
    stored: set[str] | None = None,
) -> dict[str, float]:
    """Conversion factors to add to a definition's `unit_conversions` map.

    Two kinds of entry are produced, both keyed by the exact unit string to
    write:

    * a unit that is already a key in the map but whose value is null or
      non-numeric — it is declared but cannot convert;
    * a unit the sync actually stores, when no numeric factor covers it (the
      key is absent, or declared null).

    Existing numeric factors are never returned, so an admin-supplied value is
    never overwritten and this stays safe to run on every startup.
    """
    if not canonical_unit or unit_class(canonical_unit) is None:
        return {}

    known = {normalize_unit(unit): unit for unit in conversions}
    canonical = normalize_unit(canonical_unit)
    fixes: dict[str, float] = {}

    def _has_numeric_factor(unit: str) -> bool:
        return unit in known and _numeric_factors(conversions).get(unit) is not None

    for key, value in conversions.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            factor = default_factor(canonical_unit, key)
            if factor is not None:
                fixes[key] = factor

    for raw in sorted(stored or ()):
        normalized = normalize_unit(raw)
        if normalized == canonical or unit_class(raw) != unit_class(canonical_unit):
            continue
        if _has_numeric_factor(raw):
            continue
        factor = default_factor(canonical_unit, raw)
        if factor is not None:
            fixes[raw] = factor

    return fixes


def parse_conversions(unit_conversions: str | dict | None) -> dict[str, float | None]:
    """Parse the unit_conversions JSON column into {unit: factor}.

    Factors are kept as-is; a non-numeric value (or a missing entry) means the
    pair is not actually convertible and is treated as such by
    :func:`system_unit_for`.
    """
    if not unit_conversions:
        return {}
    data = unit_conversions
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(data, dict):
        return {}
    return {u: f for u, f in data.items() if isinstance(u, str) and u}


def _numeric_factors(conversions: dict[str, float | None]) -> dict[str, float]:
    """Conversion factors that are actually usable, keyed by normalized unit.

    The map is `alternate_unit -> factor` where `1 canonical = factor alternate`.
    A null (or non-numeric) factor means the pair is declared but unconvertible,
    so it is dropped here rather than producing an unconverted value later.
    """
    return {
        normalize_unit(unit): factor
        for unit, factor in conversions.items()
        if not isinstance(factor, bool) and isinstance(factor, (int, float))
    }


def system_unit_for(
    canonical_unit: str | None,
    conversions: dict[str, float | None],
    system: str | None,
) -> str | None:
    """The unit this metric should display in under `system`, or None.

    Returns None when the measurement system does not apply: an unknown system,
    a unit-agnostic metric, or no unit of the requested system is reachable
    through `conversions`. The canonical unit is only returned when it already
    belongs to the requested system, so callers can compare against it.
    """
    if system not in UNIT_SYSTEMS:
        return None
    dimension = unit_class(canonical_unit)
    if dimension is None:
        return None

    candidates = _SYSTEM_CANDIDATES[dimension][system]

    # Already in the requested system -- nothing to convert.
    for candidate in candidates:
        if normalize_unit(candidate) == normalize_unit(canonical_unit):
            return canonical_unit

    # Otherwise the first candidate of this system that is actually convertible.
    factors = _numeric_factors(conversions)
    for candidate in candidates:
        if candidate in factors:
            for unit in conversions:
                if normalize_unit(unit) == candidate:
                    return unit
    return None


def effective_unit(
    canonical_unit: str | None,
    conversions: dict[str, float | None],
    preferred_unit: str | None,
    system: str | None,
) -> str | None:
    """Resolve what a metric should display in.

    An explicit per-metric preference always wins; otherwise the global
    measurement system applies; otherwise the canonical unit stands.
    """
    if preferred_unit and normalize_unit(preferred_unit) in {
        normalize_unit(canonical_unit),
        *(normalize_unit(unit) for unit in conversions),
    }:
        return preferred_unit
    return system_unit_for(canonical_unit, conversions, system) or canonical_unit
