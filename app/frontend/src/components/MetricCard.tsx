'use client';

import { TrendingUp, TrendingDown, Minus, AlertTriangle, CheckCircle, Info } from 'lucide-react';
import Sparkline from './Sparkline';

export interface MetricCardProps {
  name: string;
  description?: string;
  value: number | null;
  unit: string;
  formattedValue?: string; // Pre-formatted display string (e.g., "5'9\"" for compound units)
  displayUnit?: string; // Override unit display (e.g., "" to hide unit for compound units)
  recordedAt?: string;
  date?: string; // "YYYY-MM-DD" format, displayed as-is without timezone conversion
  trend: 'up' | 'down' | 'flat' | null;
  trendPct: number | null;
  trendData: number[];
  referenceLow: number | null;
  referenceHigh: number | null;
  selected?: boolean;
  onClick?: () => void;
}

type RangeStatus = 'normal' | 'borderline' | 'out-of-range' | 'unknown';

function getRangeStatus(
  value: number | null,
  low: number | null,
  high: number | null
): RangeStatus {
  if (value == null || (low == null && high == null)) return 'unknown';

  const borderlineMargin = 0.1; // 10% margin for borderline

  if (low != null && high != null) {
    const range = high - low;
    const margin = range * borderlineMargin;
    if (value < low - margin || value > high + margin) return 'out-of-range';
    if (value < low || value > high) return 'borderline';
    return 'normal';
  }
  if (low != null) {
    if (value < low * 0.9) return 'out-of-range';
    if (value < low) return 'borderline';
    return 'normal';
  }
  if (high != null) {
    if (value > high * 1.1) return 'out-of-range';
    if (value > high) return 'borderline';
    return 'normal';
  }
  return 'unknown';
}

function getStatusColor(status: RangeStatus): string {
  switch (status) {
    case 'normal':
      return 'text-emerald-600';
    case 'borderline':
      return 'text-amber-500';
    case 'out-of-range':
      return 'text-red-500';
    default:
      return 'text-gray-400';
  }
}

function getStatusBg(status: RangeStatus): string {
  switch (status) {
    case 'normal':
      return 'bg-emerald-50 border-emerald-200';
    case 'borderline':
      return 'bg-amber-50 border-amber-200';
    case 'out-of-range':
      return 'bg-red-50 border-red-200';
    default:
      return 'bg-gray-50 border-gray-200';
  }
}

function getStatusIcon(status: RangeStatus) {
  switch (status) {
    case 'normal':
      return <CheckCircle className="h-4 w-4 text-emerald-500" />;
    case 'borderline':
      return <AlertTriangle className="h-4 w-4 text-amber-500" />;
    case 'out-of-range':
      return <AlertTriangle className="h-4 w-4 text-red-500" />;
    default:
      return <Info className="h-4 w-4 text-gray-400" />;
  }
}

function getSparklineColor(status: RangeStatus): string {
  switch (status) {
    case 'normal':
      return '#10b981';
    case 'borderline':
      return '#f59e0b';
    case 'out-of-range':
      return '#ef4444';
    default:
      return '#9ca3af';
  }
}

function TrendIndicator({ trend, pct }: { trend: 'up' | 'down' | 'flat' | null; pct: number | null }) {
  if (!trend || trend === 'flat') {
    return (
      <div className="flex items-center gap-1 text-gray-400">
        <Minus className="h-3 w-3" />
        <span className="text-xs">Stable</span>
      </div>
    );
  }

  const isUp = trend === 'up';
  return (
    <div className={`flex items-center gap-1 ${isUp ? 'text-emerald-500' : 'text-red-500'}`}>
      {isUp ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
      <span className="text-xs font-medium">
        {pct != null ? `${pct > 0 ? '+' : ''}${pct}%` : trend}
      </span>
    </div>
  );
}

function formatMetricValue(value: number | null): string {
  if (value == null) return '—';
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toFixed(1);
}

/** Format a "YYYY-MM-DD" date string without timezone conversion */
function formatDateStr(dateStr: string): string {
  const [year, month, day] = dateStr.split('-').map(Number);
  const d = new Date(year, month - 1, day);
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function formatRange(low: number | null, high: number | null, unit: string): string {
  if (low != null && high != null) return `${low} – ${high} ${unit}`;
  if (low != null) return `≥ ${low} ${unit}`;
  if (high != null) return `≤ ${high} ${unit}`;
  return '';
}

export default function MetricCard({
  name,
  description,
  value,
  unit,
  formattedValue,
  displayUnit,
  recordedAt,
  date,
  trend,
  trendPct,
  trendData,
  referenceLow,
  referenceHigh,
  selected = false,
  onClick,
}: MetricCardProps) {
  const status = getRangeStatus(value, referenceLow, referenceHigh);
  const statusBg = getStatusBg(status);
  const statusColor = getStatusColor(status);
  const sparkColor = getSparklineColor(status);
  const rangeText = formatRange(referenceLow, referenceHigh, unit);
  const displayValue = formattedValue ?? formatMetricValue(value);
  const showUnit = displayUnit ?? unit;

  return (
    <div
      onClick={onClick}
      className={`
        relative overflow-hidden rounded-xl border p-4 transition-all duration-200
        hover:shadow-md cursor-pointer
        ${selected ? 'ring-2 ring-blue-500 shadow-lg' : ''}
        ${statusBg}
      `}
    >
      {/* Status indicator bar */}
      <div
        className={`absolute top-0 left-0 w-1 h-full rounded-l-xl ${
          status === 'normal'
            ? 'bg-emerald-400'
            : status === 'borderline'
            ? 'bg-amber-400'
            : status === 'out-of-range'
            ? 'bg-red-400'
            : 'bg-gray-300'
        }`}
      />

      {/* Header */}
      <div className="flex items-start justify-between mb-2 pl-2">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-800 truncate">{name}</h3>
          {description && (
            <p className="text-xs text-gray-500 truncate mt-0.5">{description}</p>
          )}
        </div>
        <div className="flex-shrink-0 ml-2">{getStatusIcon(status)}</div>
      </div>

      {/* Value */}
      <div className="flex items-baseline gap-1.5 pl-2 mb-1">
        <span className={`text-2xl font-bold tabular-nums ${statusColor}`}>
          {displayValue}
        </span>
        {showUnit && <span className="text-sm text-gray-500">{showUnit}</span>}
      </div>

      {/* Range */}
      {rangeText && (
        <div className="pl-2 mb-2">
          <span className="text-xs text-gray-400">Normal: {rangeText}</span>
        </div>
      )}

      {/* Sparkline */}
      <div className="pl-1 mb-2">
        <Sparkline
          data={trendData}
          color={sparkColor}
          height={36}
          referenceLow={referenceLow}
          referenceHigh={referenceHigh}
        />
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between pl-2">
        <TrendIndicator trend={trend} pct={trendPct} />
        {(date || recordedAt) && (
          <span className="text-xs text-gray-400">
            {date ? formatDateStr(date) : new Date(recordedAt!).toLocaleDateString('en-US', {
              month: 'short',
              day: 'numeric',
              year: 'numeric',
            })}
          </span>
        )}
      </div>
    </div>
  );
}
