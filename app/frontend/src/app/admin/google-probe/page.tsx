'use client';

import { useState } from 'react';
import AuthLayout from '@/components/AuthLayout';
import {
  Radar, Loader2, CheckCircle2, AlertTriangle, Radio, Clock,
  Package, CircleOff,
} from 'lucide-react';
import apiClient from '@/lib/api-client';

interface ProbeShape {
  payload_fields: Record<string, string | number | boolean | string[]>;
}

interface ProbeEntry {
  slug: string;
  sampled: boolean;
  point_count: number;
  synced: boolean;
  internal_name: string | null;
  shape: ProbeShape | null;
  note?: string;
}

interface ProbeResult {
  probed_user_id: number;
  probed: number;
  candidates_total: number;
  synced_slugs: string[];
  exists: ProbeEntry[];
  missing: string[];
  errored: { slug: string; error: string }[];
}

// Human-friendly labels for slugs we know about; falls back to the slug.
function label(slug: string): string {
  const words = slug.split('-');
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

function FieldChips({ shape }: { shape: ProbeShape | null }) {
  if (!shape) return <span className="text-xs text-gray-400 italic">no sample data</span>;
  const entries = Object.entries(shape.payload_fields || {});
  if (!entries.length) return <span className="text-xs text-gray-400 italic">no value fields</span>;
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {entries.map(([k, v]) => (
        <code
          key={k}
          className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-700 text-xs font-mono"
          title={JSON.stringify(v)}
        >
          {k}
        </code>
      ))}
    </div>
  );
}

export default function AdminGoogleProbePage() {
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [probedAt, setProbedAt] = useState<string | null>(null);

  const runProbe = async () => {
    setProbing(true);
    setError('');
    try {
      const res = await apiClient.get('/admin/sync/google-probe');
      setResult(res.data as ProbeResult);
      setProbedAt(new Date().toLocaleTimeString());
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Probe failed. Is a Google account connected?');
    } finally {
      setProbing(false);
    }
  };

  const notSynced = result ? result.exists.filter((e) => !e.synced) : [];
  const synced = result ? result.exists.filter((e) => e.synced) : [];

  return (
    <AuthLayout>
      <div className="flex items-center justify-between flex-wrap gap-4 mb-6">
        <div className="flex items-center gap-3">
          <Radar className="h-8 w-8 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Google Health Data Probe</h1>
            <p className="text-sm text-gray-500">
              Scan the live Google Health API for every data type it exposes, and see which are
              not yet synced.
            </p>
          </div>
        </div>

        <button
          onClick={runProbe}
          disabled={probing}
          className="btn-primary flex items-center gap-2"
        >
          {probing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Radar className="h-4 w-4" />}
          {probing ? 'Probing…' : 'Probe the API'}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-6 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-red-600 shrink-0" />
          <span className="text-sm text-red-700">{error}</span>
        </div>
      )}

      {result && (
        <>
          <div className="flex items-center gap-4 text-sm text-gray-500 mb-6">
            <span className="flex items-center gap-1.5">
              <Radio className="h-4 w-4" /> Probed user <strong className="text-gray-700">#{result.probed_user_id}</strong>
            </span>
            <span>{result.probed} candidates checked</span>
            {probedAt && (
              <span className="flex items-center gap-1.5">
                <Clock className="h-4 w-4" /> {probedAt}
              </span>
            )}
          </div>

          {/* Available but not synced — the interesting section */}
          <div className="card mb-6">
            <div className="flex items-center gap-2 mb-4">
              <Package className="h-5 w-5 text-amber-600" />
              <h2 className="text-lg font-semibold text-gray-900">
                Available but not synced
              </h2>
              <span className="px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 text-xs font-semibold">
                {notSynced.length}
              </span>
            </div>
            {notSynced.length === 0 ? (
              <p className="text-sm text-gray-500">
                Everything the API exposes is already being synced.
              </p>
            ) : (
              <div className="divide-y divide-gray-100">
                {notSynced.map((e) => (
                  <div key={e.slug} className="py-3">
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      <div>
                        <span className="font-medium text-gray-900">{label(e.slug)}</span>
                        <code className="ml-2 text-xs text-gray-400 font-mono">{e.slug}</code>
                        {e.note && (
                          <span className="ml-2 text-xs text-amber-600">{e.note}</span>
                        )}
                      </div>
                      <span className="text-xs text-gray-500">
                        {e.sampled ? `${e.point_count} sample point(s)` : 'no data on this account'}
                      </span>
                    </div>
                    <FieldChips shape={e.shape} />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Already synced */}
          <div className="card mb-6">
            <div className="flex items-center gap-2 mb-4">
              <CheckCircle2 className="h-5 w-5 text-green-600" />
              <h2 className="text-lg font-semibold text-gray-900">Already synced</h2>
              <span className="px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-semibold">
                {synced.length}
              </span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {synced.map((e) => (
                <div key={e.slug} className="px-3 py-2 rounded-lg bg-green-50 border border-green-100">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-900">{label(e.slug)}</span>
                    <code className="text-xs text-gray-400 font-mono">{e.internal_name}</code>
                  </div>
                  <FieldChips shape={e.shape} />
                </div>
              ))}
            </div>
          </div>

          {/* Not available */}
          <div className="card mb-6">
            <div className="flex items-center gap-2 mb-4">
              <CircleOff className="h-5 w-5 text-gray-400" />
              <h2 className="text-lg font-semibold text-gray-900">
                Not available in the v4 API
              </h2>
              <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 text-xs font-semibold">
                {result.missing.length}
              </span>
            </div>
            <p className="text-xs text-gray-500 mb-3">
              These data types do not exist in the Google Health API v4 catalog, so they cannot be
              pulled regardless of configuration.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {result.missing.map((slug) => (
                <span
                  key={slug}
                  className="px-2 py-0.5 rounded bg-gray-50 border border-gray-200 text-gray-500 text-xs font-mono"
                >
                  {slug}
                </span>
              ))}
            </div>
          </div>

          {result.errored.length > 0 && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3">
              <p className="text-sm font-medium text-red-700 mb-1">Request errors</p>
              <ul className="text-xs text-red-600 list-disc pl-5">
                {result.errored.map((e) => (
                  <li key={e.slug}>{e.slug}: {e.error}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {!result && !probing && (
        <div className="card p-8 text-center text-gray-500">
          <Radar className="h-10 w-10 mx-auto mb-3 text-gray-300" />
          <p className="text-sm">
            Click <strong>Probe the API</strong> to scan the live Google Health API and see which
            data types are exposed versus what is currently synced.
          </p>
        </div>
      )}
    </AuthLayout>
  );
}
