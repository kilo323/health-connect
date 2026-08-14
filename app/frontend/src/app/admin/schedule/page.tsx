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
  const [syncMessage, setSyncMessage] = useState('');
  const { syncInProgress, setSyncInProgress } = useSyncStore();

  useEffect(() => {
    loadSettings();

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

  const handleSave = async () => {
    if (!settings.cron_expression) return;

    setSaving(true);
    try {
      await apiClient.put('/admin/settings/schedule', settings);
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
    await handleSave();
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
        <div className="flex items-center justify-between mb-6">
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
            className={`flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors ${
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

          <button
            type="button"
            onClick={handleSyncNow}
            disabled={syncInProgress}
            className="btn-secondary flex items-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {syncInProgress ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {syncInProgress ? 'Syncing...' : 'Sync Now'}
          </button>
        </div>
      </div>
    </AuthLayout>
  );
}
