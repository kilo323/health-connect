'use client';

import { useState, useEffect, useCallback } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Settings, Link as LinkIcon, Cloud, Bot, Database, CheckCircle, Loader2, AlertCircle, RefreshCw } from 'lucide-react';
import apiClient from '@/lib/api-client';

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<'google' | 'nextcloud' | 'llm'>('google');

  // Google Health Connect state
  const [googleConnected, setGoogleConnected] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);

  // Nextcloud state
  const [nextcloudUrl, setNextcloudUrl] = useState('');
  const [nextcloudUsername, setNextcloudUsername] = useState('');
  const [nextcloudPassword, setNextcloudPassword] = useState('');
  const [nextcloudConnected, setNextcloudConnected] = useState(false);
  const [nextcloudLoading, setNextcloudLoading] = useState(false);

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

  // Load current settings on mount
  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const [googleRes, nextcloudRes] = await Promise.all([
        apiClient.get('/sync/configs?data_type=google_health').catch(() => ({ data: [] })),
        apiClient.get('/sync/configs?data_type=nextcloud').catch(() => ({ data: [] })),
      ]);

      if (googleRes.data?.length > 0) setGoogleConnected(true);
      if (nextcloudRes.data?.length > 0) {
        setNextcloudConnected(true);
        const config = nextcloudRes.data[0];
        setNextcloudUrl(config.settings?.server_url || '');
        setNextcloudUsername(config.settings?.username || '');
      }

      // Load LLM config if admin
      try {
        const llmRes = await apiClient.get('/admin/settings/llm');
        const baseUrl = llmRes.data?.base_url || '';
        const apiKey = llmRes.data?.api_key || '';
        const savedModel = llmRes.data?.model || '';

        if (baseUrl) setLlmBaseUrl(baseUrl);
        if (apiKey) setLlmApiKey(apiKey);
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

            // Select saved model if it's in the list, otherwise put in custom field
            if (savedModel && models.includes(savedModel)) {
              setLlmModel(savedModel);
              setCustomModelInput('');
            } else if (savedModel) {
              setLlmModel('');
              setCustomModelInput(savedModel);
            }
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

  const handleGoogleConnect = async () => {
    setGoogleLoading(true);
    try {
      const res = await apiClient.post('/google-health/connect');
      // Redirect to Google OAuth
      window.location.href = res.data.url;
    } catch (error) {
      console.error('Failed to get OAuth URL:', error);
    } finally {
      setGoogleLoading(false);
    }
  };

  const handleNextcloudSave = async () => {
    if (!nextcloudUrl || !nextcloudUsername || !nextcloudPassword) return;
    
    setNextcloudLoading(true);
    try {
      await apiClient.post('/sync/configs', {
        data_type: 'nextcloud',
        sync_mode: 'folder_based',
        is_enabled: true,
        settings: {
          server_url: nextcloudUrl,
          username: nextcloudUsername,
          password: nextcloudPassword,
        },
      });
      setNextcloudConnected(true);
    } catch (error) {
      console.error('Failed to save Nextcloud config:', error);
    } finally {
      setNextcloudLoading(false);
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
    { key: 'llm' as const, label: 'LLM Configuration', icon: Bot },
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

          <p className="text-gray-600 mb-6">
            Connect your Google Health account to sync health data from Android devices. 
            You'll be redirected to Google's authorization page.
          </p>

          {!googleConnected ? (
            <button
              onClick={handleGoogleConnect}
              disabled={googleLoading}
              className="btn-primary flex items-center gap-2"
            >
              {googleLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              Connect Google Health Connect
            </button>
          ) : (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
              <CheckCircle className="h-5 w-5 text-green-600 mb-2" />
              <p className="text-sm text-green-700">
                Your Google Health account is connected. Data will sync according to your schedule settings.
              </p>
            </div>
          )}
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
            Configure your Nextcloud instance for document storage. Documents will be organized 
            in /HealthTracker/Unprocessed/, /Processed/, and /Archived/ folders.
          </p>

          {nextcloudConnected ? (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
              <CheckCircle className="h-5 w-5 text-green-600 mb-2" />
              <p className="text-sm text-green-700">
                Nextcloud is configured. Folders will be created automatically when you upload documents.
              </p>
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
    </AuthLayout>
  );
}
