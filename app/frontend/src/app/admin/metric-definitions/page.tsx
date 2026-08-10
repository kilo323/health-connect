'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { TestTube2, Plus, Edit3, Trash2, AlertTriangle, RefreshCw, Link2, Save, X, ChevronDown, ChevronUp, Database } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface ReferenceRange {
  sex?: string | null;
  age_min?: number | null;
  age_max?: number | null;
  low?: number | null;
  high?: number | null;
  operator?: string | null;
  unit?: string | null;
  source?: string | null;
}

interface MetricDefinition {
  id: number;
  name: string;
  category: string | null;
  unit: string | null;
  data_type: string;
  description: string | null;
  aliases: string[];
  reference_ranges: ReferenceRange[];
  unit_conversions: Record<string, number>;
}

interface UnmatchedMetric {
  metric_type: string;
  count: number;
  latest_value: string | null;
  latest_unit: string | null;
  documents: string[];
  suggested_match: string | null;
}

const emptyDefinition: Omit<MetricDefinition, 'id'> = {
  name: '',
  category: '',
  unit: '',
  data_type: 'float',
  description: '',
  aliases: [],
  reference_ranges: [],
  unit_conversions: {},
};

export default function AdminMetricDefinitionsPage() {
  const [definitions, setDefinitions] = useState<MetricDefinition[]>([]);
  const [unmatched, setUnmatched] = useState<UnmatchedMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [form, setForm] = useState<Omit<MetricDefinition, 'id'>>(emptyDefinition);
  const [aliasInput, setAliasInput] = useState('');
  const [unitConvKey, setUnitConvKey] = useState('');
  const [unitConvVal, setUnitConvVal] = useState('');
  const [saving, setSaving] = useState(false);
  const [normalizing, setNormalizing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedUnmatched, setExpandedUnmatched] = useState<string | null>(null);
  const [mapTarget, setMapTarget] = useState<Record<string, number>>({});
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [defRes, unmatchedRes] = await Promise.all([
        apiClient.get('/admin/metric-definitions'),
        apiClient.get('/admin/metric-definitions/unmatched'),
      ]);
      setDefinitions(defRes.data || []);
      setUnmatched(unmatchedRes.data || []);
    } catch (err) {
      console.error('Failed to load metric definitions:', err);
    } finally {
      setLoading(false);
    }
  };

  const showMessage = (type: 'success' | 'error', text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 4000);
  };

  const startCreate = () => {
    setForm({ ...emptyDefinition });
    setAliasInput('');
    setUnitConvKey('');
    setUnitConvVal('');
    setIsCreating(true);
    setEditingId(null);
  };

  const startEdit = (def: MetricDefinition) => {
    setForm({ ...def });
    setAliasInput(def.aliases.join(', '));
    setUnitConvKey('');
    setUnitConvVal('');
    setIsCreating(false);
    setEditingId(def.id);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setIsCreating(false);
    setForm(emptyDefinition);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = {
        ...form,
        aliases: aliasInput.split(',').map(a => a.trim()).filter(Boolean),
        unit_conversions: Object.fromEntries(
          Object.entries(form.unit_conversions || {}).filter(([k, v]) => k && v)
        ),
      };

      if (editingId) {
        await apiClient.put(`/admin/metric-definitions/${editingId}`, payload);
        showMessage('success', `Updated "${form.name}"`);
      } else {
        await apiClient.post('/admin/metric-definitions', payload);
        showMessage('success', `Created "${form.name}"`);
      }
      cancelEdit();
      await loadData();
    } catch (err: any) {
      showMessage('error', err.response?.data?.detail || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (def: MetricDefinition) => {
    if (!confirm(`Delete "${def.name}"? Health metrics using it will be unlinked.`)) return;
    try {
      await apiClient.delete(`/admin/metric-definitions/${def.id}`);
      showMessage('success', `Deleted "${def.name}"`);
      await loadData();
    } catch (err: any) {
      showMessage('error', err.response?.data?.detail || 'Delete failed');
    }
  };

  const handleNormalize = async () => {
    setNormalizing(true);
    try {
      const res = await apiClient.post('/admin/metric-definitions/normalize');
      showMessage('success', res.data.message);
      await loadData();
    } catch (err: any) {
      showMessage('error', err.response?.data?.detail || 'Normalization failed');
    } finally {
      setNormalizing(false);
    }
  };

  const handleRefreshLibrary = async () => {
    setRefreshing(true);
    try {
      const res = await apiClient.post('/admin/metric-definitions/refresh-library');
      showMessage('success', res.data.message);
      await loadData();
    } catch (err: any) {
      showMessage('error', err.response?.data?.detail || 'Library refresh failed');
    } finally {
      setRefreshing(false);
    }
  };

  const handleMapUnmatched = async (metricType: string) => {
    const defId = mapTarget[metricType];
    if (!defId) return;
    try {
      const res = await apiClient.post('/admin/metric-definitions/map-unmatched', {
        metric_type: metricType,
        definition_id: defId,
      });
      showMessage('success', res.data.message);
      await loadData();
    } catch (err: any) {
      showMessage('error', err.response?.data?.detail || 'Map failed');
    }
  };

  const addReferenceRange = () => {
    setForm(f => ({
      ...f,
      reference_ranges: [
        ...(f.reference_ranges || []),
        { sex: null, age_min: null, age_max: null, low: null, high: null, operator: null, unit: f.unit, source: null },
      ],
    }));
  };

  const updateReferenceRange = (idx: number, field: string, value: any) => {
    setForm(f => {
      const ranges = [...(f.reference_ranges || [])];
      ranges[idx] = { ...ranges[idx], [field]: value };
      return { ...f, reference_ranges: ranges };
    });
  };

  const removeReferenceRange = (idx: number) => {
    setForm(f => ({
      ...f,
      reference_ranges: (f.reference_ranges || []).filter((_, i) => i !== idx),
    }));
  };

  const addUnitConversion = () => {
    if (!unitConvKey.trim()) return;
    setForm(f => ({
      ...f,
      unit_conversions: { ...(f.unit_conversions || {}), [unitConvKey.trim()]: parseFloat(unitConvVal) || 1 },
    }));
    setUnitConvKey('');
    setUnitConvVal('');
  };

  const removeUnitConversion = (key: string) => {
    setForm(f => {
      const convs = { ...(f.unit_conversions || {}) };
      delete convs[key];
      return { ...f, unit_conversions: convs };
    });
  };

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  return (
    <AuthLayout>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <TestTube2 className="h-7 w-7 text-blue-600" />
          Metric Definitions
        </h1>
        <div className="flex items-center gap-3">
          <button onClick={handleRefreshLibrary} disabled={refreshing} className="btn-secondary text-sm flex items-center gap-1">
            <Database className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            {refreshing ? 'Loading...' : 'Refresh from Library'}
          </button>
          <button onClick={handleNormalize} disabled={normalizing} className="btn-secondary text-sm flex items-center gap-1">
            <RefreshCw className={`h-4 w-4 ${normalizing ? 'animate-spin' : ''}`} />
            {normalizing ? 'Normalizing...' : 'Normalize All'}
          </button>
          <button onClick={startCreate} className="btn-primary text-sm flex items-center gap-1">
            <Plus className="h-4 w-4" /> New Definition
          </button>
        </div>
      </div>

      {message && (
        <div className={`mb-4 p-3 rounded-lg text-sm ${message.type === 'success' ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-red-50 text-red-800 border border-red-200'}`}>
          {message.text}
        </div>
      )}

      {/* Unmatched Metrics Panel */}
      {unmatched.length > 0 && (
        <div className="card mb-6 border-l-4 border-amber-400">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle className="h-5 w-5 text-amber-600" />
            <h2 className="text-lg font-semibold text-gray-900">Unmatched Metrics ({unmatched.length})</h2>
          </div>
          <p className="text-sm text-gray-600 mb-4">
            These metrics were imported but don&apos;t match any definition. Map them to an existing definition or create a new one.
          </p>
          <div className="space-y-2">
            {unmatched.map(um => (
              <div key={um.metric_type} className="border border-gray-200 rounded-lg">
                <button
                  onClick={() => setExpandedUnmatched(expandedUnmatched === um.metric_type ? null : um.metric_type)}
                  className="w-full flex items-center justify-between p-3 hover:bg-gray-50"
                >
                  <div className="flex items-center gap-3">
                    <span className="font-medium text-gray-900">{um.metric_type}</span>
                    <span className="text-xs bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full">{um.count} record{um.count !== 1 ? 's' : ''}</span>
                    {um.suggested_match && (
                      <span className="text-xs text-green-700 bg-green-50 px-2 py-0.5 rounded-full">
                        Suggested: {um.suggested_match}
                      </span>
                    )}
                  </div>
                  {expandedUnmatched === um.metric_type ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                </button>
                {expandedUnmatched === um.metric_type && (
                  <div className="px-3 pb-3 pt-1 border-t border-gray-100">
                    <div className="text-sm text-gray-600 mb-2">
                      Latest: {um.latest_value} {um.latest_unit || ''}
                      {um.documents.length > 0 && <span className="ml-2">• From: {um.documents.join(', ')}</span>}
                    </div>
                    <div className="flex items-center gap-2">
                      <select
                        className="text-sm border border-gray-300 rounded px-2 py-1.5 flex-1 max-w-xs"
                        value={mapTarget[um.metric_type] || ''}
                        onChange={e => setMapTarget(prev => ({ ...prev, [um.metric_type]: parseInt(e.target.value) }))}
                      >
                        <option value="">Map to definition...</option>
                        {definitions.map(d => (
                          <option key={d.id} value={d.id}>{d.name} ({d.category || 'uncategorized'})</option>
                        ))}
                      </select>
                      <button
                        onClick={() => handleMapUnmatched(um.metric_type)}
                        disabled={!mapTarget[um.metric_type]}
                        className="btn-primary text-xs py-1.5 flex items-center gap-1"
                      >
                        <Link2 className="h-3 w-3" /> Map
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Create/Edit Form */}
      {(isCreating || editingId !== null) && (
        <div className="card mb-6 border-l-4 border-blue-400">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            {isCreating ? 'New Metric Definition' : `Editing: ${form.name}`}
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Canonical Name *</label>
              <input type="text" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="e.g. Creatinine" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Category</label>
              <input type="text" value={form.category || ''} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="e.g. Kidney Function" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Canonical Unit</label>
              <input type="text" value={form.unit || ''} onChange={e => setForm(f => ({ ...f, unit: e.target.value }))}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="e.g. mg/dL" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Data Type</label>
              <select value={form.data_type} onChange={e => setForm(f => ({ ...f, data_type: e.target.value }))}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm">
                <option value="float">Float</option>
                <option value="int">Integer</option>
                <option value="string">String</option>
                <option value="boolean">Boolean</option>
              </select>
            </div>
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea value={form.description || ''} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" rows={2} placeholder="Optional description" />
          </div>
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Aliases (comma-separated)</label>
            <input type="text" value={aliasInput} onChange={e => setAliasInput(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
              placeholder="e.g. Creatinine, Serum, Crea" />
          </div>

          {/* Reference Ranges */}
          <div className="mb-4">
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium text-gray-700">Reference Ranges</label>
              <button onClick={addReferenceRange} className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1">
                <Plus className="h-3 w-3" /> Add Range
              </button>
            </div>
            {(form.reference_ranges || []).length === 0 && (
              <p className="text-xs text-gray-500">No reference ranges defined.</p>
            )}
            {(form.reference_ranges || []).map((range, idx) => (
              <div key={idx} className="flex items-end gap-2 mb-2 p-2 bg-gray-50 rounded">
                <div className="flex-1">
                  <label className="text-xs text-gray-500">Sex</label>
                  <select value={range.sex || ''} onChange={e => updateReferenceRange(idx, 'sex', e.target.value || null)}
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm">
                    <option value="">Both</option>
                    <option value="male">Male</option>
                    <option value="female">Female</option>
                  </select>
                </div>
                <div className="flex-1">
                  <label className="text-xs text-gray-500">Low</label>
                  <input type="number" step="any" value={range.low ?? ''} onChange={e => updateReferenceRange(idx, 'low', e.target.value ? parseFloat(e.target.value) : null)}
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm" placeholder="e.g. 0.76" />
                </div>
                <div className="flex-1">
                  <label className="text-xs text-gray-500">High</label>
                  <input type="number" step="any" value={range.high ?? ''} onChange={e => updateReferenceRange(idx, 'high', e.target.value ? parseFloat(e.target.value) : null)}
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm" placeholder="e.g. 1.27" />
                </div>
                <div className="flex-1">
                  <label className="text-xs text-gray-500">Operator</label>
                  <select value={range.operator || ''} onChange={e => updateReferenceRange(idx, 'operator', e.target.value || null)}
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm">
                    <option value="">Range</option>
                    <option value="<">&lt;</option>
                    <option value="<=">&le;</option>
                    <option value=">">&gt;</option>
                    <option value=">=">&ge;</option>
                  </select>
                </div>
                <div className="flex-1">
                  <label className="text-xs text-gray-500">Source</label>
                  <input type="text" value={range.source || ''} onChange={e => updateReferenceRange(idx, 'source', e.target.value || null)}
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm" placeholder="e.g. mayo" />
                </div>
                <button onClick={() => removeReferenceRange(idx)} className="text-red-500 hover:text-red-700 p-1">
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>

          {/* Unit Conversions */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">Unit Conversions (source unit → canonical)</label>
            <div className="flex items-end gap-2 mb-2">
              <div className="flex-1">
                <label className="text-xs text-gray-500">Source Unit</label>
                <input type="text" value={unitConvKey} onChange={e => setUnitConvKey(e.target.value)}
                  className="w-full border border-gray-300 rounded px-2 py-1 text-sm" placeholder="e.g. µmol/L" />
              </div>
              <div className="flex-1">
                <label className="text-xs text-gray-500">Multiplier</label>
                <input type="number" step="any" value={unitConvVal} onChange={e => setUnitConvVal(e.target.value)}
                  className="w-full border border-gray-300 rounded px-2 py-1 text-sm" placeholder="e.g. 88.42" />
              </div>
              <button onClick={addUnitConversion} className="text-sm text-blue-600 hover:text-blue-800 px-3 py-1">Add</button>
            </div>
            {Object.entries(form.unit_conversions || {}).map(([unit, factor]) => (
              <div key={unit} className="flex items-center gap-2 text-sm text-gray-700 bg-gray-50 rounded px-3 py-1.5 mb-1">
                <span>{unit} × {factor} = 1 {form.unit}</span>
                <button onClick={() => removeUnitConversion(unit)} className="ml-auto text-red-500 hover:text-red-700">
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <button onClick={handleSave} disabled={saving || !form.name.trim()} className="btn-primary text-sm flex items-center gap-1">
              <Save className="h-4 w-4" /> {saving ? 'Saving...' : 'Save'}
            </button>
            <button onClick={cancelEdit} className="text-sm text-gray-600 hover:text-gray-800 px-3 py-2">Cancel</button>
          </div>
        </div>
      )}

      {/* Definitions Table */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">All Definitions ({definitions.length})</h2>
        </div>
        {definitions.length === 0 ? (
          <p className="text-gray-500 text-center py-8">No metric definitions yet. Create one to start normalizing metrics.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 px-3 font-medium text-gray-700">Name</th>
                  <th className="text-left py-2 px-3 font-medium text-gray-700">Category</th>
                  <th className="text-left py-2 px-3 font-medium text-gray-700">Unit</th>
                  <th className="text-left py-2 px-3 font-medium text-gray-700">Aliases</th>
                  <th className="text-left py-2 px-3 font-medium text-gray-700">Ranges</th>
                  <th className="text-right py-2 px-3 font-medium text-gray-700">Actions</th>
                </tr>
              </thead>
              <tbody>
                {definitions.map(def => (
                  <tr key={def.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-2 px-3 font-medium text-gray-900">{def.name}</td>
                    <td className="py-2 px-3 text-gray-600">{def.category || '-'}</td>
                    <td className="py-2 px-3 text-gray-600">{def.unit || '-'}</td>
                    <td className="py-2 px-3 text-gray-500 text-xs max-w-xs truncate">
                      {def.aliases.length > 0 ? def.aliases.join(', ') : '-'}
                    </td>
                    <td className="py-2 px-3 text-gray-500 text-xs">
                      {def.reference_ranges.length > 0 ? `${def.reference_ranges.length} range${def.reference_ranges.length !== 1 ? 's' : ''}` : '-'}
                    </td>
                    <td className="py-2 px-3 text-right">
                      <button onClick={() => startEdit(def)} className="text-blue-600 hover:text-blue-800 p-1 mr-1">
                        <Edit3 className="h-4 w-4" />
                      </button>
                      <button onClick={() => handleDelete(def)} className="text-red-500 hover:text-red-700 p-1">
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AuthLayout>
  );
}
