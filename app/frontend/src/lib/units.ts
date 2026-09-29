/**
 * Unit conversion primitives shared by the read path and the admin UI.
 *
 * `unit_conversions` on a metric definition maps an alternate unit to a factor
 * in the *alternates per canonical* direction: `1 canonical = factor × alternate`.
 * Mass and length are pure ratios. Temperature is not — °F = °C × 9/5 + 32 — so
 * a temperature pair also needs an additive term, which `conversionOffset()`
 * supplies from the same physical constants the backend uses
 * (app/services/unit_systems.py).
 *
 * Full transform, in the direction the map is written:
 *     alternate_value = canonical_value * factor + offset(canonical, alternate)
 */

const TEMPERATURE = 'temperature';

/** (scale, zero) per temperature unit, relative to Kelvin. */
const TEMPERATURE_SCALE: Record<string, { scale: number; zero: number }> = {
  celsius: { scale: 1, zero: 273.15 },
  fahrenheit: { scale: 5 / 9, zero: 459.67 * (5 / 9) },
};

/**
 * Lowercase a unit and fold aliases onto a single canonical spelling so the
 * lookup succeeds even when the stored unit doesn't exactly match the
 * conversion map key (e.g. "meters" vs "m", "kilograms" vs "kg"). Mirrors
 * normalize_unit() in app/services/unit_systems.py.
 */
export function normalizeUnit(unit: string): string {
  const lower = unit.toLowerCase().trim();

  const aliases: Record<string, string> = {
    m: 'meters',
    meter: 'meters',
    metres: 'meters',
    metre: 'meters',
    km: 'kilometers',
    kilometers: 'kilometers',
    cm: 'centimeters',
    centimeters: 'centimeters',
    centimetres: 'centimeters',
    mm: 'millimeters',
    millimeters: 'millimeters',
    g: 'grams',
    gram: 'grams',
    kg: 'kilograms',
    kilograms: 'kilograms',
    lbs: 'pounds',
    lb: 'pounds',
    pounds: 'pounds',
    in: 'inches',
    inch: 'inches',
    ft: 'feet',
    foot: 'feet',
    mi: 'miles',
    mile: 'miles',
    yd: 'yards',
    yard: 'yards',
    oz: 'ounces',
    ounce: 'ounces',
    'fl oz': 'fluid ounces',
    'fluid oz': 'fluid ounces',
    // Temperature, including the ASCII spellings of the degree sign.
    '°c': 'celsius',
    'o c': 'celsius',
    'deg c': 'celsius',
    'degrees celsius': 'celsius',
    c: 'celsius',
    '°f': 'fahrenheit',
    'o f': 'fahrenheit',
    'deg f': 'fahrenheit',
    'degrees fahrenheit': 'fahrenheit',
    f: 'fahrenheit',
  };

  return aliases[lower] || lower;
}

/** Whether this unit needs an additive term as well as a factor. */
export function isAffineUnit(unit: string): boolean {
  return normalizeUnit(unit) in TEMPERATURE_SCALE;
}

/**
 * The additive term for `alternate = canonical * factor + offset(canonical, alternate)`.
 *
 * Zero for every pure ratio, so mass/length behave exactly as before. For
 * temperature this is the part a factor cannot carry: canonical °F → °C needs
 * −17.777…, canonical °C → °F needs +32. Applying only the factor would render
 * 35.95 °C as 64.7 °F instead of 96.7 °F.
 */
export function conversionOffset(canonicalUnit: string, alternateUnit: string): number {
  const canonical = normalizeUnit(canonicalUnit);
  const alternate = normalizeUnit(alternateUnit);

  const from = TEMPERATURE_SCALE[canonical];
  const to = TEMPERATURE_SCALE[alternate];
  if (!from || !to) return 0;

  return (from.zero - to.zero) / to.scale;
}

export { TEMPERATURE };

/**
 * Find a conversion factor for a unit, case-insensitive and alias-aware.
 * Returns null when the pair has no factor, i.e. is not convertible.
 */
export function findConversionFactor(
  conversions: Record<string, number>,
  unit: string
): number | null {
  const normalized = normalizeUnit(unit);
  for (const [key, factor] of Object.entries(conversions)) {
    if (normalizeUnit(key) === normalized) {
      return typeof factor === 'number' ? factor : null;
    }
  }
  return null;
}
