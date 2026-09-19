'use client';

import { useState, useEffect, useCallback } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Bot, Save, Loader2, CheckCircle, AlertCircle, RefreshCw, FileText, RotateCcw, FlaskConical } from 'lucide-react';
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
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [customModelInput, setCustomModelInput] = useState('');

  // Prompt editor state
  const [promptContent, setPromptContent] = useState('');
  const [promptLoading, setPromptLoading] = useState(true);
  const [promptSaving, setPromptSaving] = useState(false);
  const [promptSaved, setPromptSaved] = useState(false);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [promptPath, setPromptPath] = useState('');

  // Metric LLM config state
  const [metricConfig, setMetricConfig] = useState<LLMConfig>({
    base_url: '',
    api_key: '',
    model: '',
  });
  const [metricLoading, setMetricLoading] = useState(true);
  const [metricSaving, setMetricSaving] = useState(false);
  const [metricSaved, setMetricSaved] = useState(false);
  const [metricModels, setMetricModels] = useState<string[]>([]);
  const [metricFetchingModels, setMetricFetchingModels] = useState(false);
  const [metricModelsError, setMetricModelsError] = useState<string | null>(null);
  const [metricCustomModel, setMetricCustomModel] = useState('');

  useEffect(() => {
    loadConfig();
    loadPrompt();
    loadMetricConfig();
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

  const loadPrompt = async () => {
    setPromptLoading(true);
    setPromptError(null);
    try {
      const res = await apiClient.get('/admin/settings/llm-prompt');
      setPromptContent(res.data.content || '');
      setPromptPath(res.data.path || '');
    } catch (error) {
      console.error('Failed to load prompt:', error);
      setPromptError('Failed to load prompt template');
    } finally {
      setPromptLoading(false);
    }
  };

  const loadMetricConfig = async () => {
    try {
      const res = await apiClient.get('/admin/settings/metric-llm');
      setMetricConfig(res.data || { base_url: '', api_key: '', model: '' });
    } catch (error) {
      console.error('Failed to load metric LLM config:', error);
    } finally {
      setMetricLoading(false);
    }
  };

  const savePrompt = async () => {
    setPromptSaving(true);
    setPromptError(null);
    try {
      await apiClient.put('/admin/settings/llm-prompt', { content: promptContent });
      setPromptSaved(true);
      setTimeout(() => setPromptSaved(false), 3000);
    } catch (error) {
      console.error('Failed to save prompt:', error);
      setPromptError('Failed to save prompt template');
    } finally {
      setPromptSaving(false);
    }
  };

  const resetPrompt = async () => {
    if (!confirm('Reset the prompt to the built-in default? Your customizations will be lost.')) return;
    setPromptSaving(true);
    setPromptError(null);
    try {
      const res = await apiClient.post('/admin/settings/llm-prompt/reset');
      setPromptContent(res.data.content || '');
      setPromptSaved(true);
      setTimeout(() => setPromptSaved(false), 3000);
    } catch (error) {
      console.error('Failed to reset prompt:', error);
      setPromptError('Failed to reset prompt template');
    } finally {
      setPromptSaving(false);
    }
  };

  const fetchMetricModels = useCallback(async () => {
    if (!metricConfig.base_url || !metricConfig.api_key) {
      setMetricModelsError('Please enter API Base URL and API Key first');
      return;
    }
    setMetricFetchingModels(true);
    setMetricModelsError(null);
    try {
      const res = await apiClient.get('/admin/llm/models', {
        params: { base_url: metricConfig.base_url, api_key: metricConfig.api_key }
      });
      if (res.data.error) {
        setMetricModelsError(res.data.error);
        setMetricModels([]);
      } else {
        setMetricModels(res.data.models);
        setMetricModelsError(null);
      }
    } catch (error) {
      console.error('Failed to fetch metric models:', error);
      setMetricModelsError('Failed to fetch models from endpoint');
      setMetricModels([]);
    } finally {
      setMetricFetchingModels(false);
    }
  }, [metricConfig.base_url, metricConfig.api_key]);

  const handleSaveMetric = async () => {
    setMetricSaving(true);
    try {
      await apiClient.put('/admin/settings/metric-llm', metricConfig);
      setMetricSaved(true);
      setTimeout(() => setMetricSaved(false), 3000);
    } catch (error) {
      console.error('Failed to save metric LLM config:', error);
    } finally {
      setMetricSaving(false);
    }
  };

  const fetchModels = useCallback(async () => {
    if (!config.base_url || !config.api_key) {
      setModelsError('Please enter API Base URL and API Key first');
      return;
    }
    
    setFetchingModels(true);
    setModelsError(null);
    
    try {
      const res = await apiClient.get('/admin/llm/models', {
        params: {
          base_url: config.base_url,
          api_key: config.api_key
        }
      });
      
      if (res.data.error) {
        setModelsError(res.data.error);
        setAvailableModels([]);
      } else {
        setAvailableModels(res.data.models);
        setModelsError(null);
      }
    } catch (error) {
      console.error('Failed to fetch models:', error);
      setModelsError('Failed to fetch models from endpoint');
      setAvailableModels([]);
    } finally {
      setFetchingModels(false);
    }
  }, [config.base_url, config.api_key]);

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
            <div className="flex gap-2">
              <select
                value={config.model}
                onChange={(e) => setConfig({ ...config, model: e.target.value })}
                className="input-field flex-1"
                disabled={availableModels.length === 0 && !customModelInput}
              >
                {availableModels.length > 0 ? (
                  availableModels.map((model) => (
                    <option key={model} value={model}>{model}</option>
                  ))
                ) : (
                  <option value="gpt-4">GPT-4 (default)</option>
                )}
              </select>
              <button
                type="button"
                onClick={fetchModels}
                disabled={fetchingModels || !config.base_url || !config.api_key}
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
                  setConfig({ ...config, model: e.target.value });
                }
              }}
              placeholder="Enter custom model name (optional)"
              className="input-field font-mono text-sm"
            />
            <p className="text-xs text-gray-500 mt-1">
              Use this for providers that don't support /v1/models. 
              Enter a custom model name to override the dropdown selection.
            </p>
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
          <p className="text-sm text-blue-700 mt-2 font-medium">
            ℹ️ The selected model must support <strong>vision</strong> (image input) to extract text from scanned PDFs and image uploads.
            Text-based PDFs and plain text files work with any model.
          </p>
        </div>
      </div>

      {/* Metric LLM Settings */}
      <div className="card max-w-2xl mt-8">
        <div className="flex items-center gap-3 mb-6">
          <FlaskConical className="h-8 w-8 text-emerald-600" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Metric Library LLM Settings</h2>
            <p className="text-sm text-gray-500">
              Configure a separate LLM for metric definition generation. Falls back to the document LLM above if left blank.
            </p>
          </div>
        </div>

        {metricSaved && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-6 flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-green-600" />
            <span className="text-sm text-green-700">Metric LLM configuration saved successfully</span>
          </div>
        )}

        {metricLoading ? (
          <div className="flex items-center gap-2 py-8 text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading...
          </div>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); handleSaveMetric(); }} className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Base URL</label>
              <input
                type="url"
                value={metricConfig.base_url}
                onChange={(e) => setMetricConfig({ ...metricConfig, base_url: e.target.value })}
                placeholder="https://api.openai.com/v1 (leave blank to use document LLM)"
                className="input-field font-mono text-sm"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
              <input
                type="password"
                value={metricConfig.api_key}
                onChange={(e) => setMetricConfig({ ...metricConfig, api_key: e.target.value })}
                placeholder="sk-... (leave blank to use document LLM)"
                className="input-field font-mono text-sm"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Model Name</label>
              <div className="flex gap-2">
                <select
                  value={metricConfig.model}
                  onChange={(e) => setMetricConfig({ ...metricConfig, model: e.target.value })}
                  className="input-field flex-1"
                  disabled={metricModels.length === 0 && !metricCustomModel}
                >
                  {metricModels.length > 0 ? (
                    metricModels.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))
                  ) : (
                    <option value="">{metricConfig.model || '— select —'}</option>
                  )}
                </select>
                <button
                  type="button"
                  onClick={fetchMetricModels}
                  disabled={metricFetchingModels || !metricConfig.base_url || !metricConfig.api_key}
                  className="btn-secondary flex items-center gap-2 px-4 py-2"
                  title="Fetch available models from endpoint"
                >
                  {metricFetchingModels ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  Refresh
                </button>
              </div>
              {metricModelsError && (
                <p className="text-xs text-red-500 mt-1">{metricModelsError}</p>
              )}
              {metricModels.length > 0 && (
                <p className="text-xs text-green-600 mt-1">
                  Found {metricModels.length} model(s) from endpoint
                </p>
              )}
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Custom Model Name</label>
              <input
                type="text"
                value={metricCustomModel}
                onChange={(e) => {
                  setMetricCustomModel(e.target.value);
                  if (e.target.value) {
                    setMetricConfig({ ...metricConfig, model: e.target.value });
                  }
                }}
                placeholder="Enter custom model name (optional)"
                className="input-field font-mono text-sm"
              />
            </div>

            <button
              type="submit"
              disabled={metricSaving}
              className="btn-primary flex items-center gap-2"
            >
              {metricSaving && <Loader2 className="h-4 w-4 animate-spin" />}
              <Save className="h-4 w-4" /> Save Metric LLM Configuration
            </button>
          </form>
        )}

        <div className="mt-6 p-4 bg-gray-50 rounded-lg">
          <AlertCircle className="h-4 w-4 text-yellow-600 mb-2" />
          <p className="text-sm text-gray-700">
            This LLM is used by the metric definition generator (e.g. <code className="bg-gray-100 px-1 rounded text-xs">refresh_metric_library.py</code>).
            It can be a cheaper/faster model since it only generates structured JSON definitions.
            Leave all fields blank to fall back to the document analysis LLM above, then to <code className="bg-gray-100 px-1 rounded text-xs">.env</code> variables.
          </p>
        </div>
      </div>

      {/* Prompt Editor Section */}
      <div className="card max-w-4xl mt-8">
        <div className="flex items-center gap-3 mb-6">
          <FileText className="h-8 w-8 text-purple-600" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Analysis Prompt Template</h2>
            <p className="text-sm text-gray-500">
              Customize the prompt sent to the LLM when analyzing documents.
              Use <code className="bg-gray-100 px-1 rounded text-xs">{'{document_content}'}</code> as a placeholder for the extracted document text.
            </p>
          </div>
        </div>

        {promptSaved && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-4 flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-green-600" />
            <span className="text-sm text-green-700">Prompt saved successfully</span>
          </div>
        )}

        {promptError && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 flex items-center gap-2">
            <AlertCircle className="h-4 w-4 text-red-600" />
            <span className="text-sm text-red-700">{promptError}</span>
          </div>
        )}

        {promptPath && (
          <p className="text-xs text-gray-400 mb-3 font-mono">File: {promptPath}</p>
        )}

        {promptLoading ? (
          <div className="flex items-center gap-2 py-8 text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading prompt...
          </div>
        ) : (
          <>
            <textarea
              value={promptContent}
              onChange={(e) => setPromptContent(e.target.value)}
              rows={20}
              className="input-field font-mono text-sm w-full resize-y"
              spellCheck={false}
            />
            <div className="flex items-center gap-3 mt-4">
              <button
                onClick={savePrompt}
                disabled={promptSaving}
                className="btn-primary flex items-center gap-2"
              >
                {promptSaving && <Loader2 className="h-4 w-4 animate-spin" />}
                <Save className="h-4 w-4" /> Save Prompt
              </button>
              <button
                onClick={loadPrompt}
                disabled={promptLoading}
                className="btn-secondary flex items-center gap-2"
              >
                <RefreshCw className="h-4 w-4" /> Reload from Disk
              </button>
              <button
                onClick={resetPrompt}
                disabled={promptSaving}
                className="btn-secondary flex items-center gap-2 text-red-600 hover:text-red-700"
              >
                <RotateCcw className="h-4 w-4" /> Reset to Default
              </button>
            </div>
          </>
        )}

        <div className="mt-4 p-4 bg-gray-50 rounded-lg">
          <AlertCircle className="h-4 w-4 text-yellow-600 mb-2" />
          <p className="text-sm text-gray-700">
            Changes take effect immediately — the prompt is loaded from disk on every analysis request (no caching).
            Make sure to include <code className="bg-gray-100 px-1 rounded text-xs">{'{document_content}'}</code> in your prompt, 
            otherwise the document text won't be injected.
          </p>
        </div>
      </div>
    </AuthLayout>
  );
}
