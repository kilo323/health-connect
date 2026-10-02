'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import apiClient from '@/lib/api-client';

interface SeriesPoint {
  t: string;
  min: number;
  max: number;
  avg: number;
  count: number;
}

interface SeriesResponse {
  metric_type: string;
  downsample: boolean;
  raw_row_count: number;
  bucket_seconds?: number;
  unit?: string;
  range_start: string | null;
  range_end: string | null;
  points: SeriesPoint[];
}

export interface IntradayOption {
  metric_type: string;
  label: string;
  unit: string;
  /** Most recent raw sample for this metric, used to open on a day with data. */
  last_at?: string | null;
  /** Earliest raw/hourly row, used to bound the date picker and the all-time window. */
  first_at?: string | null;
}

interface Props {
  options: IntradayOption[];
  /** Number of slices to request for the selected day. */
  maxPoints?: number;
  /**
   * When set, the chart spans the trailing `days` (0 = all available data)
   * instead of a single picked day, and the date picker is hidden. The
   * dashboard passes its own period selector here so both stay in sync.
   */
  days?: number;
}

function dayBounds(dateStr: string): { start: string; end: string } {
  // Build an explicit UTC range for the chosen calendar day. Using date strings
  // avoids the browser's local timezone shifting the window by a day.
  const start = new Date(`${dateStr}T00:00:00Z`);
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Trailing window ending now; `days === 0` starts at the earliest data. */
function rangeBounds(days: number, options: IntradayOption[]): { start: string; end: string } {
  const end = new Date();
  let start: Date;
  if (days > 0) {
    start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  } else {
    // `first_at` comes back as a naive ISO string (UTC); pin the offset so
    // local-timezone parsing doesn't shift the window by a day.
    const firsts = options
      .map((o) => o.first_at)
      .filter((v): v is string => Boolean(v))
      .map((v) => new Date(v.includes('Z') || v.includes('+') ? v : `${v}Z`).getTime());
    start = firsts.length
      ? new Date(Math.min(...firsts))
      : new Date(end.getTime() - 30 * 24 * 60 * 60 * 1000);
  }
  return { start: start.toISOString(), end: end.toISOString() };
}

/** Prefer heart rate as the opening view; it is the densest intraday series. */
function preferredDefault(options: IntradayOption[]): string {
  const hr = options.find(
    (o) => o.metric_type.toLowerCase() === 'heart rate'
  );
  return (hr ?? options[0]).metric_type;
}

/**
 * Intraday chart for metrics that sync raw samples (heart rate, active minutes).
 *
 * The daily endpoints collapse each metric to one value per day, so this calls
 * `/health/metrics/{type}/series`, which buckets the window into at most
 * `maxPoints` slices and returns min/max/avg per slice. Without `days` the
 * window is a single picked day; with `days` it is the trailing N days (0 =
 * all available data), so the chart stays in sync with the dashboard's own
 * period filter. Drawing the min-max band plus the average line keeps the
 * shape honest without shipping ~30k samples per day to the browser.
 */
export default function IntradayChart({ options, maxPoints = 180, days }: Props) {
  const rangeMode = days !== undefined;
  const [metricType, setMetricType] = useState<string>('');
  const [date, setDate] = useState<string>(todayUtc());
  const [showBand, setShowBand] = useState(true);
  const [data, setData] = useState<SeriesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Open on the densest series, and on the last day that actually has samples
  // rather than today (which is usually still empty early in the morning).
  useEffect(() => {
    if (!options.length) return;
    const next = preferredDefault(options);
    setMetricType((cur) =>
      options.some((o) => o.metric_type === cur) ? cur : next
    );
    if (rangeMode) return;
    const last = options.find((o) => o.metric_type === next)?.last_at;
    if (last) setDate(last.slice(0, 10));
    // Intentionally only re-run when the option set itself changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options]);

  const window = useMemo(
    () => (rangeMode ? rangeBounds(days!, options) : dayBounds(date)),
    [rangeMode, days, options, date]
  );

  // A multi-day window gets more slices so the shape stays legible; the
  // single-day mode keeps the default 180-slice density. 2000 is plenty for
  // a 1200px-wide chart and keeps multi-year "All" payloads small.
  const sliceCount = useMemo(
    () =>
      rangeMode
        ? Math.min(2000, Math.max(300, (days! || 30) * 12))
        : maxPoints,
    [rangeMode, days, maxPoints]
  );

  useEffect(() => {
    if (!metricType) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiClient
      .get<SeriesResponse>(`/health/metrics/${encodeURIComponent(metricType)}/series`, {
        params: { start: window.start, end: window.end, max_points: sliceCount },
      })
      .then((res) => {
        if (!cancelled) setData(res.data);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(
            e?.response?.status === 404
              ? 'Not found'
              : 'Could not load intraday data'
          );
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [metricType, window, sliceCount, rangeMode, days, maxPoints]);

  const chartData = useMemo(() => {
    if (!data?.points?.length) return [];
    // Recharts needs a short label; keep the full ISO in a separate field.
    // A multi-day window needs dates on the axis, not times of day.
    const fmt = (t: string) =>
      rangeMode
        ? new Date(t).toLocaleDateString([], { month: 'short', day: 'numeric' })
        : new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return data.points.map((p) => ({
      time: fmt(p.t),
      min: p.min,
      max: p.max,
      avg: Number(p.avg.toFixed(2)),
      count: p.count,
    }));
  }, [data, rangeMode]);

  const unit = useMemo(() => {
    const fromOption = options.find((o) => o.metric_type === metricType)?.unit;
    return data?.unit || fromOption || '';
  }, [options, metricType, data]);

  const bucketMinutes = data?.bucket_seconds
    ? Math.round(data.bucket_seconds / 60)
    : null;

  // Earliest day with any detail (raw or hourly) across the offered metrics.
  const earliestUtc = useMemo(() => {
    const firsts = options
      .map((o) => o.first_at)
      .filter((v): v is string => Boolean(v))
      .sort();
    return firsts.length ? firsts[0].slice(0, 10) : undefined;
  }, [options]);

  if (!options.length) return null;

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 mb-6">
      <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            Intraday detail
            {rangeMode && window && (
              <span className="ml-2 text-xs font-normal text-gray-400">
                {new Date(window.start).toLocaleDateString()} –{' '}
                {new Date(window.end).toLocaleDateString()}
              </span>
            )}
          </h2>
          <p className="text-xs text-gray-500">
            {data
              ? `${data.raw_row_count.toLocaleString()} raw samples${
                  bucketMinutes ? `, averaged into ${bucketMinutes}-minute buckets` : ''
                }`
              : 'Raw samples for metrics that sync intraday data'}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={metricType}
            onChange={(e) => setMetricType(e.target.value)}
            className="input-field w-auto"
            aria-label="Metric"
          >
            {options.map((o) => (
              <option key={o.metric_type} value={o.metric_type}>
                {o.label}
              </option>
            ))}
          </select>
          {!rangeMode && (
            <input
              type="date"
              value={date}
              min={earliestUtc}
              max={todayUtc()}
              onChange={(e) => setDate(e.target.value)}
              className="input-field w-auto"
              aria-label="Date"
            />
          )}
          <button
            onClick={() => setShowBand((v) => !v)}
            className="btn-secondary"
            title="Show the min/max range across each interval"
          >
            {showBand ? 'Hide range' : 'Show range'}
          </button>
        </div>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      {!error && !loading && chartData.length === 0 && (
        <p className="text-sm text-gray-500">
          {rangeMode
            ? 'No intraday data for this metric in the selected period. '
            : `No intraday data for this metric on ${date}. `}
          Raw samples are only kept for the configured retention window; older
          days fall back to hourly rows and then daily rollups.
        </p>
      )}

      {chartData.length > 0 && (
        <div style={{ height: 280 }}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis
                dataKey="time"
                tick={{ fontSize: 11 }}
                interval="preserveStartEnd"
                minTickGap={40}
              />
              <YAxis
                tick={{ fontSize: 11 }}
                width={45}
                unit={unit ? ` ${unit}` : ''}
                // Pad around the observed range instead of anchoring at 0 —
                // heart rate lives in a narrow band, and a 0-based axis flattens
                // the variation that is the whole point of an intraday chart.
                domain={[
                  (dataMin: number) => Math.floor(dataMin - 5),
                  (dataMax: number) => Math.ceil(dataMax + 5),
                ]}
              />
              <Tooltip
                formatter={(value, name) => [
                  typeof value === 'number' ? value.toFixed(1) : String(value ?? ''),
                  String(name),
                ]}
                labelFormatter={(label) =>
                  rangeMode ? label : `${date} ${label}`}
              />
              {showBand && (
                <Area
                  dataKey="max"
                  stroke="none"
                  fill="#93c5fd"
                  fillOpacity={0.35}
                  name="max"
                  isAnimationActive={false}
                />
              )}
              {showBand && (
                <Area
                  dataKey="min"
                  stroke="none"
                  fill="#ffffff"
                  fillOpacity={0.9}
                  name="min"
                  isAnimationActive={false}
                />
              )}
              <Line
                dataKey="avg"
                stroke="#2563eb"
                strokeWidth={1.5}
                dot={false}
                name="avg"
                isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
