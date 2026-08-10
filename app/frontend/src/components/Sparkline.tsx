'use client';

import { AreaChart, Area, ResponsiveContainer, ReferenceLine } from 'recharts';

interface SparklineProps {
  data: number[];
  color?: string;
  height?: number;
  referenceLow?: number | null;
  referenceHigh?: number | null;
  className?: string;
}

export default function Sparkline({
  data,
  color = '#3b82f6',
  height = 40,
  referenceLow,
  referenceHigh,
  className = '',
}: SparklineProps) {
  if (!data || data.length < 2) {
    return (
      <div
        className={`flex items-center justify-center text-gray-300 text-xs ${className}`}
        style={{ height }}
      >
        No trend data
      </div>
    );
  }

  const chartData = data.map((value, index) => ({ index, value }));
  const min = Math.min(...data);
  const max = Math.max(...data);
  const padding = (max - min) * 0.15 || 1;

  return (
    <div className={className} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={`sparkGrad-${color.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.3} />
              <stop offset="100%" stopColor={color} stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#sparkGrad-${color.replace('#', '')})`}
            dot={false}
            isAnimationActive={false}
          />
          {referenceLow != null && (
            <ReferenceLine
              y={referenceLow}
              stroke="#ef4444"
              strokeDasharray="3 3"
              strokeWidth={1}
              strokeOpacity={0.5}
            />
          )}
          {referenceHigh != null && (
            <ReferenceLine
              y={referenceHigh}
              stroke="#ef4444"
              strokeDasharray="3 3"
              strokeWidth={1}
              strokeOpacity={0.5}
            />
          )}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
