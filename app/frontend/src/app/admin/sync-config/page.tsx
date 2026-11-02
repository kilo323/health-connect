'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Activity, Save, Loader2, CheckCircle, AlertCircle, Info, RotateCcw, Wrench } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface MetricSetting {
  enabled: boolean;
  cutoff_days: number;
}

interface RollupConfig {
  default_cutoff_days: number;
  metrics: Record<string, MetricSetting>;
}

// Friendly labels + which metrics are shown as rollup-capable.
const METRIC_LABELS: Record<string, string> = {
  steps: 'Steps',
  distance: 'Distance',
  calories: 'Calories (daily only)',
  heart_rate: 'Heart Rate',
  move_minutes: 'Active Minutes',
  weight: 'Weight',
  body_fat_percentage: 'Body Fat %',
};

const METRIC_ORDER = [
  'steps', 'distance', 'calories', 'heart_rate', 'move_minutes', 'weight', 'body_fat_percentage',
];

interface RepairUser {
  id: number;
  username: string;
  earliest_date: string | null;
  history_days: number;
}

interface RepairMetric {
  id: string;
  label: string;
  interval: boolean;
}

interface RepairInfo {
  users: RepairUser[];
  history_days: number;
  earliest_date: string | null;
  metrics: RepairMetric[];
  default_metrics: string[];
}

interface RepairRow {
  day: string;
  metric_type: string;
  rollup: number;
  current: number | null;
  delta: number | null;
}

interface RepairSummary {
  user_id: number;
  timezone: string;
  days: number;
  applied: boolean;
  delete_raw: boolean;
  rows_written: number;
  raw_rows_deleted: number;
  rows: RepairRow[];
}

