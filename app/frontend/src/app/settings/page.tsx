'use client';

import { useState, useEffect, useCallback } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Settings, Link as LinkIcon, Cloud, Bot, Database, CheckCircle, Loader2, AlertCircle, RefreshCw, Clock, Play } from 'lucide-react';
import FolderPicker from '@/components/FolderPicker';
import apiClient from '@/lib/api-client';

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<'google' | 'nextcloud' | 'llm'>('google');

  // Google Health Connect state
  const [googleConnected, setGoogleConnected] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [error, setError] = useState('');
  const [googleClientId, setGoogleClientId] = useState('');
  const [googleClientSecret, setGoogleClientSecret] = useState('');
  const [googleRedirectUri, setGoogleRedirectUri] = useState('');
  const [googleConfigured, setGoogleConfigured] = useState(false);
  const [googleConfigLoading, setGoogleConfigLoading] = useState(false);
  const [googleConfigSaved, setGoogleConfigSaved] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);

  // Nextcloud state
  const [nextcloudUrl, setNextcloudUrl] = useState('');
  const [nextcloudUsername, setNextcloudUsername] = useState('');
  const [nextcloudPassword, setNextcloudPassword] = useState('');
  const [nextcloudConnected, setNextcloudConnected] = useState(false);
  const [nextcloudLoading, setNextcloudLoading] = useState(false);
  const [nextcloudSyncPath, setNextcloudSyncPath] = useState('/');

  // LLM Config state (admin only)
  const [llmBaseUrl, setLlmBaseUrl] = useState('');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [llmModel, setLlmModel] = useState('');
  const [llmLoading, setLlmLoading] = useState(false);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [customModelInput, setCustomModelInput] = useState('');
  const [configLoaded, setConfigLoaded] = useState(false);
  const [savedModel, setSavedModel] = useState('');

  // Sync Schedule state (admin only)
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [cronExpression, setCronExpression] = useState('0 2 * * *');
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [scheduleSaved, setScheduleSaved] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState('');
  const [disconnecting, setDisconnecting] = useState(false);

  // Load current settings on mount
  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const [googleStatusRes, googleConfigRes, nextcloudRes, whoamiRes] = await Promise.all([
        apiClient.get('/health/google-health/status').catch(() => ({ data: { is_linked: false } })),
        apiClient.get('/health/google-health/config').catch(() => ({ data: { client_id: '', is_configured: false } })),
        apiClient.get('/health/nextcloud/config').catch(() => ({ data: { server_url: '', username: '', is_configured: false } })),
        apiClient.get('/users/me').catch(() => ({ data: { role: 'user' } })),
      ]);

      const userIsAdmin = whoamiRes.data?.role === 'admin';
      setIsAdmin(userIsAdmin);
      setGoogleConnected(googleStatusRes.data?.is_linked || false);
      setGoogleConfigured(googleConfigRes.data?.is_configured || false);

      // Only admins can see and edit Google OAuth credentials
      if (userIsAdmin) {
        try {
          const adminGoogleRes = await apiClient.get('/admin/settings/google-oauth');
          setGoogleClientId(adminGoogleRes.data?.client_id || '');
          setGoogleClientSecret(adminGoogleRes.data?.client_secret || '');
          setGoogleRedirectUri(adminGoogleRes.data?.redirect_uri || '');
        } catch { /* not admin or not configured */ }

        // Load sync schedule
        try {
          const scheduleRes = await apiClient.get('/admin/settings/schedule');
          setScheduleEnabled(scheduleRes.data?.is_enabled || false);
          setCronExpression(scheduleRes.data?.cron_expression || '0 2 * * *');
        } catch { /* not admin */ }
      }
      setNextcloudConnected(nextcloudRes.data?.is_configured || false);
      if (nextcloudRes.data?.server_url) setNextcloudUrl(nextcloudRes.data.server_url);
      if (nextcloudRes.data?.username) setNextcloudUsername(nextcloudRes.data.username);
      if (nextcloudRes.data?.sync_path) setNextcloudSyncPath(nextcloudRes.data.sync_path);

      // Load LLM config if admin
      try {
        const llmRes = await apiClient.get('/admin/settings/llm');
        const baseUrl = llmRes.data?.base_url || '';
        const apiKey = llmRes.data?.api_key || '';
        const savedModelVal = llmRes.data?.model || '';

        if (baseUrl) setLlmBaseUrl(baseUrl);
        if (apiKey) setLlmApiKey(apiKey);
        setSavedModel(savedModelVal);
        setConfigLoaded(true);

        // Auto-fetch models if we have a configured endpoint
        if (baseUrl && apiKey) {
          setFetchingModels(true);
          try {
            const modelsRes = await apiClient.get('/admin/llm/models', {
              params: { base_url: baseUrl, api_key: apiKey }
            });
            const models: string[] = modelsRes.data?.models || [];
            setAvailableModels(models);

            if (modelsRes.data?.error) {
              setModelsError(modelsRes.data.error);
            }

            // Set models and selected model in one state update so they render together
            const selectedModel = (savedModelVal && models.includes(savedModelVal)) ? savedModelVal : '';
            const customInput = (savedModelVal && !models.includes(savedModelVal)) ? savedModelVal : '';
            setAvailableModels(models);
            setLlmModel(selectedModel);
            setCustomModelInput(customInput);
          } catch {
            setModelsError('Failed to fetch models from endpoint');
          } finally {
            setFetchingModels(false);
          }
        }
      } catch { /* not admin */ }
    } catch (error) {
      console.error('Failed to load settings:', error);
    }
  };

  const fetchModels = useCallback(async () => {
    if (!llmBaseUrl || !llmApiKey) {
      setModelsError('Please enter API Base URL and API Key first');
      return;
    }

    setFetchingModels(true);
    setModelsError(null);

    try {
      const res = await apiClient.get('/admin/llm/models', {
        params: { base_url: llmBaseUrl, api_key: llmApiKey }
      });

      if (res.data.error) {
        setModelsError(res.data.error);
        setAvailableModels([]);
      } else {
        const models = res.data.models || [];
        setAvailableModels(models);
        setModelsError(null);

        // Re-select saved model if available, otherwise use custom input
        if (llmModel && models.includes(llmModel)) {
          setCustomModelInput('');
        } else if (customModelInput && models.includes(customModelInput)) {
          setLlmModel(customModelInput);
          setCustomModelInput('');
        } else {
          setLlmModel('');
        }
      }
    } catch (error) {
      console.error('Failed to fetch models:', error);
      setModelsError('Failed to fetch models from endpoint');
      setAvailableModels([]);
    } finally {
      setFetchingModels(false);
    }
  }, [llmBaseUrl, llmApiKey, llmModel, customModelInput]);

  const handleGoogleConfigSave = async () => {
    if (!googleClientId || !googleClientSecret) return;
    setGoogleConfigLoading(true);
    try {
      await apiClient.put('/admin/settings/google-oauth', {
        client_id: googleClientId,
        client_secret: googleClientSecret,
        redirect_uri: googleRedirectUri,
      });
      setGoogleConfigured(true);
      setGoogleConfigSaved(true);
      setTimeout(() => setGoogleConfigSaved(false), 3000);
    } catch (error) {
      console.error('Failed to save Google config:', error);
    } finally {
      setGoogleConfigLoading(false);
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
        setError('Google Health Connect is not configured. Please set up OAuth credentials first.');
      } else {
        console.error('Failed to get OAuth URL:', error);
      }
    } finally {
      setGoogleLoading(false);
    }
  };

  const handleScheduleSave = async () => {
    setScheduleLoading(true);
    try {
      await apiClient.put('/admin/settings/schedule', {
        is_enabled: scheduleEnabled,
        cron_expression: cronExpression,
      });
      setScheduleSaved(true);
      setTimeout(() => setScheduleSaved(false), 3000);
    } catch (error) {
      console.error('Failed to save schedule:', error);
    } finally {
      setScheduleLoading(false);
    }
  };

  const handleSyncNow = async () => {
    setSyncing(true);
    setSyncMessage('');
    try {
      await apiClient.post('/admin/sync/now');
      setSyncMessage('Sync started! Data will be fetched from Google Fit and processed.');
      setTimeout(() => setSyncMessage(''), 5000);
    } catch (error: any) {
      setSyncMessage(error.response?.data?.detail || 'Failed to start sync');
    } finally {
      setSyncing(false);
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

  const cronPresets = [
    { label: 'Daily at 2 AM', value: '0 2 * * *' },
    { label: 'Every 6 hours', value: '0 */6 * * *' },
    { label: 'Weekly Sunday 3 AM', value: '0 3 * * 0' },
    { label: 'Weekdays midnight', value: '0 0 * * 1-5' },
  ];

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

  const handleLlmSave = async () => {
    if (!llmBaseUrl || !llmApiKey) return;
    
    setLlmLoading(true);
    try {
      await apiClient.put('/admin/settings/llm', {
        base_url: llmBaseUrl,
        api_key: llmApiKey,
        model: llmModel,
      });
    } catch (error) {
      console.error('Failed to save LLM config:', error);
    } finally {
      setLlmLoading(false);
    }
  };

  const tabs = [
    { key: 'google' as const, label: 'Google Health Connect', icon: LinkIcon },
    { key: 'nextcloud' as const, label: 'Nextcloud Setup', icon: Cloud },
    ...(isAdmin ? [
      { key: 'llm' as const, label: 'LLM Configuration', icon: Bot },
      { key: 'schedule' as const, label: 'Sync Schedule', icon: Clock },
    ] : []),
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

          <p className="text-gray-600 mb-6">
            Connect your Google account to sync health and fitness data from Google Fit.
          </p>

          {/* App Configuration - Admin Only */}
          {isAdmin && (
            <div className="border border-gray-200 rounded-lg p-4 mb-6">
              <h3 className="text-sm font-semibold text-gray-900 mb-1">App Configuration</h3>
              <p className="text-xs text-gray-500 mb-4">Google Cloud OAuth credentials shared by all users.</p>

              {googleConfigSaved && (
                <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-4 flex items-center gap-2">
                  <CheckCircle className="h-4 w-4 text-green-600" />
                  <span className="text-sm text-green-700">Credentials saved</span>
                </div>
              )}

              <form onSubmit={(e) => { e.preventDefault(); handleGoogleConfigSave(); }} className="space-y-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Client ID</label>
                  <input
                    type="text"
                    value={googleClientId}
                    onChange={(e) => setGoogleClientId(e.target.value)}
                    placeholder="your-client-id.apps.googleusercontent.com"
                    required
                    className="input-field font-mono text-sm"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Client Secret</label>
                  <input
                    type="password"
                    value={googleClientSecret}
                    onChange={(e) => setGoogleClientSecret(e.target.value)}
                    placeholder="GOCSPX-..."
                    required
                    className="input-field font-mono text-sm"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Redirect URI <span className="text-gray-400">(optional)</span></label>
                  <input
                    type="url"
                    value={googleRedirectUri}
                    onChange={(e) => setGoogleRedirectUri(e.target.value)}
                    placeholder={`${typeof window !== 'undefined' ? window.location.origin : ''}/api/health/google-health/callback`}
                    className="input-field font-mono text-sm"
                  />
                  <p className="text-xs text-gray-500 mt-1">Leave empty to auto-detect from your current URL</p>
                </div>

                <button
                  type="submit"
                  disabled={googleConfigLoading || !googleClientId || !googleClientSecret}
                  className="btn-primary flex items-center gap-2"
                >
                  {googleConfigLoading && <Loader2 className="h-4 w-4 animate-spin" />}
                  Save Credentials
                </button>
              </form>
            </div>
          )}

          {/* Account Connection - All Users */}
          <div className="border border-gray-200 rounded-lg p-4">
            <h3 className="text-sm font-semibold text-gray-900 mb-1">Account Connection</h3>
            <p className="text-xs text-gray-500 mb-4">Link your personal Google account to sync health data.</p>

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
                      {isAdmin ? 'Save your OAuth credentials above first.' : 'Google OAuth is not configured yet. Ask an admin to set up credentials.'}
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

      {/* LLM Configuration Tab */}
      {activeTab === 'llm' && (
        <div className="card max-w-2xl">
          <div className="flex items-center gap-3 mb-4">
            <Bot className="h-6 w-6 text-blue-600" />
            <h2 className="text-lg font-semibold text-gray-900">LLM Configuration</h2>
          </div>

          <p className="text-gray-600 mb-6">
            Configure the AI model used for analyzing uploaded health documents. 
            Any OpenAI-compatible API endpoint is supported.
          </p>

          <form onSubmit={(e) => { e.preventDefault(); handleLlmSave(); }} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Base URL</label>
              <input
                type="url"
                value={llmBaseUrl}
                onChange={(e) => setLlmBaseUrl(e.target.value)}
                placeholder="https://api.openai.com/v1"
                required
                className="input-field"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
              <input
                type="password"
                value={llmApiKey}
                onChange={(e) => setLlmApiKey(e.target.value)}
                placeholder="sk-..."
                required
                className="input-field font-mono text-sm"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
              <div className="flex gap-2">
                <select
                  value={customModelInput ? '' : llmModel}
                  onChange={(e) => { setLlmModel(e.target.value); setCustomModelInput(''); }}
                  className="input-field flex-1"
                  disabled={availableModels.length === 0 && !fetchingModels}
                >
                  {availableModels.length > 0 ? (
                    <>
                      {!llmModel && !customModelInput && <option value="">-- Select a model --</option>}
                      {availableModels.map((model) => (
                        <option key={model} value={model}>{model}</option>
                      ))}
                    </>
                  ) : (
                    <option value="">No models available — enter custom name below</option>
                  )}
                </select>
                <button
                  type="button"
                  onClick={fetchModels}
                  disabled={fetchingModels || !llmBaseUrl || !llmApiKey}
                  className="btn-secondary flex items-center gap-2 px-4 py-2"
                  title="Fetch available models from endpoint"
                >
                  {fetchingModels ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  Refresh
                </button>
              </div>

              {modelsError && (
                <p className="text-xs text-red-500 mt-1">{modelsError}</p>
              )}

              {availableModels.length > 0 && (
                <p className="text-xs text-green-600 mt-1">
                  Found {availableModels.length} model(s) from endpoint
                </p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Custom Model Name
              </label>
              <input
                type="text"
                value={customModelInput}
                onChange={(e) => {
                  setCustomModelInput(e.target.value);
                  if (e.target.value) {
                    setLlmModel(e.target.value);
                  } else {
                    // Cleared custom input — reset to first available model if any
                    setLlmModel(availableModels.length > 0 ? availableModels[0] : '');
                  }
                }}
                placeholder="Enter custom model name (optional)"
                className="input-field font-mono text-sm"
              />
              <p className="text-xs text-gray-500 mt-1">
                Use this to enter a model name not in the list
              </p>
            </div>

            <button
              type="submit"
              disabled={llmLoading || !llmBaseUrl || !llmApiKey || !llmModel}
              className="btn-primary flex items-center gap-2"
            >
              {llmLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              Save Configuration
            </button>
          </form>
        </div>
      )}

      {/* Sync Schedule Tab (Admin only) */}
      {activeTab === 'schedule' && isAdmin && (
        <div className="card max-w-2xl">
          <div className="flex items-center gap-3 mb-4">
            <Clock className="h-6 w-6 text-blue-600" />
            <h2 className="text-lg font-semibold text-gray-900">Sync Schedule</h2>
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${scheduleEnabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
              {scheduleEnabled ? 'Active' : 'Disabled'}
            </span>
          </div>

          <p className="text-gray-600 mb-6">
            Configure when health data automatically syncs from Google Fit. Only admins can manage this setting.
          </p>

          {scheduleSaved && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-4 flex items-center gap-2">
              <CheckCircle className="h-4 w-4 text-green-600" />
              <span className="text-sm text-green-700">Schedule saved successfully</span>
            </div>
          )}

          <form onSubmit={(e) => { e.preventDefault(); handleScheduleSave(); }} className="space-y-4">
            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
              <div>
                <p className="text-sm font-medium text-gray-900">Enable Scheduled Sync</p>
                <p className="text-xs text-gray-500">Automatically sync health data on a schedule</p>
              </div>
              <button
                type="button"
                onClick={() => setScheduleEnabled(!scheduleEnabled)}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${scheduleEnabled ? 'bg-blue-600' : 'bg-gray-300'}`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${scheduleEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
              </button>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Cron Expression</label>
              <input
                type="text"
                value={cronExpression}
                onChange={(e) => setCronExpression(e.target.value)}
                className="input-field font-mono text-sm"
                placeholder="0 2 * * *"
                disabled={!scheduleEnabled}
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              {cronPresets.map((preset) => (
                <button
                  key={preset.value}
                  type="button"
                  onClick={() => setCronExpression(preset.value)}
                  disabled={!scheduleEnabled}
                  className={`text-left px-3 py-2 rounded-lg border text-sm transition-colors ${
                    cronExpression === preset.value
                      ? 'border-blue-500 bg-blue-50 text-blue-700'
                      : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50 disabled:opacity-50'
                  }`}
                >
                  <div className="font-medium">{preset.label}</div>
                  <div className="text-xs text-gray-400 font-mono">{preset.value}</div>
                </button>
              ))}
            </div>

            <button
              type="submit"
              disabled={scheduleLoading}
              className="btn-primary flex items-center gap-2"
            >
              {scheduleLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              Save Schedule
            </button>
          </form>

          <div className="mt-6 pt-4 border-t border-gray-200">
            <p className="text-sm font-medium text-gray-700 mb-3">Manual Sync</p>
            <p className="text-xs text-gray-500 mb-3">Trigger an immediate sync of all connected users' health data from Google Fit.</p>

            {syncMessage && (
              <div className={`rounded-lg p-3 mb-3 text-sm ${syncMessage.includes('Failed') ? 'bg-red-50 text-red-700' : 'bg-blue-50 text-blue-700'}`}>
                {syncMessage}
              </div>
            )}

            <button
              type="button"
              onClick={handleSyncNow}
              disabled={syncing}
              className="btn-secondary flex items-center gap-2"
            >
              {syncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              Sync Now
            </button>
          </div>
        </div>
      )}
    </AuthLayout>
  );
}
