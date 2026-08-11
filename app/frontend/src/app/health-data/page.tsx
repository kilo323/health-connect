'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Plus, Trash2, Download, Search, Activity, Filter } from 'lucide-react';
import apiClient from '@/lib/api-client';
import { useUnitConversion } from '@/hooks/useUnitConversion';

interface HealthMetric {
  id: number;
  metric_type: string;
  value: number;
  unit: string;
  recorded_at: string;
  source: string;
  source_document?: string | null;
}

const METRIC_TYPES = [
  'blood_pressure', 'heart_rate', 'weight', 'height', 'glucose',
  'cholesterol', 'temperature', 'spo2', 'sleep_hours', 'steps'
];

export default function HealthDataPage() {
  const [metrics, setMetrics] = useState<HealthMetric[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [showAddForm, setShowAddForm] = useState(false);
  const [filterYear, setFilterYear] = useState<string>('all');
  const [filterMetricType, setFilterMetricType] = useState<string>('all');
  const [availableTypes, setAvailableTypes] = useState<string[]>([]);
  const { convert, formatValue: formatMetricDisplay, loaded: unitsLoaded } = useUnitConversion();

  // Convert a metric value to the user's preferred display unit
  function formatValue(metric: HealthMetric): string {
    if (!unitsLoaded) return metric.value.toString();
    const c = convert(metric.metric_type, metric.value, metric.unit);
    return formatMetricDisplay(c.value, c.unit);
  }

  function formatUnit(metric: HealthMetric): string {
    if (!unitsLoaded) return metric.unit;
    return convert(metric.metric_type, metric.value, metric.unit).unit;
  }
  
  // Add form state
  const [metricType, setMetricType] = useState(METRIC_TYPES[0]);
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState('mmHg');
  const [source, setSource] = useState('manual');

  // Generate year options (current year back to 2020)
  const currentYear = new Date().getFullYear();
  const yearOptions = Array.from({ length: currentYear - 2019 }, (_, i) => currentYear - i);

  useEffect(() => {
    loadMetrics();
  }, [filterYear]);

  const loadMetrics = async () => {
    try {
      const params: Record<string, string | number> = { limit: 500 };
      if (filterYear !== 'all') params.year = parseInt(filterYear);
      const res = await apiClient.get('/health/metrics', { params });
      const data = res.data || [];
      setMetrics(data);
      // Extract unique metric types for filter dropdown
      const types = [...new Set(data.map((m: HealthMetric) => m.metric_type))].sort();
      setAvailableTypes(types as string[]);
    } catch (error) {
      console.error('Failed to load metrics:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAddMetric = async () => {
    if (!value) return;
    
    try {
      await apiClient.post('/health/metrics', {
        metric_type: metricType,
        value: parseFloat(value),
        unit,
        source,
      });
      setShowAddForm(false);
      setValue('');
      loadMetrics();
    } catch (error) {
      console.error('Failed to add metric:', error);
    }
  };

  const handleDeleteMetric = async (id: number) => {
    if (!confirm('Are you sure?')) return;
    
    try {
      await apiClient.delete(`/health/metrics/${id}`);
      loadMetrics();
    } catch (error) {
      console.error('Failed to delete metric:', error);
    }
  };

  const handleExport = async () => {
    try {
      const res = await apiClient.get('/health/metrics/export', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `health-metrics-${new Date().toISOString().split('T')[0]}.csv`;
      a.click();
    } catch (error) {
      console.error('Failed to export:', error);
    }
  };

  const filteredMetrics = metrics.filter(m => {
    const matchesSearch = m.metric_type.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.unit.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesType = filterMetricType === 'all' || m.metric_type === filterMetricType;
    return matchesSearch && matchesType;
  });

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  return (
    <AuthLayout>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Health Data</h1>
        <button onClick={() => setShowAddForm(true)} className="btn-primary flex items-center gap-2">
          <Plus className="h-4 w-4" /> Add Metric
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 mb-6 flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search metrics..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input-field pl-10"
          />
        </div>
        <select
          value={filterYear}
          onChange={(e) => setFilterYear(e.target.value)}
          className="input-field w-auto"
        >
          <option value="all">All Years</option>
          {yearOptions.map(y => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
        <select
          value={filterMetricType}
          onChange={(e) => setFilterMetricType(e.target.value)}
          className="input-field w-auto"
        >
          <option value="all">All Metrics</option>
          {availableTypes.map(t => (
            <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
          ))}
        </select>
        <button onClick={handleExport} className="btn-secondary flex items-center gap-2">
          <Download className="h-4 w-4" /> Export CSV
        </button>
      </div>

      {/* Add Metric Modal */}
      {showAddForm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-full max-w-md mx-4">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">Add Health Metric</h2>
            
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Metric Type</label>
                <select
                  value={metricType}
                  onChange={(e) => setMetricType(e.target.value)}
                  className="input-field"
                >
                  {METRIC_TYPES.map(type => (
                    <option key={type} value={type}>{type.replace(/_/g, ' ')}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Value</label>
                <input
                  type="number"
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  step="0.01"
                  className="input-field"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Unit</label>
                <input
                  type="text"
                  value={unit}
                  onChange={(e) => setUnit(e.target.value)}
                  placeholder="e.g., mmHg, kg, bpm"
                  className="input-field"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Source</label>
                <select
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  className="input-field"
                >
                  <option value="manual">Manual Entry</option>
                  <option value="google_health_connect">Google Health Connect</option>
                  <option value="nextcloud">Nextcloud Upload</option>
                </select>
              </div>

              <div className="flex gap-3 pt-2">
                <button onClick={handleAddMetric} className="btn-primary flex-1">Save</button>
                <button onClick={() => setShowAddForm(false)} className="btn-secondary flex-1">Cancel</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Metrics Table */}
      {filteredMetrics.length === 0 ? (
        <div className="card text-center py-12">
          <Activity className="h-12 w-12 text-gray-300 mx-auto mb-4" />
          <p className="text-gray-500">No health metrics recorded yet.</p>
          <button onClick={() => setShowAddForm(true)} className="btn-primary mt-4">
            Add Your First Metric
          </button>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100">
                <th className="text-left py-3 px-4 font-medium text-gray-500">Metric</th>
                <th className="text-left py-3 px-4 font-medium text-gray-500">Value</th>
                <th className="text-left py-3 px-4 font-medium text-gray-500">Unit</th>
                <th className="text-left py-3 px-4 font-medium text-gray-500">Source</th>
                <th className="text-left py-3 px-4 font-medium text-gray-500">Recorded At</th>
                <th className="text-right py-3 px-4 font-medium text-gray-500">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredMetrics.map(metric => (
                <tr key={metric.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="py-3 px-4 capitalize text-gray-900">{metric.metric_type.replace(/_/g, ' ')}</td>
                  <td className="py-3 px-4 font-medium text-gray-900">{formatValue(metric)}</td>
                  <td className="py-3 px-4 text-gray-600">{formatUnit(metric)}</td>
                  <td className="py-3 px-4">
                    <span
                      className={`px-2 py-1 rounded-full text-xs font-medium ${
                        metric.source === 'manual' ? 'bg-blue-50 text-blue-700' :
                        metric.source === 'google_fit' ? 'bg-green-50 text-green-700' :
                        'bg-purple-50 text-purple-700'
                      }`}
                      title={metric.source_document ? `Imported from: ${metric.source_document}` : undefined}
                    >
                      {metric.source.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td className="py-3 px-4 text-gray-600">
                    {new Date(metric.recorded_at).toLocaleDateString()}
                  </td>
                  <td className="py-3 px-4 text-right">
                    <button
                      onClick={() => handleDeleteMetric(metric.id)}
                      className="text-red-500 hover:text-red-700 p-1"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AuthLayout>
  );
}
