'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Bot, Save, Loader2, CheckCircle, AlertCircle } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface LLMConfig {
  base_url: string;
  api_key: string;
  model: string;
}

export default function AdminLLMConfigPage() {
  const [config, setConfig] = useState<LLMConfig>({
    base_url: '',
    api_key: '',
    model: 'gpt-4',
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const res = await apiClient.get('/admin/settings/llm');
      setConfig(res.data || { base_url: '', api_key: '', model: 'gpt-4' });
    } catch (error) {
      console.error('Failed to load LLM config:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!config.base_url || !config.api_key) return;

    setSaving(true);
    try {
      await apiClient.put('/admin/settings/llm', config);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (error) {
      console.error('Failed to save LLM config:', error);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">LLM Configuration</h1>

      <div className="card max-w-2xl">
        <div className="flex items-center gap-3 mb-6">
          <Bot className="h-8 w-8 text-blue-600" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900">AI Model Settings</h2>
            <p className="text-sm text-gray-500">Configure the LLM used for document analysis</p>
          </div>
        </div>

        {saved && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-6 flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-green-600" />
            <span className="text-sm text-green-700">Configuration saved successfully</span>
          </div>
        )}

        <form onSubmit={(e) => { e.preventDefault(); handleSave(); }} className="space-y-5">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">API Base URL</label>
            <input
              type="url"
              value={config.base_url}
              onChange={(e) => setConfig({ ...config, base_url: e.target.value })}
              placeholder="https://api.openai.com/v1"
              className="input-field font-mono text-sm"
            />
            <p className="text-xs text-gray-500 mt-1">The base URL for the OpenAI-compatible API endpoint</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
            <input
              type="password"
              value={config.api_key}
              onChange={(e) => setConfig({ ...config, api_key: e.target.value })}
              placeholder="sk-..."
              className="input-field font-mono text-sm"
            />
            <p className="text-xs text-gray-500 mt-1">Your API key for authentication</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Model Name</label>
            <select
              value={config.model}
              onChange={(e) => setConfig({ ...config, model: e.target.value })}
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
            disabled={saving || !config.base_url || !config.api_key}
            className="btn-primary flex items-center gap-2"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            <Save className="h-4 w-4" /> Save Configuration
          </button>
        </form>

        <div className="mt-6 p-4 bg-gray-50 rounded-lg">
          <AlertCircle className="h-4 w-4 text-yellow-600 mb-2" />
          <p className="text-sm text-gray-700">
            This configuration is used for analyzing uploaded health documents. 
            Make sure the API endpoint supports chat completions with JSON output.
          </p>
        </div>
      </div>
    </AuthLayout>
  );
}