export default function AdminSyncConfigPage() {
  const [config, setConfig] = useState<RollupConfig>({ default_cutoff_days: 7, metrics: {} });
  const [defaults, setDefaults] = useState<RollupConfig>({ default_cutoff_days: 7, metrics: {} });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  // Google rollup repair state
  const [repairInfo, setRepairInfo] = useState<RepairInfo | null>(null);
  const [repairMetrics, setRepairMetrics] = useState<string[]>([]);
  const [repairDays, setRepairDays] = useState<number>(365);
  const [repairDeleteRaw, setRepairDeleteRaw] = useState(false);
  const [repairRunning, setRepairRunning] = useState(false);
  const [repairResult, setRepairResult] = useState<RepairSummary[] | null>(null);
  const [repairDryRun, setRepairDryRun] = useState(false);  const [repairError, setRepairError] = useState('');

  useEffect(() => {
    loadConfig();
    loadRepairInfo();
  }, []);

  const loadRepairInfo = async () => {
    try {
      const res = await apiClient.get('/admin/sync/rollup-repair');
      const info = res.data as RepairInfo;
      setRepairInfo(info);
      setRepairMetrics(info.default_metrics);
      if (info.history_days > 0) setRepairDays(info.history_days);
    } catch (err) {
      console.error('Failed to load rollup repair info:', err);
    }
  };

  const runRepair = async (dryRun: boolean) => {
    if (!repairMetrics.length) {
      setRepairError('Select at least one metric');
      return;
    }
    if (!dryRun && !window.confirm(
      `Rebuild daily rows for the last ${repairDays} days from Google's rollups?` +
      (repairDeleteRaw ? ' Superseded raw rows will be DELETED.' : '')
    )) {
      return;
    }
    setRepairRunning(true);
    setRepairError('');
    setRepairResult(null);
    try {
      const res = await apiClient.post('/admin/sync/rollup-repair', {
        days: repairDays,
        metrics: repairMetrics,
        delete_raw: repairDeleteRaw,
        dry_run: dryRun,
      });
      setRepairResult(res.data.summaries);
      setRepairDryRun(dryRun);
    } catch (err: any) {
      setRepairError(err.response?.data?.detail || 'Rollup repair failed');
    } finally {
      setRepairRunning(false);
    }
  };

  const toggleRepairMetric = (id: string) => {
    setRepairMetrics((cur) =>
      cur.includes(id) ? cur.filter((m) => m !== id) : [...cur, id]
    );
  };

  const loadConfig = async () => {
    try {
      const res = await apiClient.get('/admin/settings/rollup');
      setConfig(res.data.config || { default_cutoff_days: 7, metrics: {} });
      setDefaults(res.data.defaults || { default_cutoff_days: 7, metrics: {} });
    } catch (err) {
      console.error('Failed to load rollup config:', err);
      setError('Failed to load configuration');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError('');
    try {
      await apiClient.put('/admin/settings/rollup', config);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save configuration');
    } finally {
      setSaving(false);
    }
  };

  const setMetric = (name: string, patch: Partial<MetricSetting>) => {
    setConfig((c) => ({
      ...c,
      metrics: {
        ...c.metrics,
        [name]: { ...c.metrics[name], enabled: true, cutoff_days: c.default_cutoff_days, ...patch },
      },
    }));
  };

  const applyDefaultToAll = (value: number) => {
    setConfig((c) => {
      const metrics: Record<string, MetricSetting> = {};
      for (const name of METRIC_ORDER) {
        const existing = c.metrics[name];
        metrics[name] = {
          enabled: existing?.enabled ?? defaults.metrics[name]?.enabled ?? true,
          cutoff_days: value,
        };
      }
      return { ...c, default_cutoff_days: value, metrics };
    });
  };

  const resetMetricToDefault = (name: string) => {
    const d = defaults.metrics[name];
    if (d) setMetric(name, { enabled: d.enabled, cutoff_days: d.cutoff_days });
  };

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Sync Configuration</h1>

      <div className="card max-w-3xl">
        <div className="flex items-center gap-3 mb-4">
          <Activity className="h-8 w-8 text-blue-600" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Metric Rollup</h2>
            <p className="text-sm text-gray-500">
              Roll up older history to one value per day, while keeping recent data granular.
            </p>
          </div>
        </div>

        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-6 flex items-start gap-2">
          <Info className="h-4 w-4 text-blue-600 mt-0.5" />
          <p className="text-sm text-blue-700">
            For each enabled metric, data <strong>older than the cutoff</strong> is stored as a single
            daily total/average; data <strong>within the cutoff</strong> stays detailed. This is a global
            admin setting applied to all users.
          </p>
        </div>

        {saved && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-6 flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-green-600" />
            <span className="text-sm text-green-700">Configuration saved</span>
          </div>
        )}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-6 flex items-center gap-2">
            <AlertCircle className="h-4 w-4 text-red-600" />
            <span className="text-sm text-red-700">{error}</span>
          </div>
        )}

        {/* Global default */}
        <div className="mb-6 p-4 bg-gray-50 rounded-lg">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Default cutoff (days) — apply to all
          </label>
          <div className="flex items-center gap-3">
            <input
              type="number"
              min={0}
              value={config.default_cutoff_days}
              onChange={(e) => applyDefaultToAll(parseInt(e.target.value || '0', 10))}
              className="input-field w-28"
            />
            <span className="text-xs text-gray-500">
              Setting this updates every metric below; you can then adjust individual outliers.
              Use <strong>0</strong> to always roll up (no granular recent data).
            </span>
          </div>
        </div>

        {/* Per-metric table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs font-semibold text-gray-500 uppercase border-b border-gray-200">
                <th className="py-2 pr-4">Metric</th>
                <th className="py-2 pr-4">Roll up</th>
                <th className="py-2 pr-4">Cutoff (days)</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {METRIC_ORDER.map((name) => {
                const m = config.metrics[name] ?? {
                  enabled: defaults.metrics[name]?.enabled ?? true,
                  cutoff_days: config.default_cutoff_days,
                };
                return (
                  <tr key={name} className="border-b border-gray-100">
                    <td className="py-2.5 pr-4 font-medium text-gray-800">
                      {METRIC_LABELS[name] || name}
                    </td>
                    <td className="py-2.5 pr-4">
                      <input
                        type="checkbox"
                        checked={m.enabled}
                        onChange={(e) => setMetric(name, { enabled: e.target.checked })}
                        className="h-4 w-4 accent-blue-600"
                      />
                    </td>
                    <td className="py-2.5 pr-4">
                      <input
                        type="number"
                        min={0}
                        value={m.cutoff_days}
                        disabled={!m.enabled}
                        onChange={(e) => setMetric(name, { cutoff_days: parseInt(e.target.value || '0', 10) })}
                        className="input-field w-24 disabled:opacity-50"
                      />
                    </td>
                    <td className="py-2.5">
                      <button
                        type="button"
                        title="Reset to default"
                        onClick={() => resetMetricToDefault(name)}
                        className="text-gray-400 hover:text-gray-600"
                      >
                        <RotateCcw className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="mt-6">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="btn-primary flex items-center gap-2"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            <Save className="h-4 w-4" /> Save Configuration
          </button>
        </div>

        <div className="mt-6 p-4 bg-gray-50 rounded-lg">
          <p className="text-sm text-gray-700 mb-2 font-medium">Notes</p>
          <ul className="text-xs text-gray-600 space-y-1 list-disc list-inside">
            <li>Calories is only available as a daily total from Google — it always rolls up.</li>
            <li>Heart Rate is stored as daily Avg / Min / Max on both rolled-up and recent days.</li>
            <li>Changes take effect on the next sync; existing stored rows are not retroactively changed.</li>
          </ul>
        </div>
      </div>

      {/* Google Rollup Repair */}
      <div className="card max-w-3xl mt-8">
        <div className="flex items-center gap-3 mb-4">
          <Wrench className="h-8 w-8 text-blue-600" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Google Rollup Repair</h2>
            <p className="text-sm text-gray-500">
              Replace daily totals with Google's own deduplicated rollup values — the exact
              numbers the Google Health app shows. Use after restoring data or to fix history
              recorded before multi-device de-duplication existed. Recent days repair
              themselves on every sync.
            </p>
          </div>
        </div>

        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-6 flex items-start gap-2">
          <Info className="h-4 w-4 text-blue-600 mt-0.5" />
          <p className="text-sm text-blue-700">
            {repairInfo && repairInfo.history_days > 0 ? (
              <>
                Your history spans <strong>{repairInfo.history_days.toLocaleString()} days</strong>{' '}
                (oldest data: <strong>{repairInfo.earliest_date}</strong>).
                Use at least <strong>{repairInfo.history_days.toLocaleString()}</strong> days to
                cover everything — the input below is pre-filled with that value.
              </>
            ) : (
              <>No stored data found yet — run a sync first.</>
            )}
          </p>
        </div>

        {repairError && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-6 flex items-center gap-2">
            <AlertCircle className="h-4 w-4 text-red-600" />
            <span className="text-sm text-red-700">{repairError}</span>
          </div>
        )}

        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">Metrics to rebuild</label>
          <div className="grid grid-cols-2 gap-2">
            {(repairInfo?.metrics ?? []).map((m) => (
              <label key={m.id} className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={repairMetrics.includes(m.id)}
                  onChange={() => toggleRepairMetric(m.id)}
                  className="h-4 w-4 accent-blue-600"
                />
                {m.label}
                {m.interval && (
                  <span className="text-xs text-gray-400">(multi-device)</span>
                )}
              </label>
            ))}
          </div>
        </div>

        <div className="mb-6 p-4 bg-gray-50 rounded-lg">
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Days of history to rebuild
          </label>
          <input
            type="number"
            min={1}
            max={3650}
            value={repairDays}
            onChange={(e) => setRepairDays(parseInt(e.target.value || '1', 10))}
            className="input-field w-32"
          />
          <p className="text-xs text-gray-500 mt-2">
            {repairInfo && repairInfo.history_days > 0 ? (
              <>
                You need <strong>{repairInfo.history_days.toLocaleString()}</strong> days to cover
                all stored history (oldest data: {repairInfo.earliest_date}).
                {repairDays < repairInfo.history_days && (
                  <span className="text-amber-600">
                    {' '}Current value leaves older days untouched.
                  </span>
                )}
              </>
            ) : (
              <>How far back to fetch Google's daily rollups.</>
            )}
          </p>

          <label className="flex items-start gap-2 text-sm text-gray-700 mt-4">
            <input
              type="checkbox"
              checked={repairDeleteRaw}
              onChange={(e) => setRepairDeleteRaw(e.target.checked)}
              className="h-4 w-4 accent-blue-600 mt-0.5"
            />
            <span>
              Also delete the superseded raw rows of rebuilt days
              <span className="block text-xs text-gray-500">
                Daily totals are exact either way. Deleting removes the old inflated
                intraday detail for those days (charts fall back to hourly summaries).
              </span>
            </span>
          </label>
        </div>

        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => runRepair(true)}
            disabled={repairRunning || !repairMetrics.length}
            className="btn-secondary flex items-center gap-2"
          >
            {repairRunning && <Loader2 className="h-4 w-4 animate-spin" />}
            Preview
          </button>
          <button
            type="button"
            onClick={() => runRepair(false)}
            disabled={repairRunning || !repairMetrics.length}
            className="btn-primary flex items-center gap-2"
          >
            {repairRunning && <Loader2 className="h-4 w-4 animate-spin" />}
            <Wrench className="h-4 w-4" /> Run Repair
          </button>
        </div>

        {repairResult && (
          <div className="mt-6">
            <div className={`rounded-lg p-3 mb-4 flex items-center gap-2 ${
              repairDryRun
                ? 'bg-blue-50 border border-blue-200'
                : 'bg-green-50 border border-green-200'
            }`}>
              {repairDryRun ? <Info className="h-4 w-4 text-blue-600" /> : <CheckCircle className="h-4 w-4 text-green-600" />}
              <span className={`text-sm ${repairDryRun ? 'text-blue-700' : 'text-green-700'}`}>
                {repairDryRun ? 'Preview only — nothing was written. ' : ''}
                {repairResult.reduce((n, s) => n + s.rows_written, 0).toLocaleString()} daily row(s) written
                {repairResult.some((s) => s.delete_raw) && (
                  <> ({repairResult.reduce((n, s) => n + s.raw_rows_deleted, 0).toLocaleString()} raw row(s) deleted)</>
                )}
              </span>
            </div>

            {repairResult.map((s) => (
              <div key={s.user_id} className="mb-4">
                <p className="text-xs font-semibold text-gray-500 uppercase mb-2">
                  User {s.user_id} · {s.timezone} · {s.rows.length.toLocaleString()} day rows
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs font-semibold text-gray-500 uppercase border-b border-gray-200">
                        <th className="py-2 pr-4">Day</th>
                        <th className="py-2 pr-4">Metric</th>
                        <th className="py-2 pr-4 text-right">Google</th>
                        <th className="py-2 pr-4 text-right">Current</th>
                        <th className="py-2 text-right">Delta</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.rows.slice(0, 50).map((r, i) => (
                        <tr key={i} className="border-b border-gray-100">
                          <td className="py-1.5 pr-4 text-gray-800">{r.day}</td>
                          <td className="py-1.5 pr-4 text-gray-600">{r.metric_type}</td>
                          <td className="py-1.5 pr-4 text-right text-gray-800">
                            {r.rollup.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                          </td>
                          <td className="py-1.5 pr-4 text-right text-gray-600">
                            {r.current === null ? '—' : r.current.toLocaleString(undefined, { maximumFractionDigits: 1 })}
                          </td>
                          <td className={`py-1.5 text-right ${
                            r.delta && r.delta !== 0 ? 'text-amber-600' : 'text-gray-400'
                          }`}>
                            {r.delta === null ? '—' : r.delta.toLocaleString(undefined, { maximumFractionDigits: 1, signDisplay: 'exceptZero' })}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {s.rows.length > 50 && (
                  <p className="text-xs text-gray-400 mt-1">
                    … and {(s.rows.length - 50).toLocaleString()} more day(s)
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </AuthLayout>
  );
}
