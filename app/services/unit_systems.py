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


# Per unit, (scale, offset) describing how it relates to its dimension's base
# unit:  <base> = value * scale + offset. The base is kg for mass, m for length
# and **K for temperature**. Mass and length have a zero offset because they
# are pure ratios; temperature does not, which is the whole reason a °F/°C
# conversion needs an additive term as well as a factor.
_TO_BASE: dict[str, tuple[float, float]] = {
    "kilograms": (1.0, 0.0),
    "grams": (1e-3, 0.0),
    "milligrams": (1e-6, 0.0),
    "pounds": (0.45359237, 0.0),
    "ounces": (0.028349523125, 0.0),
    "meters": (1.0, 0.0),
    "kilometers": (1e3, 0.0),
    "centimeters": (1e-2, 0.0),
    "millimeters": (1e-3, 0.0),
    "miles": (1609.344, 0.0),
    "feet": (0.3048, 0.0),
    "inches": (0.0254, 0.0),
    "yards": (0.9144, 0.0),
    # 0 °C = 273.15 K; 0 °F = 459.67 °R = 255.372… K
    "celsius": (1.0, 273.15),
    "fahrenheit": (5.0 / 9.0, 459.67 * 5.0 / 9.0),
}


def _base_params(unit: str | None) -> tuple[float, float] | None:
    return _TO_BASE.get(normalize_unit(unit))


def is_affine(unit: str | None) -> bool:
    """Whether converting this unit needs an additive term as well as a factor.

    Only temperature. °F = °C × 9/5 + 32, so scaling alone is off by a constant
    32 — treating a °C/°F pair as a bare ratio renders 35.95 °C as 64.7 °F
    instead of 96.7 °F. Callers must apply :func:`default_offset` alongside the
    factor for these units.
    """
    params = _base_params(unit)
    return params is not None and params[1] != 0.0


def _ratio(canonical_unit: str, alternate_unit: str) -> float | None:
    """The bare ratio between two units of the same dimension, or None."""
    canonical = _base_params(canonical_unit)
    alternate = _base_params(alternate_unit)
    if canonical is None or alternate is None:
        return None
    if unit_class(canonical_unit) != unit_class(alternate_unit):
        return None
    return canonical[0] / alternate[0]


def default_factor(canonical_unit: str, alternate_unit: str) -> float | None:
    """The factor for `1 canonical_unit = factor * alternate_unit`, or None.

    None when the two units are of different or unknown dimensions. Note this is
    the *alternates per canonical* direction, which is what ``unit_conversions``
    stores and what the admin UI renders as `unit × factor = 1 canonical`. For
    temperature this is only part of the story — pair it with
    :func:`default_offset`.
    """
    return _ratio(canonical_unit, alternate_unit)


def default_offset(canonical_unit: str, alternate_unit: str) -> float | None:
    """The additive term for `alternate_value = canonical_value * factor + offset`.

    Zero for every pure ratio (mass, length). For temperature it carries the
    part a factor cannot: canonical °F → °C needs -17.777…, canonical °C → °F
    needs +32. None when the pair is not a known standard pair.
    """
    canonical = _base_params(canonical_unit)
    alternate = _base_params(alternate_unit)
    if canonical is None or alternate is None:
        return None
    if unit_class(canonical_unit) != unit_class(alternate_unit):
        return None
    return (canonical[1] - alternate[1]) / alternate[0]


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
) -> dict[str, float | None]:
    """Conversion factors to repair or add in a definition's `unit_conversions`.

    Keyed by the exact unit string to write. A value of ``None`` writes a JSON
    null, meaning "declared but not convertible".

    * **Added** — a unit the sync actually stores that no numeric factor covers.
    * **Filled** — a declared key whose value is null or non-numeric.
    * **Corrected** — a stored factor that is materially wrong for a standard
      unit pair (e.g. Weight's ``lb: 0.453592``, the reciprocal of the real
      2.20462). Only when the pair has an exact physical value, so a genuinely
      non-standard conversion in ``unit_conversions`` is never rewritten.
      Factors that merely round the exact value (Distance's ``miles:
      0.621371``) are left alone.

    Only the factor is stored. The additive term a temperature pair also needs
    comes from :func:`default_offset`, which is a physical constant rather than
    per-definition data.
    """
    if not canonical_unit or unit_class(canonical_unit) is None:
        return {}

    canonical = normalize_unit(canonical_unit)
    numeric = _numeric_factors(conversions)
    fixes: dict[str, float | None] = {}

    for key, value in conversions.items():
        current = numeric.get(normalize_unit(key))
        exact = default_factor(canonical_unit, key)
        if exact is None:
            # Not a standard pair: never touch an admin-supplied conversion.
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fixes[key] = exact
            continue
        if _matches(current, exact):
            continue  # Already correct (possibly rounded) — leave it alone.
        fixes[key] = exact

    for raw in sorted(stored or ()):
        normalized = normalize_unit(raw)
        if normalized == canonical or unit_class(raw) != unit_class(canonical_unit):
            continue
        if normalized in numeric:
            continue  # Covered above (correct, or about to be corrected)
        factor = default_factor(canonical_unit, raw)
        if factor is not None:
            fixes[raw] = factor

    return {unit: value for unit, value in fixes.items() if conversions.get(unit) != value}


def _matches(current: float | None, exact: float) -> bool:
    """Whether a stored factor already agrees with the exact value.

    Tolerant of rounding: a value within 0.01% counts as correct so a
    deliberately shortened factor is not rewritten every startup.
    """
    if current is None or exact == 0:
        return False
    return abs(current - exact) <= abs(exact) * 1e-4


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
