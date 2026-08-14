'use client';

import { useState, useEffect, useCallback } from 'react';
import apiClient from '@/lib/api-client';

interface MetricDefinition {
  id: number;
  name: string;
  unit: string | null;
  unit_conversions: Record<string, number>;
  aliases: string[];
}

interface UnitPreference {
  id: number;
  metric_definition_id: number;
  metric_name: string;
  canonical_unit: string | null;
  preferred_unit: string;
}

export interface ConvertedMetric {
  value: number;
  unit: string;
}

/**
 * Hook that loads the user's unit preferences and metric definitions,
 * then provides a `convert` function to transform values to the user's
 * preferred display unit.
 *
 * unit_conversions maps: alternate_unit → factor where `1 canonical = factor alternate`
 * (e.g., for Weight with canonical "lb": {"kg": 0.453592} means 1 lb = 0.453592 kg)
 */
export function useUnitConversion() {
  const [definitions, setDefinitions] = useState<MetricDefinition[]>([]);
  const [preferences, setPreferences] = useState<Map<string, string>>(new Map()); // lowercase metric name → preferred unit
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [defsRes, prefsRes] = await Promise.allSettled([
        apiClient.get('/health/metrics/definitions'),
        apiClient.get('/users/me/unit-preferences'),
      ]);

      if (defsRes.status === 'fulfilled') {
        setDefinitions(defsRes.value.data || []);
      }

      if (prefsRes.status === 'fulfilled') {
        const prefs: UnitPreference[] = prefsRes.value.data || [];
        const prefMap = new Map<string, string>();
        for (const p of prefs) {
          prefMap.set(p.metric_name.toLowerCase(), p.preferred_unit);
        }
        setPreferences(prefMap);
      }
    } catch (err) {
      console.error('Failed to load unit conversion data:', err);
    } finally {
      setLoaded(true);
    }
  };

  /**
   * Find the metric definition for a given metric name (case-insensitive, checks aliases too).
   */
  const findDefinition = useCallback(
    (metricName: string): MetricDefinition | undefined => {
      const lower = metricName.toLowerCase();
      return (
        definitions.find((d) => d.name.toLowerCase() === lower) ||
        definitions.find((d) =>
          d.aliases?.some((a) => a.toLowerCase() === lower)
        )
      );
    },
    [definitions]
  );

  /**
   * Convert a metric value to the user's preferred unit.
   *
   * @param metricName  The metric name (e.g., "Weight", "weight", "Glucose")
   * @param value       The numeric value in the given unit
   * @param unit        The unit the value is currently in
   * @returns           The converted value and display unit
   */
  const convert = useCallback(
    (metricName: string, value: number, unit: string): ConvertedMetric => {
      const def = findDefinition(metricName);
      if (!def || !def.unit) {
        return { value, unit };
      }

      const canonicalUnit = def.unit;
      const preferredUnit = preferences.get(def.name.toLowerCase()) || canonicalUnit;

      // If already in preferred unit, no conversion needed
      if (unit.toLowerCase() === preferredUnit.toLowerCase()) {
        return { value, unit: preferredUnit };
      }

      const conversions = def.unit_conversions || {};

      // Step 1: Convert source unit → canonical unit
      let canonicalValue = value;
      if (unit.toLowerCase() !== canonicalUnit.toLowerCase()) {
        // Look up the source unit in conversions
        // conversions[altUnit] = factor means: 1 canonical = factor alt
        // So: canonical_value = alt_value / factor
        const factor = findConversionFactor(conversions, unit);
        if (factor == null) {
          return { value, unit }; // Can't convert, return as-is
        }
        canonicalValue = value / factor;
      }

      // Step 2: Convert canonical → preferred unit
      if (preferredUnit.toLowerCase() === canonicalUnit.toLowerCase()) {
        return { value: canonicalValue, unit: canonicalUnit };
      }

      const prefFactor = findConversionFactor(conversions, preferredUnit);
      if (prefFactor == null) {
        return { value: canonicalValue, unit: canonicalUnit };
      }

      return { value: canonicalValue * prefFactor, unit: preferredUnit };
    },
    [definitions, preferences, findDefinition]
  );

  /**
   * Get the display unit for a metric (the user's preferred unit, or canonical if no preference).
   */
  const getDisplayUnit = useCallback(
    (metricName: string, currentUnit: string): string => {
      const def = findDefinition(metricName);
      if (!def) return currentUnit;
      return preferences.get(def.name.toLowerCase()) || def.unit || currentUnit;
    },
    [definitions, preferences, findDefinition]
  );

  /**
   * Format a metric value for display, handling compound units like feet/inches.
   *
   * For simple units: returns the number formatted as a string.
   * For compound units (e.g., "feet"): returns "X'Y\"" format.
   *
   * @param value  The numeric value
   * @param unit   The display unit (after conversion)
   * @returns      A formatted display string
   */
  const formatValue = useCallback(
    (value: number | null, unit: string): string => {
      if (value == null) return '—';

      // Compound unit: feet → display as X'Y"
      if (unit.toLowerCase() === 'feet') {
        const totalInches = value * 12;
        const feet = Math.floor(value);
        const inches = Math.round(totalInches - feet * 12);
        // Handle rounding that pushes inches to 12
        if (inches === 12) return `${feet + 1}'0"`;
        return `${feet}'${inches}"`;
      }

      // Default: format number
      if (Number.isInteger(value)) return value.toLocaleString();
      return value.toFixed(1);
    },
    []
  );

  return { convert, getDisplayUnit, formatValue, findDefinition, loaded, refresh: loadData };
}

/**
 * Normalize common unit aliases and metric prefixes so the lookup succeeds
 * even when the stored unit doesn't exactly match the conversion map key
 * (e.g. "meters" vs "m", "kilograms" vs "kg", "centimeters" vs "cm").
 */
function normalizeUnit(unit: string): string {
  const lower = unit.toLowerCase().trim();

  // Common aliases
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
    oz: 'ounces',
    ounce: 'ounces',
    'fl oz': 'fluid ounces',
    'fluid oz': 'fluid ounces',
  };

  return aliases[lower] || lower;
}

/**
 * Find a conversion factor for a unit, case-insensitive and alias-aware.
 */
function findConversionFactor(
  conversions: Record<string, number>,
  unit: string
): number | null {
  const normalized = normalizeUnit(unit);
  for (const [key, factor] of Object.entries(conversions)) {
    if (normalizeUnit(key) === normalized) {
      return factor;
    }
  }
  return null;
}
