'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { useAuthStore } from '@/store/auth-store';
import {
  User, Lock, Ruler, Check, AlertCircle, Search, Loader2,
  Globe, Scale, RotateCcw,
} from 'lucide-react';
import apiClient from '@/lib/api-client';
import { notifyUnitPreferencesChanged, type UnitSystem } from '@/hooks/useUnitConversion';

interface MetricUnitView {
  id: number;
  name: string;
  category: string | null;
  canonical_unit: string | null;
  available_units: string[];
  preferred_unit: string | null;
  system_unit: string | null;
  effective_unit: string | null;
}

interface UnitsOverview {
  unit_system: UnitSystem;
  metrics: MetricUnitView[];
}

const SYSTEM_CHOICES: { value: UnitSystem; label: string; hint: string }[] = [
  {
    value: null,
    label: 'Per metric',
    hint: 'Each metric uses its stored default unless you override it below.',
  },
  {
    value: 'metric',
    label: 'Metric',
    hint: 'Every metric switches to its metric unit (kg, km, °C, …).',
  },
  {
    value: 'imperial',
    label: 'Imperial',
    hint: 'Every metric switches to its imperial unit (lb, miles, °F, …).',
  },
];

export default function ProfilePage() {
  const [activeTab, setActiveTab] = useState<'profile' | 'password' | 'units'>('profile');
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);

  // ── Profile tab state ──
  const [email, setEmail] = useState('');
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileMessage, setProfileMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // ── Password tab state ──
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // ── Units tab state ──
  const [unitSystem, setUnitSystem] = useState<UnitSystem>(null);
  const [metrics, setMetrics] = useState<MetricUnitView[]>([]);
  const [unitsLoading, setUnitsLoading] = useState(true);
  const [savingSystem, setSavingSystem] = useState(false);
  const [savingMetricId, setSavingMetricId] = useState<number | null>(null);
  const [clearing, setClearing] = useState(false);
  const [unitMessage, setUnitMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [metricFilter, setMetricFilter] = useState('');
  const [showAllMetrics, setShowAllMetrics] = useState(false);

  // Load current user profile
  useEffect(() => {
    if (user) setEmail(user.email || '');
  }, [user]);

  const loadUnits = useCallback(async () => {
    try {
      const res = await apiClient.get('/users/me/units');
      setMetrics(res.data?.metrics || []);
      setUnitSystem(res.data?.unit_system ?? null);
    } catch (err) {
      console.error('Failed to load unit settings:', err);
      setUnitMessage({ type: 'error', text: 'Failed to load unit settings' });
    } finally {
      setUnitsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'units') loadUnits();
  }, [activeTab, loadUnits]);

  // ── Profile handlers ──
  const handleProfileSave = async () => {
    setProfileSaving(true);
    setProfileMessage(null);
    try {
      const res = await apiClient.put('/users/me', { email: email || null });
      setUser({ ...user!, email: res.data.email });
      setProfileMessage({ type: 'success', text: 'Profile updated successfully' });
    } catch (err: any) {
      setProfileMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to update profile' });
    } finally {
      setProfileSaving(false);
    }
  };

  // ── Password handlers ──
  const handlePasswordChange = async () => {
    if (newPassword !== confirmPassword) {
      setPasswordMessage({ type: 'error', text: 'New passwords do not match' });
      return;
    }
    if (newPassword.length < 6) {
      setPasswordMessage({ type: 'error', text: 'New password must be at least 6 characters' });
      return;
    }
    setPasswordSaving(true);
    setPasswordMessage(null);
    try {
      await apiClient.put('/users/me/password', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setPasswordMessage({ type: 'success', text: 'Password changed successfully' });
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err: any) {
      setPasswordMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to change password' });
    } finally {
      setPasswordSaving(false);
    }
  };

  // ── Units tab handlers ──
  const handleSystemChange = async (system: UnitSystem) => {
    if (system === unitSystem) return;
    setSavingSystem(true);
    setUnitMessage(null);
    try {
      await apiClient.put('/users/me/unit-system', { unit_system: system });
      setUnitSystem(system);
      // system_unit / effective_unit are resolved per system, so reload.
      await loadUnits();
      notifyUnitPreferencesChanged();
      setUnitMessage({
        type: 'success',
        text: system ? `Measurement system set to ${system}` : 'Reverted to per-metric units',
      });
    } catch (err: any) {
      setUnitMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to save measurement system' });
    } finally {
      setSavingSystem(false);
    }
  };

  const handleSelectUnit = async (metric: MetricUnitView, unit: string) => {
    setSavingMetricId(metric.id);
    setUnitMessage(null);
    try {
      if (unit) {
        await apiClient.put('/users/me/unit-preferences', {
          metric_definition_id: metric.id,
          preferred_unit: unit,
        });
      } else {
        await apiClient.delete(`/users/me/unit-preferences/${metric.id}`);
      }
      await loadUnits();
      notifyUnitPreferencesChanged();
      setUnitMessage({
        type: 'success',
        text: unit
          ? `${metric.name} will now display in ${unit}`
          : `${metric.name} reverted to the ${unitSystem ? unitSystem : 'default'} setting`,
      });
    } catch (err: any) {
      setUnitMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to save preference' });
    } finally {
      setSavingMetricId(null);
    }
  };

  const handleClearOverrides = async () => {
    setClearing(true);
    setUnitMessage(null);
    try {
      const res = await apiClient.delete('/users/me/unit-preferences');
      await loadUnits();
      notifyUnitPreferencesChanged();
      setUnitMessage({ type: 'success', text: res.data?.message || 'Per-metric overrides cleared' });
    } catch (err: any) {
      setUnitMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to clear overrides' });
    } finally {
      setClearing(false);
    }
  };

  const overrideCount = useMemo(
    () => metrics.filter((m) => m.preferred_unit).length,
    [metrics]
  );
  const affectedBySystem = useMemo(
    () => metrics.filter((m) => m.system_unit && m.system_unit !== m.canonical_unit),
    [metrics]
  );
  const visibleMetrics = useMemo(() => {
    const q = metricFilter.trim().toLowerCase();
    return metrics.filter((m) => {
      if (!showAllMetrics && m.available_units.length < 2) return false;
      if (!q) return true;
      return m.name.toLowerCase().includes(q) || (m.category || '').toLowerCase().includes(q);
    });
  }, [metrics, metricFilter, showAllMetrics]);

  const tabs = [
    { key: 'profile' as const, label: 'Profile', icon: User },
    { key: 'password' as const, label: 'Password', icon: Lock },
    { key: 'units' as const, label: 'Units', icon: Ruler },
  ];

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Profile</h1>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-lg p-1 mb-6 w-fit">
        {tabs.map((tab) => (
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

      {/* ── Profile Tab ── */}
      {activeTab === 'profile' && (
        <div className="card max-w-lg">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Profile Information</h2>

          {profileMessage && (
            <div className={`flex items-center gap-2 px-4 py-3 rounded-lg mb-4 text-sm ${
              profileMessage.type === 'success'
                ? 'bg-green-50 border border-green-200 text-green-700'
                : 'bg-red-50 border border-red-200 text-red-700'
            }`}>
              {profileMessage.type === 'success' ? <Check className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
              {profileMessage.text}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
              <input
                type="text"
                value={user?.username || ''}
                disabled
                className="input bg-gray-50 cursor-not-allowed"
              />
              <p className="text-xs text-gray-500 mt-1">Username cannot be changed</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="input-field"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
              <input
                type="text"
                value={user?.role || ''}
                disabled
                className="input bg-gray-50 cursor-not-allowed capitalize"
              />
            </div>

            <button
              onClick={handleProfileSave}
              disabled={profileSaving}
              className="btn-primary"
            >
              {profileSaving ? (
                <><Loader2 className="h-4 w-4 animate-spin" /> Saving...</>
              ) : (
                'Save Changes'
              )}
            </button>
          </div>
        </div>
      )}

      {/* ── Password Tab ── */}
      {activeTab === 'password' && (
        <div className="card max-w-lg">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Change Password</h2>

          {passwordMessage && (
            <div className={`flex items-center gap-2 px-4 py-3 rounded-lg mb-4 text-sm ${
              passwordMessage.type === 'success'
                ? 'bg-green-50 border border-green-200 text-green-700'
                : 'bg-red-50 border border-red-200 text-red-700'
            }`}>
              {passwordMessage.type === 'success' ? <Check className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
              {passwordMessage.text}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Current Password</label>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="input-field"
                autoComplete="current-password"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="input-field"
                autoComplete="new-password"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Confirm New Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="input-field"
                autoComplete="new-password"
              />
            </div>

            <button
              onClick={handlePasswordChange}
              disabled={passwordSaving || !currentPassword || !newPassword || !confirmPassword}
              className="btn-primary"
            >
              {passwordSaving ? (
                <><Loader2 className="h-4 w-4 animate-spin" /> Changing...</>
              ) : (
                'Change Password'
              )}
            </button>
          </div>
        </div>
      )}

      {/* ── Units Tab ── */}
      {activeTab === 'units' && (
        <div className="max-w-3xl space-y-6">
          {/* Status message */}
          {unitMessage && (
            <div className={`flex items-center gap-2 px-4 py-3 rounded-lg text-sm ${
              unitMessage.type === 'success'
                ? 'bg-green-50 border border-green-200 text-green-700'
                : 'bg-red-50 border border-red-200 text-red-700'
            }`}>
              {unitMessage.type === 'success' ? <Check className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
              {unitMessage.text}
            </div>
          )}

          {/* Global measurement system */}
          <div className="card">
            <div className="flex items-center gap-2 mb-1">
              <Globe className="h-5 w-5 text-gray-600" />
              <h2 className="text-lg font-semibold text-gray-900">Measurement System</h2>
            </div>
            <p className="text-sm text-gray-600 mb-4">
              Switch every metric at once. Anything you override per metric below keeps its own unit.
            </p>

            <div className="grid gap-3 sm:grid-cols-3">
              {SYSTEM_CHOICES.map((choice) => {
                const isSelected = choice.value === unitSystem;
                return (
                  <label
                    key={choice.label}
                    className={`flex items-start gap-3 px-4 py-3 rounded-lg border cursor-pointer transition-colors ${
                      isSelected
                        ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-500'
                        : 'border-gray-200 bg-white hover:border-gray-300'
                    } ${savingSystem ? 'opacity-60' : ''}`}
                  >
                    <input
                      type="radio"
                      name="unit-system"
                      checked={isSelected}
                      onChange={() => handleSystemChange(choice.value)}
                      disabled={savingSystem || unitsLoading}
                      className="mt-0.5 h-4 w-4 text-blue-600 border-gray-300 focus:ring-blue-500"
                    />
                    <span>
                      <span className="block font-medium text-gray-900 capitalize">{choice.label}</span>
                      <span className="block text-xs text-gray-600 mt-0.5">{choice.hint}</span>
                    </span>
                  </label>
                );
              })}
            </div>

            {savingSystem && (
              <p className="flex items-center gap-2 text-sm text-gray-500 mt-3">
                <Loader2 className="h-4 w-4 animate-spin" /> Saving measurement system…
              </p>
            )}

            {unitSystem && !unitsLoading && (
              <div className="mt-4 rounded-lg bg-gray-50 border border-gray-200 px-4 py-3 text-sm text-gray-700">
                {affectedBySystem.length > 0 ? (
                  <>
                    <span className="font-medium">
                      Changed to {SYSTEM_CHOICES.find((c) => c.value === unitSystem)?.label.toLowerCase()}:
                    </span>{' '}
                    {(() => {
                      const changed = affectedBySystem
                        .filter((m) => !m.preferred_unit)
                        .map((m) => `${m.name} → ${m.system_unit}`);
                      return changed.length > 0
                        ? changed.join(', ')
                        : 'none — every affected metric has a per-metric override';
                    })()}
                  </>
                ) : (
                  <>No {unitSystem} units are defined for any metric in your library.</>
                )}
              </div>
            )}
          </div>

          {/* Per-metric overrides */}
          <div className="card">
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-2">
                <Scale className="h-5 w-5 text-gray-600" />
                <h2 className="text-lg font-semibold text-gray-900">Per-Metric Units</h2>
              </div>
              {overrideCount > 0 && (
                <button
                  onClick={handleClearOverrides}
                  disabled={clearing}
                  className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900 disabled:opacity-50"
                >
                  {clearing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                  Clear {overrideCount} override{overrideCount === 1 ? '' : 's'}
                </button>
              )}
            </div>
            <p className="text-sm text-gray-600 mb-4">
              {overrideCount > 0
                ? `${overrideCount} metric${overrideCount === 1 ? '' : 's'} pinned to a specific unit. Everything else follows the measurement system above.`
                : 'No overrides. Every metric follows the measurement system above, or its stored default.'}
            </p>

            <div className="relative mb-3">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
              <input
                type="text"
                value={metricFilter}
                onChange={(e) => setMetricFilter(e.target.value)}
                placeholder="Filter metrics (e.g. weight, distance)…"
                className="input-field pl-10"
              />
            </div>

            <label className="flex items-center gap-2 text-sm text-gray-600 mb-3">
              <input
                type="checkbox"
                checked={showAllMetrics}
                onChange={(e) => setShowAllMetrics(e.target.checked)}
                className="h-4 w-4 text-blue-600 border-gray-300 focus:ring-blue-500"
              />
              Show metrics with only one unit
            </label>

            {unitsLoading ? (
              <p className="flex items-center gap-2 text-sm text-gray-500 py-6 justify-center">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading units…
              </p>
            ) : visibleMetrics.length === 0 ? (
              <p className="text-sm text-gray-500 py-6 text-center">
                No metrics match “{metricFilter}”.
              </p>
            ) : (
              <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
                {visibleMetrics.map((metric) => {
                  const isSaving = savingMetricId === metric.id;
                  const inherited = metric.system_unit || metric.canonical_unit || '—';
                  return (
                    <div
                      key={metric.id}
                      className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3"
                    >
                      <div className="flex-1 min-w-[180px]">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-gray-900">{metric.name}</span>
                          {metric.category && (
                            <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
                              {metric.category}
                            </span>
                          )}
                          {metric.preferred_unit && (
                            <span className="text-xs font-medium text-blue-700 bg-blue-50 border border-blue-200 px-2 py-0.5 rounded">
                              Override
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-gray-500 mt-0.5">
                          Showing <strong className="text-gray-700">{metric.effective_unit || '—'}</strong>
                          {metric.canonical_unit ? ` · default ${metric.canonical_unit}` : ''}
                        </p>
                      </div>

                      <div className="flex items-center gap-2">
                        <select
                          value={metric.preferred_unit || ''}
                          onChange={(e) => handleSelectUnit(metric, e.target.value)}
                          disabled={isSaving}
                          className="input-field py-2 text-sm w-auto min-w-[160px] disabled:opacity-50"
                        >
                          <option value="">
                            {metric.system_unit
                              ? `${unitSystem === 'metric' ? 'Metric' : 'Imperial'} default (${metric.system_unit})`
                              : `Default (${inherited})`}
                          </option>
                          {metric.available_units.map((unit) => (
                            <option key={unit} value={unit}>
                              {unit}{unit === metric.canonical_unit ? ' (stored default)' : ''}
                            </option>
                          ))}
                        </select>
                        {isSaving && <Loader2 className="h-4 w-4 animate-spin text-gray-400" />}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </AuthLayout>
  );
}
