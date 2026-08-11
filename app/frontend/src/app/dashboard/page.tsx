'use client';

import { useEffect, useState, useMemo } from 'react';
import AuthLayout from '@/components/AuthLayout';
import MetricCard from '@/components/MetricCard';
import Sparkline from '@/components/Sparkline';
import {
  Activity, Loader2, LayoutGrid, List, ChevronDown, ChevronRight,
  TrendingUp, TrendingDown, Minus, CheckCircle, AlertTriangle,
} from 'lucide-react';
import apiClient from '@/lib/api-client';
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
  trend: 'up' | 'down' | 'flat' | null;
  trend_pct: number | null;
  recent_avg: number | null;
  prior_avg: number | null;
}

interface TimeSeriesPoint {
  date: string;
  value: number;
  unit: string;
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
}: {
  name: string;
  definition?: MetricDefinition;
  timeSeries: TimeSeriesPoint[];
  summary?: MetricSummary;
}) {
  if (timeSeries.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400">
        No trend data available for {name}
      </div>
    );
  }

  const { low, high } = definition ? getDefaultRange(definition) : { low: null, high: null };
  const values = timeSeries.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = (max - min) * 0.2 || 5;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-lg font-semibold text-gray-900">{name}</h3>
          {definition?.description && <p className="text-sm text-gray-500">{definition.description}</p>}
        </div>
        {summary && (
          <div className="text-right">
            <span className="text-2xl font-bold text-gray-900">{summary.latest_value}</span>
            <span className="text-sm text-gray-500 ml-1">{summary.unit}</span>
          </div>
        )}
      </div>

      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={timeSeries} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            tickFormatter={(v) => { const d = new Date(v); return `${d.getMonth() + 1}/${d.getDate()}`; }}
          />
          <YAxis domain={[min - padding, max + padding]} tick={{ fontSize: 11, fill: '#9ca3af' }} width={50} />
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <Tooltip
            contentStyle={{ backgroundColor: 'white', border: '1px solid #e5e7eb', borderRadius: '8px', fontSize: '13px' }}
            labelFormatter={(v: any) => new Date(String(v)).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}
            formatter={(value: any) => [`${value} ${definition?.unit || ''}`, name]}
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
  name, description, value, unit, trend, trendPct, trendData,
  refLow, refHigh, selected, onClick,
}: {
  name: string; description?: string; value: number | null; unit: string;
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
        <span className={`text-lg font-bold tabular-nums ${sc.text}`}>{formatValue(value)}</span>
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

// ── Main Dashboard ───────────────────────────────────────────────────────────

export default function DashboardPage() {
  const [reportData, setReportData] = useState<ReportData | null>(null);
  const [definitions, setDefinitions] = useState<MetricDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(new Set());
  const [period, setPeriod] = useState<number>(90);

  useEffect(() => { loadData(); }, [period]);

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

  const groupedMetrics = useMemo(() => {
    const groups: Record<string, Array<{
      config: MetricConfig; definition?: MetricDefinition; summary?: MetricSummary;
      trendData: number[]; refLow: number | null; refHigh: number | null;
    }>> = {};
    for (const cat of CATEGORY_ORDER) groups[cat] = [];
    for (const config of CORE_METRICS) {
      const def = findDefinition(config.name, definitions);
      const summary = findSummary(config.name, reportData?.summary || []);
      const ts = findTimeSeries(config.name, reportData?.time_series || {});
      const { low, high } = def ? getDefaultRange(def) : { low: null, high: null };
      groups[config.category].push({ config, definition: def, summary, trendData: ts.map((p) => p.value), refLow: low, refHigh: high });
    }
    for (const cat of CATEGORY_ORDER) groups[cat].sort((a, b) => a.config.priority - b.config.priority);
    return groups;
  }, [definitions, reportData]);

  const healthSummary = useMemo(() => {
    let total = 0, normal = 0, borderline = 0, outOfRange = 0, noData = 0;
    for (const config of CORE_METRICS) {
      const def = findDefinition(config.name, definitions);
      const summary = findSummary(config.name, reportData?.summary || []);
      if (!summary) { noData++; continue; }
      total++;
      const { low, high } = def ? getDefaultRange(def) : { low: null, high: null };
      const status = getRangeStatus(summary.latest_value, low, high);
      if (status === 'normal') normal++;
      else if (status === 'borderline') borderline++;
      else if (status === 'out-of-range') outOfRange++;
      else normal++;
    }
    return { total, normal, borderline, outOfRange, noData };
  }, [definitions, reportData]);

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
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Health Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">Your key health metrics at a glance</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex flex-wrap items-center gap-1 bg-gray-100 rounded-lg p-1">
            {TIME_RANGES.map((r) => (
              <button key={r.days} onClick={() => setPeriod(r.days)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${period === r.days ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>
                {r.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1">
            <button onClick={() => setViewMode('grid')}
              className={`p-1.5 rounded-md transition-colors ${viewMode === 'grid' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-400'}`}>
              <LayoutGrid className="h-4 w-4" />
            </button>
            <button onClick={() => setViewMode('list')}
              className={`p-1.5 rounded-md transition-colors ${viewMode === 'list' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-400'}`}>
              <List className="h-4 w-4" />
            </button>
          </div>
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
            {healthSummary.noData > 0 && (
              <div className="flex items-center gap-1.5">
                <div className="w-2.5 h-2.5 rounded-full bg-gray-300" />
                <span className="text-sm text-gray-600"><span className="font-semibold">{healthSummary.noData}</span> No Data</span>
              </div>
            )}
          </div>
          <div className="text-xs text-gray-400">{periodLabel(period)} trend · {healthSummary.total} metrics tracked</div>
        </div>
      </div>

      {/* Metric Categories */}
      {CATEGORY_ORDER.map((category) => {
        const metrics = groupedMetrics[category] || [];
        const isCollapsed = collapsedCategories.has(category);
        const withData = metrics.filter((m) => m.summary).length;

        return (
          <div key={category} className="mb-6">
            <button onClick={() => toggleCategory(category)}
              className="flex items-center gap-2 mb-3 group w-full text-left">
              <span className="text-lg">{CATEGORY_ICONS[category] || '📊'}</span>
              <h2 className="text-base font-semibold text-gray-800">{category}</h2>
              <span className="text-xs text-gray-400 ml-1">({withData}/{metrics.length})</span>
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
                      value={m.summary?.latest_value ?? null}
                      unit={m.summary?.unit || m.definition?.unit || ''}
                      recordedAt={m.summary?.recorded_at}
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
                      value={m.summary?.latest_value ?? null}
                      unit={m.summary?.unit || m.definition?.unit || ''}
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

      {/* Expanded Detail Chart */}
      {selectedMetric && (
        <div className="card mt-6">
          <DetailChart name={selectedMetric}
            definition={findDefinition(selectedMetric, definitions)}
            timeSeries={findTimeSeries(selectedMetric, reportData?.time_series || {})}
            summary={findSummary(selectedMetric, reportData?.summary || [])} />
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
