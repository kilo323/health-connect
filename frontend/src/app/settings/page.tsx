'use client';

import { useState } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Settings, Link as LinkIcon, Cloud, Bot, Database, CheckCircle, Loader2, AlertCircle } from 'lucide-react';
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
  const [llmBaseUrl, setLlmBaseUrl] = useState('https://api.openai.com/v1');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [llmModel, setLlmModel] = useState('gpt-4');
  const [llmLoading, setLlmLoading] = useState(false);

  // Load current settings on mount
  useState(() => {
    loadSettings();
  });

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
        setLlmBaseUrl(llmRes.data.base_url);
        setLlmModel(llmRes.data.model);
      } catch { /* not admin */ }
    } catch (error) {
      console.error('Failed to load settings:', error);
    }
  };

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
              <select
                value={llmModel}
                onChange={(e) => setLlmModel(e.target.value)}
                className="input-field"
              >
                <option value="gpt-4">GPT-4</option>
                <option value="gpt-4-turbo">GPT-4 Turbo</option>
                <option value="gpt-3.5-turbo">GPT-3.5 Turbo</option>
                <option value="gpt-4o">GPT-4o</option>
              </select>
            </div>

            <button
              type="submit"
              disabled={llmLoading || !llmBaseUrl || !llmApiKey}
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
