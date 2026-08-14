'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Activity, TrendingUp, TrendingDown, Minus, BarChart3, Loader2 } from 'lucide-react';
import apiClient from '@/lib/api-client';
import { useUnitConversion } from '@/hooks/useUnitConversion';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area,
} from 'recharts';

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

const METRIC_LABELS: Record<string, string> = {
  steps: 'Steps',
  heart_rate: 'Heart Rate',
  'Heart Rate (Avg)': 'Heart Rate (Avg)',
  'Heart Rate (Min)': 'Heart Rate (Min)',
  'Heart Rate (Max)': 'Heart Rate (Max)',
  sleep: 'Sleep',
  weight: 'Weight',
  distance: 'Distance',
  calories: 'Calories',
  blood_pressure: 'Blood Pressure',
  blood_glucose: 'Blood Glucose',
  body_temperature: 'Body Temp',
  oxygen_saturation: 'SpO2',
  body_fat_percentage: 'Body Fat %',
  height: 'Height',
  heart_minutes: 'Heart Points',
  move_minutes: 'Move Minutes',
  bmr: 'BMR',
  speed: 'Speed',
};

const METRIC_COLORS: Record<string, string> = {
  steps: '#3b82f6',
  heart_rate: '#ef4444',
  'Heart Rate (Avg)': '#ef4444',
  'Heart Rate (Min)': '#f97316',
  'Heart Rate (Max)': '#b91c1c',
  sleep: '#8b5cf6',
  weight: '#f59e0b',
  distance: '#10b981',
  calories: '#f97316',
  blood_pressure: '#dc2626',
  blood_glucose: '#06b6d4',
  body_temperature: '#ec4899',
  oxygen_saturation: '#14b8a6',
  body_fat_percentage: '#a855f7',
  height: '#6366f1',
  heart_minutes: '#e11d48',
  move_minutes: '#059669',
  bmr: '#d97706',
  speed: '#0ea5e9',
};

function TrendIcon({ trend }: { trend: 'up' | 'down' | 'flat' | null }) {
  if (!trend || trend === 'flat') return <Minus className="h-4 w-4 text-gray-400" />;
  if (trend === 'up') return <TrendingUp className="h-4 w-4 text-green-500" />;
  return <TrendingDown className="h-4 w-4 text-red-500" />;
}

function formatValue(value: number, unit: string): string {
  if (!isFinite(value)) return '—';

  // Whole-number units
  if (unit.toLowerCase() === 'steps' || unit.toLowerCase() === 'heart_points' || unit.toLowerCase() === 'move_minutes' || unit.toLowerCase() === 'kcal' || unit.toLowerCase() === 'cal') {
    return Math.round(value).toLocaleString();
  }

  // Time units
  if (unit.toLowerCase() === 'ms') {
    return (value / 3600000).toFixed(1);
  }

  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(1);
}

function formatUnit(unit: string): string {
  if (unit.toLowerCase() === 'ms') return 'hrs';
  return unit;
}

// ── Time Range Filters ───────────────────────────────────────────────────────
// `days` is sent to /health/reports/overview. A value of 0 means "all time"
// (the backend omits the date filter).
const TIME_RANGES: Array<{ days: number; label: string }> = [
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
  { days: 180, label: '180d' },
  { days: 365, label: '1y' },
  { days: 1095, label: '3y' },
  { days: 1825, label: '5y' },
  { days: 3650, label: '10y' },
  { days: 0, label: 'All' },
];

function getRangeLabel(days: number): string {
  return days === 0 ? 'All' : days < 365 ? `${days}d` : `${days / 365}y`;
}

