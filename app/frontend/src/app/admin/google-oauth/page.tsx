'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Link as LinkIcon, Save, Loader2, CheckCircle, AlertCircle } from 'lucide-react';
import apiClient from '@/lib/api-client';

export default function AdminGoogleOAuthPage() {
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [redirectUri, setRedirectUri] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    loadConfig();
  }, []);

  // Derive the callback URL from the origin the admin is currently using — so on a
  // public/reverse-proxied deployment it pre-fills the public URL automatically.
  const deriveRedirectUri = () =>
    `${window.location.origin}/api/health/google-health/callback`;

  const loadConfig = async () => {
    try {
      const res = await apiClient.get('/admin/settings/google-oauth');
      setClientId(res.data?.client_id || '');
      setClientSecret(res.data?.client_secret || '');
      // Pre-fill with the current-origin callback only when none is stored.
      setRedirectUri(res.data?.redirect_uri || deriveRedirectUri());
    } catch (error) {
      console.error('Failed to load Google OAuth config:', error);
      setRedirectUri(deriveRedirectUri());
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!clientId || !clientSecret) return;
    setSaving(true);
    try {
      await apiClient.put('/admin/settings/google-oauth', {
        client_id: clientId,
        client_secret: clientSecret,
        redirect_uri: redirectUri,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (error) {
      console.error('Failed to save Google OAuth config:', error);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Google OAuth Configuration</h1>

      <div className="card max-w-2xl">
        <div className="flex items-center gap-3 mb-6">
          <LinkIcon className="h-8 w-8 text-blue-600" />
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Google Cloud OAuth</h2>
            <p className="text-sm text-gray-500">Configure the OAuth credentials shared by all users</p>
          </div>
        </div>

        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
          <p className="text-sm text-blue-700">
            Create a project in the <a href="https://console.cloud.google.com/" target="_blank" className="underline font-medium">Google Cloud Console</a>, enable the <span className="font-medium">Google Health API</span> (the legacy Google Fit API was deprecated in 2026), and create OAuth 2.0 credentials. Under <span className="font-medium">Data Access</span>, add the <code>googlehealth.activity_and_fitness.readonly</code>, <code>googlehealth.health_metrics_and_measurements.readonly</code>, and <code>googlehealth.sleep.readonly</code> scopes. Add the redirect URI below to your authorized redirect URIs.
          </p>
        </div>

        {saved && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-4 flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-green-600" />
            <span className="text-sm text-green-700">Configuration saved successfully</span>
          </div>
        )}

        <form onSubmit={(e) => { e.preventDefault(); handleSave(); }} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Client ID</label>
            <input
              type="text"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="your-client-id.apps.googleusercontent.com"
              required
              className="input-field font-mono text-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Client Secret</label>
            <input
              type="password"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder="GOCSPX-..."
              required
              className="input-field font-mono text-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Redirect URI <span className="text-gray-400">(optional)</span></label>
            <input
              type="url"
              value={redirectUri}
              onChange={(e) => setRedirectUri(e.target.value)}
              placeholder={deriveRedirectUri()}
              className="input-field font-mono text-sm"
            />
            <p className="text-xs text-gray-500 mt-1">
              Pre-filled from the current page origin — on a public/reverse-proxied deployment this is
              your public URL. Register this exact value in Google Cloud as an authorized redirect URI.
            </p>
          </div>

          <button
            type="submit"
            disabled={saving || !clientId || !clientSecret}
            className="btn-primary flex items-center gap-2"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            <Save className="h-4 w-4" /> Save Configuration
          </button>
        </form>
      </div>
    </AuthLayout>
  );
}
