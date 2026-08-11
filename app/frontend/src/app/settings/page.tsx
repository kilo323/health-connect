'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Link as LinkIcon, Cloud, CheckCircle, Loader2, AlertCircle, RefreshCw } from 'lucide-react';
import FolderPicker from '@/components/FolderPicker';
import apiClient from '@/lib/api-client';

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<'google' | 'nextcloud' | 'sync'>('google');

  // Google Health Connect state
  const [googleConnected, setGoogleConnected] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [error, setError] = useState('');
  const [googleConfigured, setGoogleConfigured] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [googleAccountLinked, setGoogleAccountLinked] = useState<boolean | null>(null);

  // Nextcloud state
  const [nextcloudUrl, setNextcloudUrl] = useState('');
  const [nextcloudUsername, setNextcloudUsername] = useState('');
  const [nextcloudPassword, setNextcloudPassword] = useState('');
  const [nextcloudConnected, setNextcloudConnected] = useState(false);
  const [nextcloudLoading, setNextcloudLoading] = useState(false);
  const [nextcloudSyncPath, setNextcloudSyncPath] = useState('/');

  // Sync Settings state (per-user)
  const [syncDaysBack, setSyncDaysBack] = useState(7);
  const [lastGoogleSync, setLastGoogleSync] = useState<string | null>(null);
  const [syncSettingsLoading, setSyncSettingsLoading] = useState(false);
  const [syncSettingsSaved, setSyncSettingsSaved] = useState(false);

  // Load current settings on mount; pick up the not-linked flag when
  // returning from the Google OAuth redirect.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('google_account_linked') === 'false') {
      setGoogleAccountLinked(false);
      window.history.replaceState({}, '', window.location.pathname);
    }
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const [googleStatusRes, googleConfigRes, nextcloudRes, syncSettingsRes] = await Promise.all([
        apiClient.get('/health/google-health/status').catch(() => ({ data: { is_linked: false } })),
        apiClient.get('/health/google-health/config').catch(() => ({ data: { client_id: '', is_configured: false } })),
        apiClient.get('/health/nextcloud/config').catch(() => ({ data: { server_url: '', username: '', is_configured: false } })),
        apiClient.get('/health/sync/settings').catch(() => ({ data: { sync_days_back: 7, last_google_sync: null } })),
      ]);

      setGoogleConnected(googleStatusRes.data?.is_linked || false);
      if (googleStatusRes.data?.account_linked !== null && googleStatusRes.data?.account_linked !== undefined) {
        setGoogleAccountLinked(googleStatusRes.data.account_linked);
      }
      setGoogleConfigured(googleConfigRes.data?.is_configured || false);
      setSyncDaysBack(syncSettingsRes.data?.sync_days_back ?? 7);
      setLastGoogleSync(syncSettingsRes.data?.last_google_sync ?? null);

      setNextcloudConnected(nextcloudRes.data?.is_configured || false);
      if (nextcloudRes.data?.server_url) setNextcloudUrl(nextcloudRes.data.server_url);
      if (nextcloudRes.data?.username) setNextcloudUsername(nextcloudRes.data.username);
      if (nextcloudRes.data?.sync_path) setNextcloudSyncPath(nextcloudRes.data.sync_path);
    } catch (error) {
      console.error('Failed to load settings:', error);
    }
  };

  const handleGoogleConnect = async () => {
    setGoogleLoading(true);
    try {
      const res = await apiClient.post('/health/google-health/connect');
      const url = res.data.oauth_url || res.data.url;
      if (url) {
        window.location.href = url;
      }
    } catch (error: any) {
      const detail = error.response?.data?.detail;
      if (detail?.includes('not configured')) {
        setError('Google Health Connect is not configured. Please ask an admin to set up OAuth credentials.');
      } else {
        console.error('Failed to get OAuth URL:', error);
      }
    } finally {
      setGoogleLoading(false);
    }
  };

  const handleGoogleDisconnect = async () => {
    if (!confirm('This will disconnect your Google Health account and revoke access. You can reconnect later. Continue?')) return;
    setDisconnecting(true);
    try {
      await apiClient.post('/health/google-health/disconnect');
      setGoogleConnected(false);
    } catch (error) {
      console.error('Failed to disconnect:', error);
    } finally {
      setDisconnecting(false);
    }
  };

  const handleNextcloudSave = async () => {
    if (!nextcloudUrl || !nextcloudUsername || !nextcloudPassword) return;

    setNextcloudLoading(true);
    try {
      await apiClient.put('/health/nextcloud/config', {
        server_url: nextcloudUrl,
        username: nextcloudUsername,
        password: nextcloudPassword,
        sync_path: nextcloudSyncPath,
      });
      setNextcloudConnected(true);
    } catch (error) {
      console.error('Failed to save Nextcloud config:', error);
    } finally {
      setNextcloudLoading(false);
    }
  };

  const handleNextcloudDisconnect = async () => {
    if (!confirm('Remove your Nextcloud configuration?')) return;
    try {
      await apiClient.delete('/health/nextcloud/config');
      setNextcloudConnected(false);
      setNextcloudUrl('');
      setNextcloudUsername('');
      setNextcloudSyncPath('/');
    } catch (error) {
      console.error('Failed to disconnect Nextcloud:', error);
    }
  };

  const handleSyncSettingsSave = async () => {
    setSyncSettingsLoading(true);
    try {
      await apiClient.put('/health/sync/settings', {
        sync_days_back: syncDaysBack,
      });
      setSyncSettingsSaved(true);
      setTimeout(() => setSyncSettingsSaved(false), 3000);
    } catch (error) {
      console.error('Failed to save sync settings:', error);
    } finally {
      setSyncSettingsLoading(false);
    }
  };

  const tabs = [
    { key: 'google' as const, label: 'Google Health Connect', icon: LinkIcon },
    { key: 'nextcloud' as const, label: 'Nextcloud Setup', icon: Cloud },
    { key: 'sync' as const, label: 'Sync Settings', icon: RefreshCw },
  ];

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Settings</h1>

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

      {/* Google Health Connect Tab */}
      {activeTab === 'google' && (
        <div className="card max-w-2xl">
          <div className="flex items-center gap-3 mb-4">
            <LinkIcon className="h-6 w-6 text-blue-600" />
            <h2 className="text-lg font-semibold text-gray-900">Google Health Connect</h2>
            {googleConnected && (
              <span className="flex items-center gap-1 text-sm text-green-600 font-medium ml-auto">
                <CheckCircle className="h-4 w-4" /> Connected
              </span>
            )}
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-4 text-sm">
              {error}
            </div>
          )}

          <p className="text-gray-600 mb-4">
            Connect your Google account to sync health and fitness data via the Google Health API (Fitbit, Pixel Watch, and other Google health sources).
          </p>

          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4 flex items-start gap-2">
            <AlertCircle className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
            <p className="text-sm text-amber-700">
              If you linked your account before the Google Health API migration, disconnect and reconnect — previously granted Google Fit permissions no longer work.
            </p>
          </div>

          <div className="border border-gray-200 rounded-lg p-4">
            {googleConnected ? (
              <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="h-5 w-5 text-green-600" />
                    <p className="text-sm text-green-700 font-medium">Connected</p>
                  </div>
                  <button
                    type="button"
                    onClick={handleGoogleDisconnect}
                    disabled={disconnecting}
                    className="text-sm text-red-600 hover:text-red-800 font-medium flex items-center gap-1 disabled:opacity-50"
                  >
                    {disconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                    Disconnect
                  </button>
                </div>
                <p className="text-xs text-green-600 mt-1 ml-7">Health data will sync according to your schedule settings.</p>
              </div>
            ) : (
              <div>
                {!googleConfigured && (
                  <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-3 flex items-start gap-2">
                    <AlertCircle className="h-4 w-4 text-yellow-600 mt-0.5" />
                    <p className="text-sm text-yellow-700">
                      Google OAuth is not configured yet. Ask an admin to set up credentials.
                    </p>
                  </div>
                )}

                <button
                  type="button"
                  onClick={handleGoogleConnect}
                  disabled={googleLoading || !googleConfigured}
                  className="btn-primary flex items-center gap-2"
                >
                  {googleLoading && <Loader2 className="h-4 w-4 animate-spin" />}
                  Connect Google Account
                </button>
              </div>
            )}

            {googleConnected && googleAccountLinked === false && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mt-3 flex items-start gap-2">
                <AlertCircle className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
                <p className="text-sm text-amber-700">
                  <span className="font-medium">Connected, but no Google Health data available.</span>{' '}
                  This Google account isn't linked to Google Health yet. Open the Fitbit mobile app, sign in with this Google account, and complete setup — then data will sync automatically.
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Nextcloud Tab */}
      {activeTab === 'nextcloud' && (
        <div className="card max-w-2xl">
          <div className="flex items-center gap-3 mb-4">
            <Cloud className="h-6 w-6 text-blue-600" />
            <h2 className="text-lg font-semibold text-gray-900">Nextcloud Setup</h2>
            {nextcloudConnected && (
              <span className="flex items-center gap-1 text-sm text-green-600 font-medium ml-auto">
                <CheckCircle className="h-4 w-4" /> Connected
              </span>
            )}
          </div>

          <p className="text-gray-600 mb-6">
            Configure your Nextcloud instance for document storage and health data syncing.
          </p>

          {nextcloudConnected ? (
            <div className="space-y-4">
              {/* Connection Status */}
              <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="h-5 w-5 text-green-600" />
                    <p className="text-sm text-green-700">
                      Connected to {nextcloudUsername}@{nextcloudUrl}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={handleNextcloudDisconnect}
                    className="text-sm text-red-600 hover:text-red-800 font-medium"
                  >
                    Disconnect
                  </button>
                </div>
              </div>

              {/* Sync Path */}
              <FolderPicker
                currentPath={nextcloudSyncPath}
                onSelect={async (path) => {
                  setNextcloudSyncPath(path);
                  try {
                    await apiClient.put('/health/nextcloud/config', {
                      sync_path: path,
                    });
                  } catch (error) {
                    console.error('Failed to save sync path:', error);
                  }
                }}
              />

              {/* Instructions */}
              <div className="border border-blue-200 bg-blue-50 rounded-lg p-4">
                <h4 className="text-sm font-semibold text-blue-900 mb-2">How to use Nextcloud sync</h4>
                <p className="text-xs text-blue-700 mb-3">
                  Place medical documents (bloodwork, lab tests, imaging, etc.) in the <strong>Unprocessed</strong> folder
                  under your sync path. Documents will be analyzed by AI when you trigger analysis.
                </p>
                <div className="grid grid-cols-3 gap-3 text-xs">
                  <div className="bg-white rounded-lg p-3 border border-blue-100">
                    <p className="font-semibold text-blue-800 mb-1">{nextcloudSyncPath}Unprocessed/</p>
                    <p className="text-blue-600">Place new documents here for processing</p>
                  </div>
                  <div className="bg-white rounded-lg p-3 border border-blue-100">
                    <p className="font-semibold text-blue-800 mb-1">{nextcloudSyncPath}Processed/</p>
                    <p className="text-blue-600">Documents after AI analysis is complete</p>
                  </div>
                  <div className="bg-white rounded-lg p-3 border border-blue-100">
                    <p className="font-semibold text-blue-800 mb-1">{nextcloudSyncPath}Archived/</p>
                    <p className="text-blue-600">Old or completed documents</p>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <form onSubmit={(e) => { e.preventDefault(); handleNextcloudSave(); }} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Server URL</label>
                <input
                  type="url"
                  value={nextcloudUrl}
                  onChange={(e) => setNextcloudUrl(e.target.value)}
                  placeholder="https://your-instance.nextcloud.com"
                  required
                  className="input-field"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
                <input
                  type="text"
                  value={nextcloudUsername}
                  onChange={(e) => setNextcloudUsername(e.target.value)}
                  required
                  className="input-field"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">App Password</label>
                <input
                  type="password"
                  value={nextcloudPassword}
                  onChange={(e) => setNextcloudPassword(e.target.value)}
                  required
                  placeholder="Generate an app password in Nextcloud settings"
                  className="input-field"
                />
              </div>

              <button
                type="submit"
                disabled={nextcloudLoading || !nextcloudUrl || !nextcloudUsername}
                className="btn-primary flex items-center gap-2"
              >
                {nextcloudLoading && <Loader2 className="h-4 w-4 animate-spin" />}
                Save & Connect
              </button>
            </form>
          )}
        </div>
      )}

      {/* Sync Settings Tab */}
      {activeTab === 'sync' && (
        <div className="card max-w-2xl">
          <h2 className="text-lg font-semibold text-gray-900 mb-1">Google Fit Sync Settings</h2>
          <p className="text-sm text-gray-500 mb-4">Configure how far back to sync health data from Google Fit.</p>

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Days Back for Sync
              </label>
              <p className="text-xs text-gray-500 mb-2">
                On first sync, data will be fetched this many days into the past. After that, only new data since the last sync is fetched (with a 1-day overlap to catch late-arriving data).
              </p>
              <input
                type="number"
                min={1}
                max={365}
                value={syncDaysBack}
                onChange={(e) => setSyncDaysBack(parseInt(e.target.value) || 7)}
                className="input-field w-32"
              />
            </div>

            {lastGoogleSync && (
              <div className="bg-gray-50 rounded-lg p-3">
                <p className="text-sm text-gray-600">
                  <span className="font-medium">Last Google Fit sync:</span>{' '}
                  {new Date(lastGoogleSync).toLocaleString()}
                </p>
              </div>
            )}

            <button
              onClick={handleSyncSettingsSave}
              disabled={syncSettingsLoading}
              className="btn-primary flex items-center gap-2"
            >
              {syncSettingsLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              {syncSettingsSaved ? 'Saved!' : 'Save Settings'}
            </button>
          </div>
        </div>
      )}
    </AuthLayout>
  );
}
