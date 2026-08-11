'use client';

import { useEffect, useState, useMemo } from 'react';
import AuthLayout from '@/components/AuthLayout';
import MetricCard from '@/components/MetricCard';
import Sparkline from '@/components/Sparkline';
import {
  Activity, Loader2, LayoutGrid, List, ChevronDown, ChevronRight,
  TrendingUp, TrendingDown, Minus, CheckCircle, AlertTriangle, X,
  Settings2, RotateCcw,
} from 'lucide-react';
import apiClient from '@/lib/api-client';
import { useUnitConversion } from '@/hooks/useUnitConversion';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, ReferenceArea,
} from 'recharts';

// ── Types ────────────────────────────────────────────────────────────────────

interface ReferenceRange {
  sex: string | null;
  age_min: number | null;
  age_max: number | null;
  low: number | null;
  high: number | null;
  operator: string | null;
  unit: string | null;
  source: string | null;
}

interface MetricDefinition {
  id: number;
  name: string;
  category: string | null;
  unit: string | null;
  data_type: string;
  description: string | null;
  aliases: string[];
  reference_ranges: ReferenceRange[];
  unit_conversions: Record<string, number>;
}

interface MetricSummary {
  metric_type: string;
  latest_value: number;
  unit: string;
  recorded_at: string;
  date: string; // "YYYY-MM-DD" format
  trend: 'up' | 'down' | 'flat' | null;
  trend_pct: number | null;
  recent_avg: number | null;
  prior_avg: number | null;
}

interface TimeSeriesPoint {
  date: string;
  value: number;
  unit: string;
  source: string;
}

interface ReportData {
  period_days: number;
  summary: MetricSummary[];
  time_series: Record<string, TimeSeriesPoint[]>;
}

// ── Core Health Metrics Configuration ────────────────────────────────────────

interface MetricConfig {
  name: string;
  category: string;
  priority: number;
}

const CORE_METRICS: MetricConfig[] = [
  { name: 'Hemoglobin A1c', category: 'Metabolic & Diabetes', priority: 1 },
  { name: 'Glucose', category: 'Metabolic & Diabetes', priority: 2 },
  { name: 'Insulin', category: 'Metabolic & Diabetes', priority: 3 },
  { name: 'LDL Cholesterol', category: 'Cardiovascular', priority: 1 },
  { name: 'HDL Cholesterol', category: 'Cardiovascular', priority: 2 },
  { name: 'Triglycerides', category: 'Cardiovascular', priority: 3 },
  { name: 'Cholesterol', category: 'Cardiovascular', priority: 4 },
  { name: 'CRP, High Sensitivity', category: 'Cardiovascular', priority: 5 },
  { name: 'eGFR', category: 'Kidney', priority: 1 },
  { name: 'Creatinine', category: 'Kidney', priority: 2 },
  { name: 'ALT', category: 'Liver', priority: 1 },
  { name: 'Hemoglobin', category: 'Blood Health', priority: 1 },
  { name: 'WBC', category: 'Blood Health', priority: 2 },
  { name: 'Platelet Count', category: 'Blood Health', priority: 3 },
  { name: 'TSH', category: 'Thyroid', priority: 1 },
  { name: 'Sodium', category: 'Electrolytes', priority: 1 },
  { name: 'Potassium', category: 'Electrolytes', priority: 2 },
  { name: 'Vitamin D, 25-Hydroxy', category: 'Nutrients', priority: 1 },
  { name: 'Vitamin B12', category: 'Nutrients', priority: 2 },
  { name: 'Ferritin', category: 'Nutrients', priority: 3 },
  { name: 'Weight', category: 'Body', priority: 1 },
  { name: 'Body Fat Percentage', category: 'Body', priority: 2 },
  { name: 'BMI', category: 'Body', priority: 3 },
];

const CATEGORY_ORDER = [
  'Metabolic & Diabetes',
  'Cardiovascular',
  'Kidney',
  'Liver',
  'Blood Health',
  'Thyroid',
  'Electrolytes',
  'Nutrients',
  'Body',
];

const CATEGORY_ICONS: Record<string, string> = {
  'Metabolic & Diabetes': '🩸',
  'Cardiovascular': '❤️',
  'Kidney': '🫘',
  'Liver': '🫁',
  'Blood Health': '🔴',
  'Thyroid': '🦋',
  'Electrolytes': '⚡',
  'Nutrients': '💊',
  'Body': '⚖️',
};