export default function ReportsPage() {
  const [activeTab, setActiveTab] = useState<'overview'>('overview');
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState<number>(30);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const { convert } = useUnitConversion();

  useEffect(() => {
    loadReport();
  }, [period]);

  const loadReport = async () => {
    setLoading(true);
    try {
      const res = await apiClient.get('/health/reports/overview', { params: { days: period } });
      setData(res.data);
      // Auto-select first metric with time series data
      if (!selectedMetric && res.data?.summary?.length > 0) {
        const withData = res.data.summary.find((s: MetricSummary) =>
          res.data.time_series[s.metric_type]?.length > 0
        );
        if (withData) setSelectedMetric(withData.metric_type);
      }
    } catch (error) {
      console.error('Failed to load report:', error);
    } finally {
      setLoading(false);
    }
  };

  const tabs = [{ key: 'overview' as const, label: 'Overview', icon: BarChart3 }];

  if (loading) {
    return (
      <AuthLayout>
        <div className="flex items-center justify-center h-full">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      </AuthLayout>
    );
  }

  const summary = data?.summary || [];
  const timeSeries = data?.time_series || {};
  const chartData = selectedMetric ? timeSeries[selectedMetric] || [] : [];

  return (
    <AuthLayout>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Reports</h1>
        <div className="flex flex-wrap items-center gap-2">
          {TIME_RANGES.map((r) => (
            <button
              key={r.days}
              onClick={() => setPeriod(r.days)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                period === r.days
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-lg p-1 mb-6 w-fit">
        {tabs.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            <tab.icon className="h-4 w-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <>
          {/* Summary Cards */}
          {summary.length === 0 ? (
            <div className="card text-center py-12">
              <Activity className="h-12 w-12 text-gray-300 mx-auto mb-4" />
              <p className="text-gray-500">No health data yet. Sync from Google Fit or add metrics manually.</p>
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 mb-8">
              {summary.map(item => {
                const color = METRIC_COLORS[item.metric_type] || '#6b7280';
                const converted = convert(item.metric_type, item.latest_value, item.unit);
                const displayVal = formatValue(converted.value, converted.unit);
                const displayUnit = formatUnit(converted.unit);
                const convertedAvg = item.recent_avg !== null
                  ? convert(item.metric_type, item.recent_avg, item.unit)
                  : null;
                return (
                  <div
                    key={item.metric_type}
                    onClick={() => setSelectedMetric(item.metric_type)}
                    className={`card cursor-pointer transition-all hover:shadow-md ${
                      selectedMetric === item.metric_type ? 'ring-2 ring-blue-500 shadow-md' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
                        {METRIC_LABELS[item.metric_type] || item.metric_type.replace(/_/g, ' ')}
                      </span>
                      <div className="flex items-center gap-1">
                        <TrendIcon trend={item.trend} />
                        {item.trend_pct !== null && (
                          <span className={`text-xs font-medium ${
                            item.trend === 'up' ? 'text-green-600' :
                            item.trend === 'down' ? 'text-red-600' :
                            'text-gray-500'
                          }`}>
                            {item.trend_pct > 0 ? '+' : ''}{item.trend_pct}%
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-baseline gap-1">
                      <span className="text-2xl font-bold" style={{ color }}>
                        {displayVal}
                      </span>
                      <span className="text-sm text-gray-500">
                        {displayUnit}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 mt-1">
                      {new Date(item.recorded_at).toLocaleDateString()}
                    </p>
                    {convertedAvg !== null && (
                      <p className="text-xs text-gray-400">
                        7d avg: {formatValue(convertedAvg.value, convertedAvg.unit)}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Chart */}
          {selectedMetric && chartData.length > 0 && (() => {
            // Convert chart data to preferred unit
            const convertedChartData = chartData.map((p) => {
              const c = convert(selectedMetric, p.value, p.unit);
              return { ...p, value: Math.round(c.value * 100) / 100, unit: c.unit };
            });
            const chartUnit = convertedChartData[0]?.unit || '';

            return (
            <div className="card">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-gray-900">
                  {METRIC_LABELS[selectedMetric] || selectedMetric.replace(/_/g, ' ')} Trend
                </h2>
                <span className="text-sm text-gray-500">
                  {convertedChartData.length} data point{convertedChartData.length !== 1 ? 's' : ''} over {getRangeLabel(period)}
                </span>
              </div>
              <div className="h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={convertedChartData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                    <defs>
                      <linearGradient id={`gradient-${selectedMetric}`} x1="0" y1="0" x2="0" y2="1">
                        <stop
                          offset="5%"
                          stopColor={METRIC_COLORS[selectedMetric] || '#3b82f6'}
                          stopOpacity={0.3}
                        />
                        <stop
                          offset="95%"
                          stopColor={METRIC_COLORS[selectedMetric] || '#3b82f6'}
                          stopOpacity={0}
                        />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 12 }}
                      tickFormatter={(v) => {
                        const d = new Date(v + 'T00:00:00');
                        return `${d.getMonth() + 1}/${d.getDate()}`;
                      }}
                    />
                    <YAxis tick={{ fontSize: 12 }} width={60} />
                    <Tooltip
                      labelFormatter={(v) => new Date(v + 'T00:00:00').toLocaleDateString()}
                      formatter={(value) => [
                        `${formatValue(Number(value), chartUnit)} ${formatUnit(chartUnit)}`,
                        METRIC_LABELS[selectedMetric] || selectedMetric,
                      ]}
                    />
                    <Area
                      type="monotone"
                      dataKey="value"
                      stroke={METRIC_COLORS[selectedMetric] || '#3b82f6'}
                      strokeWidth={2}
                      fill={`url(#gradient-${selectedMetric})`}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
            );
          })()}

          {selectedMetric && chartData.length === 0 && summary.length > 0 && (
            <div className="card text-center py-8">
              <p className="text-gray-500">
                No chart data for {METRIC_LABELS[selectedMetric] || selectedMetric} in the last {getRangeLabel(period)}.
              </p>
            </div>
          )}
        </>
      )}
    </AuthLayout>
  );
}
