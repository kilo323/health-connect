'use client';

import { useState, useRef } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Settings, Upload, Download, FileText, CheckCircle, AlertCircle, Loader2 } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface ImportResult {
  imported: number;
  created: number;
  updated: number;
  errors: string[];
}

export default function AdminSystemSettingsPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const handleImport = async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      setImportError('Please select an app_settings.json file first.');
      return;
    }

    setImporting(true);
    setImportError(null);
    setImportResult(null);

    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await apiClient.post('/admin/settings/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      setImportResult(res.data);
    } catch (error: any) {
      const detail = error.response?.data?.detail || 'Failed to import settings. Please check the file format.';
      setImportError(detail);
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleExport = async () => {
    try {
      const res = await apiClient.get('/admin/settings/app_settings_export', {
        responseType: 'blob',
      });
      const blob = new Blob([res.data], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'app_settings.json';
      URL.revokeObjectURL(url);
      a.click();
    } catch (error) {
      console.error('Export failed:', error);
    }
  };

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">System Settings</h1>
      <p className="text-sm text-gray-500 mb-6 max-w-2xl">
        Administrative tools for managing app-wide configuration. Settings are stored in the
        <code className="bg-gray-100 px-1 rounded mx-1 text-xs">app_settings</code>
        database table.
      </p>

      <div className="grid gap-6 md:grid-cols-2 max-w-3xl">
        {/* ── App Settings Import ── */}
        <div className="card">
          <div className="flex items-center gap-3 mb-4">
            <Upload className="h-6 w-6 text-blue-600" />
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Import Settings</h2>
              <p className="text-sm text-gray-500">Upload an app_settings.json file</p>
            </div>
          </div>

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">JSON File</label>
              <input
                type="file"
                ref={fileInputRef}
                accept="application/json,.json"
                className="block w-full text-sm text-gray-700 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
              />
            </div>

            <button
              onClick={handleImport}
              disabled={importing}
              className="btn-primary w-full flex items-center justify-center gap-2"
            >
              {importing && <Loader2 className="h-4 w-4 animate-spin" />}
              <Upload className="h-4 w-4" />
              {importing ? 'Importing...' : 'Import'}
            </button>

            <p className="text-xs text-gray-500">
              Existing keys are overwritten; missing keys are inserted. Values for sensitive
              fields are obfuscated in server logs.
            </p>
          </div>

          {importResult && (
            <div className="mt-4 space-y-2">
              <div className="bg-green-50 border border-green-200 rounded-lg p-3">
                <div className="flex items-center gap-2">
                  <CheckCircle className="h-4 w-4 text-green-600" />
                  <span className="text-sm font-medium text-green-700">Import complete</span>
                </div>
                <p className="text-sm text-green-700 mt-1">
                  {importResult.imported} processed ({importResult.created} created, {importResult.updated} updated)
                </p>
              </div>
              {importResult.errors.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <AlertCircle className="h-4 w-4 text-red-600" />
                    <span className="text-sm font-medium text-red-700">
                      {importResult.errors.length} error(s)
                    </span>
                  </div>
                  <ul className="text-xs text-red-700 list-disc list-inside">
                    {importResult.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {importError && (
            <div className="mt-4 bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-red-600 mt-0.5" />
              <span className="text-sm text-red-700">{importError}</span>
            </div>
          )}
        </div>

        {/* ── App Settings Export ── */}
        <div className="card">
          <div className="flex items-center gap-3 mb-4">
            <Download className="h-6 w-6 text-emerald-600" />
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Export Settings</h2>
              <p className="text-sm text-gray-500">Download current app_settings.json</p>
            </div>
          </div>

          <button
            onClick={handleExport}
            className="btn-secondary w-full flex items-center justify-center gap-2"
          >
            <Download className="h-4 w-4" />
            Download app_settings.json
          </button>

          <div className="mt-4 p-3 bg-gray-50 rounded-lg">
            <FileText className="h-4 w-4 text-gray-500 mb-1" />
            <p className="text-xs text-gray-500">
              Exports all key/value pairs from the <code className="bg-gray-100 px-1 rounded">app_settings</code> table,
              including OAuth tokens and API keys. Store the file securely.
            </p>
          </div>
        </div>

        {/* ── Future admin tools placeholder ── */}
        <div className="card md:col-span-2 border-2 border-dashed border-gray-200">
          <div className="flex items-center gap-3 text-gray-400">
            <Settings className="h-6 w-6" />
            <div>
              <h2 className="text-lg font-semibold text-gray-700">More Tools</h2>
              <p className="text-sm">Additional admin configuration tools will appear here.</p>
            </div>
          </div>
        </div>
      </div>
    </AuthLayout>
  );
}