/** Default dashboard metric names derived from CORE_METRICS. */
const DEFAULT_METRIC_NAMES: string[] = CORE_METRICS.map((m) => m.name);

// ── Time Range Filters ───────────────────────────────────────────────────────
// `days` is sent to the backend /health/reports/overview endpoint. A value of 0
// is a sentinel meaning "all time" (the backend will omit the date filter).
const TIME_RANGES: Array<{ days: number; label: string }> = [
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
  { days: 180, label: '180d' },
  { days: 365, label: '1y' },
  { days: 1095, label: '3y' },
  { days: 1825, label: '5y' },
  { days: 3650, label: '10y' },
  { days: 0, label: 'All' },
];

function periodLabel(days: number): string {
  return TIME_RANGES.find((r) => r.days === days)?.label ?? `${days}d`;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function findDefinition(name: string, defs: MetricDefinition[]): MetricDefinition | undefined {
  return (
    defs.find((d) => d.name === name) ||
    defs.find((d) => d.name.toLowerCase() === name.toLowerCase()) ||
    defs.find((d) => d.aliases?.some((a) => a.toLowerCase() === name.toLowerCase()))
  );
}

function findSummary(name: string, summaries: MetricSummary[]): MetricSummary | undefined {
  return (
    summaries.find((s) => s.metric_type === name) ||
    summaries.find((s) => s.metric_type.toLowerCase() === name.toLowerCase())
  );
}

function findTimeSeries(name: string, ts: Record<string, TimeSeriesPoint[]>): TimeSeriesPoint[] {
  return ts[name] || ts[name.toLowerCase()] || [];
}

function getDefaultRange(def: MetricDefinition): { low: number | null; high: number | null } {
  if (!def.reference_ranges || def.reference_ranges.length === 0) return { low: null, high: null };
  const general = def.reference_ranges.find((r) => !r.sex && !r.age_min && !r.age_max);
  const range = general || def.reference_ranges[0];
  return { low: range.low ?? null, high: range.high ?? null };
}

type RangeStatus = 'normal' | 'borderline' | 'out-of-range' | 'unknown';

function getRangeStatus(value: number | null, low: number | null, high: number | null): RangeStatus {
  if (value == null || (low == null && high == null)) return 'unknown';
  const margin = ((high ?? 0) - (low ?? 0)) * 0.1 || 1;
  if ((low != null && value < low - margin) || (high != null && value > high + margin)) return 'out-of-range';
  if ((low != null && value < low) || (high != null && value > high)) return 'borderline';
  return 'normal';
}

function formatValue(value: number | null): string {
  if (value == null) return '—';
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(1);
}

// ── Expanded Detail Chart ────────────────────────────────────────────────────

function DetailChart({
  name,
  definition,
  timeSeries,
  summary,
  convert,
  formatMetricDisplay,
}: {
  name: string;
  definition?: MetricDefinition;
  timeSeries: TimeSeriesPoint[];
  summary?: MetricSummary;
  convert: (metricName: string, value: number, unit: string) => { value: number; unit: string };
  formatMetricDisplay: (value: number | null, unit: string) => string;
}) {
  if (timeSeries.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400">
        No trend data available for {name}
      </div>
    );
  }

  // Convert time series values to preferred unit
  const convertedSeries = timeSeries.map((p) => {
    const c = convert(name, p.value, p.unit);
    return { ...p, value: Math.round(c.value * 100) / 100, unit: c.unit };
  });
  const displayUnit = convertedSeries[0]?.unit || summary?.unit || '';

  const { low, high } = definition ? getDefaultRange(definition) : { low: null, high: null };
  const values = convertedSeries.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = (max - min) * 0.2 || 5;

  // Convert summary latest value
  const convertedDisplay = summary
    ? convert(name, summary.latest_value, summary.unit)
    : null;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          {definition?.description && <p className="text-sm text-gray-500">{definition.description}</p>}
        </div>
        {summary && (
          <div className="text-right">
            <span className="text-2xl font-bold text-gray-900">{convertedDisplay ? formatMetricDisplay(convertedDisplay.value, convertedDisplay.unit) : '—'}</span>
            <span className="text-sm text-gray-500 ml-1">{displayUnit}</span>
          </div>
        )}
      </div>

      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={convertedSeries} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            tickFormatter={(v) => {
              const parts = v.split('-');
              return `${parseInt(parts[1])}/${parseInt(parts[2])}`;
            }}
          />
          <YAxis domain={[min - padding, max + padding]} tick={{ fontSize: 11, fill: '#9ca3af' }} width={50} />
          <Tooltip
            content={({ active, payload }: any) => {
              if (!active || !payload?.length) return null;
              const point = payload[0].payload as TimeSeriesPoint;
              const parts = point.date.split('-');
              const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
              const dateLabel = d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
              return (
                <div style={{ backgroundColor: 'white', border: '1px solid #e5e7eb', borderRadius: '8px', fontSize: '13px', padding: '8px 12px' }}>
                  <div style={{ color: '#374151', fontWeight: 600, marginBottom: '2px' }}>{dateLabel}</div>
                  <div style={{ color: '#111827' }}>{point.value} {displayUnit}</div>
                  {point.source && (
                    <div style={{ color: '#9ca3af', fontSize: '11px', marginTop: '2px' }}>
                      {point.source === 'google_health_connect' ? 'Google Health' :
                       point.source === 'document_analysis' ? 'Document Import' :
                       point.source === 'manual' ? 'Manual Entry' :
                       point.source}
                    </div>
                  )}
                </div>
              );
            }}
          />
          {low != null && (
            <ReferenceLine y={low} stroke="#ef4444" strokeDasharray="6 4" strokeWidth={1}
              label={{ value: `Low: ${low}`, position: 'left', fontSize: 10, fill: '#ef4444' }} />
          )}
          {high != null && (
            <ReferenceLine y={high} stroke="#ef4444" strokeDasharray="6 4" strokeWidth={1}
              label={{ value: `High: ${high}`, position: 'left', fontSize: 10, fill: '#ef4444' }} />
          )}
          {low != null && high != null && (
            <ReferenceArea y1={low} y2={high} fill="#10b981" fillOpacity={0.06} />
          )}
          <Line type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2}
            dot={{ fill: '#3b82f6', r: 3 }} activeDot={{ r: 5, fill: '#2563eb' }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── List Row Component ───────────────────────────────────────────────────────

function ListRow({
  name, description, value, unit, formattedValue, trend, trendPct, trendData,
  refLow, refHigh, selected, onClick,
}: {
  name: string; description?: string; value: number | null; unit: string;
  formattedValue?: string;
  trend: 'up' | 'down' | 'flat' | null; trendPct: number | null;
  trendData: number[]; refLow: number | null; refHigh: number | null;
  selected: boolean; onClick: () => void;
}) {
  const status = getRangeStatus(value, refLow, refHigh);
  const sc = {
    normal:      { bg: 'bg-emerald-50',  border: 'border-emerald-200',  text: 'text-emerald-600',  bar: 'bg-emerald-400',  spark: '#10b981' },
    borderline:  { bg: 'bg-amber-50',     border: 'border-amber-200',     text: 'text-amber-600',     bar: 'bg-amber-400',     spark: '#f59e0b' },
    'out-of-range': { bg: 'bg-red-50',    border: 'border-red-200',       text: 'text-red-600',       bar: 'bg-red-400',       spark: '#ef4444' },
    unknown:     { bg: 'bg-gray-50',      border: 'border-gray-200',      text: 'text-gray-400',      bar: 'bg-gray-300',      spark: '#9ca3af' },
  }[status];

  return (
    <div
      onClick={onClick}
      className={`relative flex items-center gap-4 p-3 rounded-lg border transition-all cursor-pointer hover:shadow-sm ${selected ? 'ring-2 ring-blue-500 shadow-md' : ''} ${sc.bg} ${sc.border}`}
    >
      <div className={`w-1 h-10 rounded-full ${sc.bar} flex-shrink-0`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-800">{name}</span>
          {status === 'normal' && <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />}
          {status === 'borderline' && <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />}
          {status === 'out-of-range' && <AlertTriangle className="h-3.5 w-3.5 text-red-500" />}
        </div>
        {description && <p className="text-xs text-gray-500 truncate">{description}</p>}
      </div>
      <div className="text-right flex-shrink-0 w-24">
        <span className={`text-lg font-bold tabular-nums ${sc.text}`}>{formattedValue ?? formatValue(value)}</span>
        <span className="text-xs text-gray-500 ml-1">{unit}</span>
        {refLow != null && refHigh != null && <p className="text-xs text-gray-400">{refLow}–{refHigh}</p>}
      </div>
      <div className="flex-shrink-0 w-24">
        <Sparkline data={trendData} height={28} color={sc.spark} referenceLow={refLow} referenceHigh={refHigh} />
      </div>
      <div className="flex-shrink-0 w-16 text-right">
        {trend && trend !== 'flat' ? (
          <div className={`flex items-center gap-1 justify-end ${trend === 'up' ? 'text-emerald-500' : 'text-red-500'}`}>
            {trend === 'up' ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
            <span className="text-xs font-medium">{trendPct != null ? `${trendPct > 0 ? '+' : ''}${trendPct}%` : ''}</span>
          </div>
        ) : (
          <div className="flex items-center gap-1 justify-end text-gray-400">
            <Minus className="h-3 w-3" /><span className="text-xs">Stable</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Metric Toggle List (for customize modal) ────────────────────────────────

function MetricToggleList({
  definitions,
  selected,
  onToggle,
}: {
  definitions: MetricDefinition[];
  selected: string[];
  onToggle: (name: string) => void;
}) {
  // Build a name→definition lookup (also match by alias for CORE_METRICS names)
  const defByName = useMemo(() => {
    const map = new Map<string, MetricDefinition>();
    for (const d of definitions) {
      map.set(d.name, d);
      map.set(d.name.toLowerCase(), d);
      for (const a of d.aliases || []) map.set(a.toLowerCase(), d);
    }
    return map;
  }, [definitions]);

  // Collect every unique metric: all definitions + any selected names not in definitions
  const allMetrics = useMemo(() => {
    const isDefSelected = (d: MetricDefinition): boolean => {
      for (const sel of selected) {
        if (sel === d.name || sel.toLowerCase() === d.name.toLowerCase()) return true;
        if (d.aliases?.some((a) => a.toLowerCase() === sel.toLowerCase())) return true;
      }
      return false;
    };

    const seen = new Set<string>();
    const items: Array<{ name: string; category: string; isSelected: boolean }> = [];
    for (const d of definitions) {
      if (seen.has(d.name)) continue;
      seen.add(d.name);
      items.push({ name: d.name, category: d.category || 'Other', isSelected: isDefSelected(d) });
    }
    // Selected names that don't match any definition (e.g. CORE_METRICS names like "Hemoglobin A1c")
    for (const name of selected) {
      if (seen.has(name)) continue;
      const def = defByName.get(name) || defByName.get(name.toLowerCase());
      if (def && seen.has(def.name)) {
        // The definition already covers this selected name; mark it selected in-place
        const existing = items.find((i) => i.name === def.name);
        if (existing) existing.isSelected = true;
        continue;
      }
      seen.add(name);
      items.push({ name, category: def?.category || 'Other', isSelected: true });
    }
    return items;
  }, [definitions, selected, defByName]);

  // Group by category, selected first
  const grouped = useMemo(() => {
    const selectedItems = allMetrics.filter((m) => m.isSelected);
    const unselectedItems = allMetrics.filter((m) => !m.isSelected);

    const buildGroups = (items: typeof allMetrics) => {
      const map = new Map<string, typeof allMetrics>();
      for (const item of items) {
        const list = map.get(item.category) || [];
        list.push(item);
        map.set(item.category, list);
      }
      // Sort categories: known order first, then alphabetical
      const knownOrder = CATEGORY_ORDER.filter((c) => map.has(c));
      const extra = Array.from(map.keys()).filter((c) => !CATEGORY_ORDER.includes(c)).sort();
      return [...knownOrder, ...extra].map((cat) => ({ category: cat, items: map.get(cat)! }));
    };

    return { selectedGroups: buildGroups(selectedItems), unselectedGroups: buildGroups(unselectedItems) };
  }, [allMetrics]);

  const renderGroup = (group: { category: string; items: typeof allMetrics }) => (
    <div key={group.category}>
      <div className="text-xs font-medium text-gray-400 uppercase tracking-wider px-1 pt-3 pb-1">
        {CATEGORY_ICONS[group.category] || '📊'} {group.category}
      </div>
      {group.items.map((m) => {
        const isOn = m.isSelected;
        return (
          <button
            key={m.name}
            onClick={() => onToggle(m.name)}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left text-sm transition-colors cursor-pointer ${
              isOn
                ? 'bg-blue-50 text-blue-800 hover:bg-blue-100'
                : 'text-gray-700 hover:bg-gray-50'
            }`}
          >
            <div className={`w-[18px] h-[18px] rounded border-2 flex items-center justify-center flex-shrink-0 transition-colors ${
              isOn ? 'bg-blue-600 border-blue-600' : 'border-gray-300'
            }`}>
              {isOn && <CheckCircle className="h-3 w-3 text-white" />}
            </div>
            <span className="flex-1">{m.name}</span>
          </button>
        );
      })}
    </div>
  );

  return (
    <div>
      {grouped.selectedGroups.length > 0 && (
        <div className="mb-2">
          <div className="text-xs font-semibold text-blue-600 uppercase tracking-wider px-1 pb-1">
            Selected ({grouped.selectedGroups.reduce((n, g) => n + g.items.length, 0)})
          </div>
          {grouped.selectedGroups.map(renderGroup)}
        </div>
      )}
      {grouped.unselectedGroups.length > 0 && (
        <div>
          {grouped.selectedGroups.length > 0 && (
            <div className="border-t border-gray-100 my-2" />
          )}
          {grouped.unselectedGroups.map(renderGroup)}
        </div>
      )}
    </div>
  );
}

// ── Main Dashboard ───────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [reportData, setReportData] = useState<ReportData | null>(null);
  const [definitions, setDefinitions] = useState<MetricDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(new Set());
  const [period, setPeriod] = useState<number>(0);
  const { convert, formatValue: formatMetricDisplay, getDisplayUnit, loaded: unitsLoaded } = useUnitConversion();

  // Dashboard metric selection
  const [customMetrics, setCustomMetrics] = useState<string[] | null>(null); // null = default
  const [showCustomize, setShowCustomize] = useState(false);
  const [editMetrics, setEditMetrics] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => { loadData(); }, [period]);

  useEffect(() => { loadDashboardMetrics(); }, []);

  // Close modal on Escape key
  useEffect(() => {
    if (!selectedMetric) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') setSelectedMetric(null); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectedMetric]);

  const loadData = async () => {
    setLoading(true);
    try {
      const [reportRes, defsRes] = await Promise.allSettled([
        apiClient.get('/health/reports/overview', { params: { days: period } }),
        apiClient.get('/health/metrics/definitions'),
      ]);
      if (reportRes.status === 'fulfilled') setReportData(reportRes.value.data);
      if (defsRes.status === 'fulfilled') setDefinitions(defsRes.value.data);
    } catch (error) {
      console.error('Failed to load dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadDashboardMetrics = async () => {
    try {
      const res = await apiClient.get('/users/me/dashboard-metrics');
      if (res.data.is_default) {
        setCustomMetrics(null);
      } else {
        setCustomMetrics(res.data.metric_names);
      }
    } catch {
      setCustomMetrics(null);
    }
  };

  /** The effective list of metric names to display, in order. */
  const activeMetricNames: string[] = customMetrics ?? DEFAULT_METRIC_NAMES;

  /** Build MetricConfig list from active metric names, preserving category/priority for known metrics. */
  const activeMetrics: MetricConfig[] = useMemo(() => {
    return activeMetricNames.map((name, i) => {
      const core = CORE_METRICS.find((c) => c.name === name);
      if (core) return core;
      // Custom metric not in CORE list — find its definition for category
      const def = findDefinition(name, definitions);
      return { name, category: def?.category || 'Other', priority: i + 100 };
    });
  }, [activeMetricNames, definitions]);

  // Collect dynamic categories that might not be in CATEGORY_ORDER
  const activeCategories: string[] = useMemo(() => {
    const cats = new Set(CATEGORY_ORDER);
    for (const m of activeMetrics) {
      if (!cats.has(m.category)) cats.add(m.category);
    }
    // Preserve CATEGORY_ORDER first, then extras alphabetically
    const ordered = CATEGORY_ORDER.filter((c) => cats.has(c));
    const extras = Array.from(cats).filter((c) => !CATEGORY_ORDER.includes(c)).sort();
    return [...ordered, ...extras];
  }, [activeMetrics]);

  const openCustomize = () => {
    setEditMetrics([...activeMetricNames]);
    setShowCustomize(true);
  };

  const toggleMetric = (name: string) => {
    // Resolve name: if it's a definition name, check if any selected entry maps to that same definition
    const def = definitions.find((d) => d.name === name);
    if (!def) {
      // Not a definition — plain toggle
      setEditMetrics((prev) =>
        prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]
      );
      return;
    }
    // Check if any currently-selected name resolves to this definition
    const matchIdx = editMetrics.findIndex((sel) => {
      if (sel === def.name || sel.toLowerCase() === def.name.toLowerCase()) return true;
      return def.aliases?.some((a) => a.toLowerCase() === sel.toLowerCase()) ?? false;
    });
    if (matchIdx >= 0) {
      // Remove the existing entry (whatever form it's in)
      setEditMetrics((prev) => prev.filter((_, i) => i !== matchIdx));
    } else {
      setEditMetrics((prev) => [...prev, def.name]);
    }
  };

  const saveCustomize = async () => {
    setSaving(true);
    try {
      await apiClient.put('/users/me/dashboard-metrics', { metric_names: editMetrics });
      setCustomMetrics(editMetrics.length > 0 ? editMetrics : null);
      setShowCustomize(false);
    } catch (error) {
      console.error('Failed to save dashboard metrics:', error);
    } finally {
      setSaving(false);
    }
  };

  const revertToDefault = async () => {
    setSaving(true);
    try {
      await apiClient.put('/users/me/dashboard-metrics', { metric_names: [] });
      setCustomMetrics(null);
      setEditMetrics([...DEFAULT_METRIC_NAMES]);
      setShowCustomize(false);
    } catch (error) {
      console.error('Failed to revert dashboard metrics:', error);
    } finally {
      setSaving(false);
    }
  };

  const groupedMetrics = useMemo(() => {
    const groups: Record<string, Array<{
      config: MetricConfig; definition?: MetricDefinition; summary?: MetricSummary;
      trendData: number[]; refLow: number | null; refHigh: number | null;
      displayValue: number | null; displayUnit: string; formattedDisplay: string;
    }>> = {};
    for (const cat of activeCategories) groups[cat] = [];
    for (const config of activeMetrics) {
      const def = findDefinition(config.name, definitions);
      const summary = findSummary(config.name, reportData?.summary || []);
      // Skip metrics with no data
      if (!summary) continue;
      const ts = findTimeSeries(config.name, reportData?.time_series || {});
      const { low, high } = def ? getDefaultRange(def) : { low: null, high: null };

      // Convert values to preferred unit
      const converted = convert(config.name, summary.latest_value, summary.unit);
      const convertedTrendData = ts.map((p) => Math.round(convert(config.name, p.value, p.unit).value * 100) / 100);

      groups[config.category].push({
        config, definition: def, summary,
        trendData: convertedTrendData, refLow: low, refHigh: high,
        displayValue: converted.value, displayUnit: converted.unit,
        formattedDisplay: formatMetricDisplay(converted.value, converted.unit),
      });
    }
    for (const cat of activeCategories) groups[cat].sort((a, b) => a.config.priority - b.config.priority);
    return groups;
  }, [definitions, reportData, convert, formatMetricDisplay, activeMetrics, activeCategories]);

  const healthSummary = useMemo(() => {
    let total = 0, normal = 0, borderline = 0, outOfRange = 0;
    for (const config of activeMetrics) {
      const def = findDefinition(config.name, definitions);
      const summary = findSummary(config.name, reportData?.summary || []);
      if (!summary) continue;
      total++;
      const { low, high } = def ? getDefaultRange(def) : { low: null, high: null };
      const status = getRangeStatus(summary.latest_value, low, high);
      if (status === 'normal') normal++;
      else if (status === 'borderline') borderline++;
      else if (status === 'out-of-range') outOfRange++;
      else normal++;
    }
    return { total, normal, borderline, outOfRange };
  }, [definitions, reportData, activeMetrics]);

  const toggleCategory = (cat: string) => {
    setCollapsedCategories((prev) => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
  };

  if (loading) {
    return (
      <AuthLayout>
        <div className="flex items-center justify-center h-full">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-6 gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Health Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">Your key health metrics at a glance</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Time range: buttons on sm+, dropdown on small screens */}
          <div className="hidden sm:flex items-center gap-1 bg-gray-100 rounded-lg p-1">
            {TIME_RANGES.map((r) => (
              <button key={r.days} onClick={() => setPeriod(r.days)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer ${period === r.days ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>
                {r.label}
              </button>
            ))}
          </div>
          <select
            value={period}
            onChange={(e) => setPeriod(Number(e.target.value))}
            className="sm:hidden px-3 py-1.5 rounded-md text-xs font-medium bg-gray-100 text-gray-700 border-none outline-none cursor-pointer"
          >
            {TIME_RANGES.map((r) => (
              <option key={r.days} value={r.days}>{r.label}</option>
            ))}
          </select>
          <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1">
            <button onClick={() => setViewMode('grid')}
              className={`p-1.5 rounded-md transition-colors cursor-pointer ${viewMode === 'grid' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-400'}`}>
              <LayoutGrid className="h-4 w-4" />
            </button>
            <button onClick={() => setViewMode('list')}
              className={`p-1.5 rounded-md transition-colors cursor-pointer ${viewMode === 'list' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-400'}`}>
              <List className="h-4 w-4" />
            </button>
          </div>
          <button
            onClick={openCustomize}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors cursor-pointer"
            title="Customize dashboard metrics"
          >
            <Settings2 className="h-3.5 w-3.5" />
            Customize
          </button>
        </div>
      </div>

      {/* Health Summary Bar */}
      <div className="card mb-6">
        <div className="flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-blue-600" />
            <span className="text-sm font-medium text-gray-700">Health Overview</span>
          </div>
          <div className="flex items-center gap-4 flex-1">
            {healthSummary.normal > 0 && (
              <div className="flex items-center gap-1.5">
                <div className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
                <span className="text-sm text-gray-600"><span className="font-semibold">{healthSummary.normal}</span> Normal</span>
              </div>
            )}
            {healthSummary.borderline > 0 && (
              <div className="flex items-center gap-1.5">
                <div className="w-2.5 h-2.5 rounded-full bg-amber-500" />
                <span className="text-sm text-gray-600"><span className="font-semibold">{healthSummary.borderline}</span> Borderline</span>
              </div>
            )}
            {healthSummary.outOfRange > 0 && (
              <div className="flex items-center gap-1.5">
                <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
                <span className="text-sm text-gray-600"><span className="font-semibold">{healthSummary.outOfRange}</span> Out of Range</span>
              </div>
            )}
          </div>
          <div className="text-xs text-gray-400">{periodLabel(period)} trend · {healthSummary.total} metrics</div>
        </div>
      </div>

      {/* Metric Categories */}
      {activeCategories.map((category) => {
        const metrics = groupedMetrics[category] || [];
        // Hide categories with no data
        if (metrics.length === 0) return null;
        const isCollapsed = collapsedCategories.has(category);

        return (
          <div key={category} className="mb-6">
            <button onClick={() => toggleCategory(category)}
              className="flex items-center gap-2 mb-3 group w-full text-left cursor-pointer">
              <span className="text-lg">{CATEGORY_ICONS[category] || '📊'}</span>
              <h2 className="text-base font-semibold text-gray-800">{category}</h2>
              <span className="text-xs text-gray-400 ml-1">({metrics.length})</span>
              <div className="flex-1" />
              {isCollapsed
                ? <ChevronRight className="h-4 w-4 text-gray-400 group-hover:text-gray-600" />
                : <ChevronDown className="h-4 w-4 text-gray-400 group-hover:text-gray-600" />}
            </button>

            {!isCollapsed && (
              viewMode === 'grid' ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                  {metrics.map((m) => (
                    <MetricCard key={m.config.name} name={m.config.name}
                      description={m.definition?.description || undefined}
                      value={m.displayValue}
                      unit={m.displayUnit}
                      formattedValue={m.formattedDisplay}
                      recordedAt={m.summary?.recorded_at}
                      date={m.summary?.date}
                      trend={m.summary?.trend ?? null} trendPct={m.summary?.trend_pct ?? null}
                      trendData={m.trendData} referenceLow={m.refLow} referenceHigh={m.refHigh}
                      selected={selectedMetric === m.config.name}
                      onClick={() => setSelectedMetric(selectedMetric === m.config.name ? null : m.config.name)} />
                  ))}
                </div>
              ) : (
                <div className="space-y-2">
                  {metrics.map((m) => (
                    <ListRow key={m.config.name} name={m.config.name}
                      description={m.definition?.description || undefined}
                      value={m.displayValue}
                      unit={m.displayUnit}
                      formattedValue={m.formattedDisplay}
                      trend={m.summary?.trend ?? null} trendPct={m.summary?.trend_pct ?? null}
                      trendData={m.trendData} refLow={m.refLow} refHigh={m.refHigh}
                      selected={selectedMetric === m.config.name}
                      onClick={() => setSelectedMetric(selectedMetric === m.config.name ? null : m.config.name)} />
                  ))}
                </div>
              )
            )}
          </div>
        );
      })}

      {/* Customize Metrics Modal */}
      {showCustomize && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          onClick={(e) => { if (e.target === e.currentTarget) setShowCustomize(false); }}
          role="dialog"
          aria-modal="true"
          aria-label="Customize dashboard metrics"
        >
          <div className="absolute inset-0 bg-black/40" />
          <div className="relative w-full sm:w-[32rem] max-h-[80vh] mx-4 bg-white rounded-2xl shadow-2xl flex flex-col">
            <div className="flex items-center justify-between px-6 pt-5 pb-3 border-b border-gray-100">
              <h2 className="text-lg font-semibold text-gray-900">Customize Dashboard</h2>
              <button
                onClick={() => setShowCustomize(false)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors cursor-pointer"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-4">
              <p className="text-xs text-gray-400 mb-3">
                Tap a metric to add or remove it from your dashboard.
                {customMetrics === null && editMetrics.length === DEFAULT_METRIC_NAMES.length && (
                  <span className="ml-1">(showing defaults)</span>
                )}
              </p>
              <MetricToggleList definitions={definitions} selected={editMetrics} onToggle={toggleMetric} />
            </div>

            <div className="flex items-center justify-between px-6 py-4 border-t border-gray-100">
              <button
                onClick={revertToDefault}
                disabled={saving}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors cursor-pointer disabled:opacity-50"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Revert to Default
              </button>
              <button
                onClick={saveCustomize}
                disabled={saving}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors cursor-pointer disabled:opacity-50"
              >
                {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Detail Chart Modal */}
      {selectedMetric && (
        <div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center"
          onClick={(e) => { if (e.target === e.currentTarget) setSelectedMetric(null); }}
          role="dialog"
          aria-modal="true"
          aria-label={`${selectedMetric} detail`}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/40" />

          {/* Panel: bottom sheet on mobile, centered card on desktop */}
          <div className="relative w-full sm:w-[85vw] lg:w-[75vw] xl:w-[65vw] sm:max-w-5xl mx-0 sm:mx-6
                          bg-white rounded-t-2xl sm:rounded-2xl shadow-2xl
                          max-h-[85vh] overflow-y-auto">
            {/* Mobile drag handle */}
            <div className="flex justify-center pt-3 sm:hidden">
              <div className="w-10 h-1 rounded-full bg-gray-300" />
            </div>
            <div className="sticky top-0 z-10 flex items-center justify-between px-6 pt-4 pb-3 bg-white border-b border-gray-100">
              <h2 className="text-lg font-semibold text-gray-900">{selectedMetric}</h2>
              <button
                onClick={() => setSelectedMetric(null)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors cursor-pointer"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="px-6 sm:px-8 pb-6 sm:pb-8">
              <DetailChart name={selectedMetric}
                definition={findDefinition(selectedMetric, definitions)}
                timeSeries={findTimeSeries(selectedMetric, reportData?.time_series || {})}
                summary={findSummary(selectedMetric, reportData?.summary || [])}
                convert={convert}
                formatMetricDisplay={formatMetricDisplay} />
            </div>
          </div>
        </div>
      )}

      {/* Empty State */}
      {healthSummary.total === 0 && (
        <div className="card text-center py-16">
          <Activity className="h-16 w-16 text-gray-200 mx-auto mb-4" />
          <h2 className="text-lg font-semibold text-gray-700 mb-2">No Health Data Yet</h2>
          <p className="text-gray-500 max-w-md mx-auto">
            Upload medical documents or sync from Google Health Connect to start tracking your health metrics.
          </p>
        </div>
      )}
    </AuthLayout>
  );
}
