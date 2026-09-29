'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Database, Save, Play, Square, Loader2, CheckCircle, AlertCircle, Info } from 'lucide-react';
import apiClient from '@/lib/api-client';
import { useSyncStore } from '@/store/sync-store';

interface ScheduleSettings {
  is_enabled: boolean;
  cron_expression: string;
}

export default function AdminSchedulePage() {
  const [settings, setSettings] = useState<ScheduleSettings>({
    is_enabled: false,
    cron_expression: '0 2 * * *',
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [syncMessage, setSyncMessage] = useState('');
  const [retentionDays, setRetentionDays] = useState(90);
  const [hourlyRetentionDays, setHourlyRetentionDays] = useState(730);
  const [savingRetention, setSavingRetention] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const [compactionMessage, setCompactionMessage] = useState('');
  const { syncInProgress, setSyncInProgress } = useSyncStore();

  const loadRetention = async () => {
    try {
      const res = await apiClient.get('/admin/sync/compaction');
      setRetentionDays(res.data?.raw_retention_days ?? 90);
      setHourlyRetentionDays(res.data?.hourly_retention_days ?? 730);
    } catch (error) {
      console.error('Failed to load raw retention:', error);
    }
  };

  const handleSaveRetention = async () => {
    setSavingRetention(true);
    setCompactionMessage('');
    try {
      const res = await apiClient.put('/admin/sync/compaction', {
        raw_retention_days: retentionDays,
        hourly_retention_days: hourlyRetentionDays,
      });
      setRetentionDays(res.data.raw_retention_days);
      if (res.data.hourly_retention_days != null) {
        setHourlyRetentionDays(res.data.hourly_retention_days);
      }
      setCompactionMessage('Retention windows saved.');
    } catch (error: any) {
      setCompactionMessage(
        error.response?.data?.detail || 'Failed to save retention windows'
      );
    } finally {
      setSavingRetention(false);
    }
  };

  const handleCompact = async (dryRun: boolean) => {
    setCompacting(true);
    setCompactionMessage('');
    try {
      const res = await apiClient.post('/admin/sync/compact', { dry_run: dryRun });
      const d = res.data || {};
      const lines = [
        `${d.dry_run ? 'Would compact' : 'Compacted'}: ${d.days_compacted ?? 0} day group(s)`,
        `Hourly rows ${d.dry_run ? 'that would be written' : 'written'}: ${d.hourly_rows_written ?? 0}`,
        `Daily rollup rows: ${d.daily_rows_written ?? 0}`,
        `Raw rows ${d.dry_run ? 'that would be deleted' : 'deleted'}: ${d.raw_rows_deleted ?? 0}`,
      ];
      if (d.hourly_rows_deleted) {
        lines.push(`Hourly rows pruned: ${d.hourly_rows_deleted}`);
      }
      if (d.skipped) lines.push(`Skipped: ${d.skipped}`);
      if ((d.days_kept_no_daily || []).length) {
        lines.push(`Kept (no rollup could be written): ${d.days_kept_no_daily.length}`);
      }
      if ((d.errors || []).length) {
        lines.push(`Errors: ${d.errors.length} — ${d.errors[0]}`);
      }
      setCompactionMessage(lines.join('\n'));
    } catch (error: any) {
      setCompactionMessage(error.response?.data?.detail || 'Failed to run compaction');
    } finally {
      setCompacting(false);
    }
  };

  useEffect(() => {
    loadSettings();
    loadRetention();

    // Poll sync status so the button state stays accurate even if the user
    // leaves this page while a sync is running.
    let cancelled = false;
    const checkStatus = async () => {
      try {
        const res = await apiClient.get('/admin/status/sync');
        if (!cancelled) {
          setSyncInProgress(res.data?.sync_in_progress ?? false);
        }
      } catch (error) {
        // ignore — user may not be logged in
      }
    };
    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [setSyncInProgress]);

  const loadSettings = async () => {
    try {
      const res = await apiClient.get('/admin/settings/schedule');
      setSettings(res.data || { is_enabled: false, cron_expression: '0 2 * * *' });
    } catch (error) {
      console.error('Failed to load schedule settings:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (next?: ScheduleSettings) => {
    // Allow a caller (the enable/disable toggle) to pass the state it wants
    // persisted — React state updates are async, so reading `settings` right
    // after setSettings() would still send the old value.
    const payload = next ?? settings;
    if (!payload.cron_expression) return;

    if (next) {
      setSettings(next);
    }
    setSaving(true);
    try {
      await apiClient.put('/admin/settings/schedule', payload);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);

      // Reload scheduler status
      const statusRes = await apiClient.get('/admin/status/scheduler');
      if (statusRes.data.is_running) {
        console.log('Scheduler restarted successfully');
      }
    } catch (error) {
      console.error('Failed to save schedule settings:', error);
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async () => {
    // The button label reflects the CURRENT state, so clicking it must send
    // the inverted state. Previously it called handleSave() unchanged, which
    // re-submitted is_enabled=false and stopped the scheduler on every
    // "Enable" click.
    await handleSave({ ...settings, is_enabled: !settings.is_enabled });
  };

  const handleSyncNow = async () => {
    if (syncInProgress) return;
    setSyncing(true);
    setSyncInProgress(true);
    setSyncMessage('');
    try {
      await apiClient.post('/admin/sync/now');
      setSyncMessage('Sync started! Syncing from connected accounts.');
      setTimeout(() => setSyncMessage(''), 5000);
    } catch (error: any) {
      setSyncInProgress(false);
      setSyncMessage(error.response?.data?.detail || 'Failed to start sync');
    } finally {
      setSyncing(false);
    }
  };

  const handleBackfill = async () => {
    if (syncInProgress) return;
    setBackfilling(true);
    setSyncMessage('');
    try {
      await apiClient.post('/admin/sync/backfill');
      setSyncMessage('Backfill started! Re-fetching all user data from the beginning.');
      setTimeout(() => setSyncMessage(''), 5000);
    } catch (error: any) {
      setSyncMessage(error.response?.data?.detail || 'Failed to start backfill');
    } finally {
      setBackfilling(false);
    }
  };

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  // Parse cron expression for display
  const parseCronDisplay = (cron: string) => {
    try {
      const parts = cron.split(' ');
      if (parts.length !== 5) return 'Invalid cron';
      
      const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
      
      return `Every ${dayOfMonth === '*' ? 'day' : dayOfMonth} at ${hour.padStart(2, '0')}:${minute.padStart(2, '0')} UTC`;
    } catch {
      return cron;
    }
  };

  const cronExamples = [
    { label: 'Daily at 2 AM', value: '0 2 * * *' },
    { label: 'Every 6 hours', value: '0 */6 * * *' },
    { label: 'Weekly on Sunday at 3 AM', value: '0 3 * * 0' },
    { label: 'Every weekday at midnight', value: '0 0 * * 1-5' },
  ];

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Schedule Configuration</h1>

      <div className="card max-w-2xl">
        <div className="flex items-center justify-between gap-4 mb-6 flex-wrap">
          <div className="flex items-center gap-3">
            <Database className="h-8 w-8 text-blue-600" />
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Automated Sync Schedule</h2>
              <p className="text-sm text-gray-500">Configure when health data syncs from Google Health Connect to Nextcloud</p>
            </div>
          </div>

          <button
            onClick={handleToggle}
            disabled={saving}
            className={`flex shrink-0 items-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors ${
              settings.is_enabled 
                ? 'bg-red-100 text-red-700 hover:bg-red-200' 
                : 'bg-green-100 text-green-700 hover:bg-green-200'
            }`}
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            {settings.is_enabled ? (
              <>
                <Square className="h-4 w-4" /> Disable
              </>
            ) : (
              <>
                <Play className="h-4 w-4" /> Enable
              </>
            )}
          </button>
        </div>

        {saved && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-6 flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-green-600" />
            <span className="text-sm text-green-700">Schedule updated successfully</span>
          </div>
        )}

        {settings.is_enabled && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-6 flex items-start gap-2">
            <Info className="h-4 w-4 text-blue-600 mt-0.5" />
            <p className="text-sm text-blue-700">
              Scheduler is currently running with cron expression: <strong>{settings.cron_expression}</strong>
            </p>
          </div>
        )}

        {!settings.is_enabled && (
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 mb-6 flex items-start gap-2">
            <AlertCircle className="h-4 w-4 text-gray-500 mt-0.5" />
            <p className="text-sm text-gray-700">
              Scheduler is disabled. Health data will not sync automatically until you enable it.
            </p>
          </div>
        )}

        <form onSubmit={(e) => { e.preventDefault(); handleSave(); }} className="space-y-5">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Cron Expression</label>
            <input
              type="text"
              value={settings.cron_expression}
              onChange={(e) => setSettings({ ...settings, cron_expression: e.target.value })}
              className="input-field font-mono text-sm"
              placeholder="0 2 * * *"
              disabled={!settings.is_enabled}
            />
            <p className="text-xs text-gray-500 mt-1">
              Current schedule: <strong>{parseCronDisplay(settings.cron_expression)}</strong>
            </p>
          </div>

          {/* Cron Examples */}
          <div className="pt-2 border-t border-gray-100">
            <label className="block text-sm font-medium text-gray-700 mb-3">Quick Presets</label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {cronExamples.map((example) => (
                <button
                  key={example.value}
                  type="button"
                  onClick={() => setSettings({ ...settings, cron_expression: example.value })}
                  className={`text-left px-3 py-2 rounded-lg border text-sm transition-colors ${
                    settings.cron_expression === example.value
                      ? 'border-blue-500 bg-blue-50 text-blue-700'
                      : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <div className="font-medium">{example.label}</div>
                  <div className="text-xs text-gray-500 font-mono mt-1">{example.value}</div>
                </button>
              ))}
            </div>
          </div>

          <button
            type="submit"
            disabled={saving || !settings.cron_expression}
            className="btn-primary flex items-center gap-2"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            <Save className="h-4 w-4" /> Save Schedule
          </button>
        </form>

        {/* Info Section */}
        <div className="mt-6 p-4 bg-gray-50 rounded-lg">
          <AlertCircle className="h-4 w-4 text-yellow-600 mb-2" />
          <p className="text-sm text-gray-700 mb-2 font-medium">About Scheduled Sync</p>
          <ul className="text-xs text-gray-600 space-y-1 list-disc list-inside">
            <li>The scheduler will fetch health data from Google Health Connect at the scheduled time</li>
            <li>Downloaded data is uploaded to your configured Nextcloud instance</li>
            <li>Documents are organized into Unprocessed/, Processed/, and Archived/ folders</li>
            <li>All credentials are encrypted at rest using Fernet encryption</li>
          </ul>
        </div>

        {/* Sync Now Section */}
        <div className="mt-6 pt-4 border-t border-gray-200">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">Manual Sync</h3>
          <p className="text-xs text-gray-500 mb-3">Trigger an immediate sync of all connected users' health data.</p>

          {syncMessage && (
            <div className={`rounded-lg p-3 mb-3 text-sm ${syncMessage.includes('Failed') ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'}`}>
              {syncMessage}
            </div>
          )}

          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              onClick={handleSyncNow}
              disabled={syncInProgress}
              className="btn-secondary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {syncInProgress ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {syncInProgress ? 'Syncing...' : 'Sync Now'}
            </button>
            <button
              type="button"
              onClick={handleBackfill}
              disabled={syncInProgress || backfilling}
              className="btn-secondary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {backfilling ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
              {backfilling ? 'Backfilling...' : 'Backfill All Data'}
            </button>
          </div>
        </div>

        {/* Raw Data Retention Section */}
        <div className="mt-6 pt-4 border-t border-gray-200">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">Data Retention &amp; Rollups</h3>
          <p className="text-xs text-gray-500 mb-3">
            Intraday samples are compacted in two stages: every closed day is
            aggregated into <strong>hourly rows</strong> (kept for intraday charts long
            after raw data is gone), and past the raw window each day is summarised into
            a <strong>daily rollup</strong> before the raw rows are deleted. Daily rows are
            never pruned. Days older than the <code className="text-gray-600">Sync Days Back</code>{' '}
            setting can no longer be re-fetched from Google, so the summary is always
            written before the raw rows are removed. Set either window to{' '}
            <strong>0</strong> to keep that tier forever.
          </p>

          {compactionMessage && (
            <div className={`rounded-lg p-3 mb-3 text-sm whitespace-pre-line ${
              compactionMessage.includes('Failed') ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'
            }`}>
              {compactionMessage}
            </div>
          )}

          <form
            onSubmit={(e) => { e.preventDefault(); handleSaveRetention(); }}
            className="flex items-end gap-4 flex-wrap mb-3"
          >
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Keep raw samples for (days)
              </label>
              <input
                type="number"
                min={0}
                max={3650}
                value={retentionDays}
                onChange={(e) => setRetentionDays(Number(e.target.value))}
                className="input-field w-32"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Keep hourly rows for (days)
              </label>
              <input
                type="number"
                min={0}
                max={3650}
                value={hourlyRetentionDays}
                onChange={(e) => setHourlyRetentionDays(Number(e.target.value))}
                className="input-field w-32"
              />
            </div>
            <button type="submit" disabled={savingRetention} className="btn-secondary flex items-center gap-2">
              {savingRetention && <Loader2 className="h-4 w-4 animate-spin" />}
              <Save className="h-4 w-4" /> Save
            </button>
          </form>

          <p className="text-xs text-gray-500 mb-3">
            Compaction runs nightly at 03:17 UTC when the scheduler is enabled. With the
            scheduler disabled, run it manually:
          </p>
          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => handleCompact(true)}
              disabled={compacting}
              className="btn-secondary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {compacting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Info className="h-4 w-4" />}
              Preview Compaction
            </button>
            <button
              type="button"
              onClick={() => handleCompact(false)}
              disabled={compacting}
              className="btn-secondary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {compacting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
              Compact Now
            </button>
          </div>
        </div>
      </div>
    </AuthLayout>
  );
}
