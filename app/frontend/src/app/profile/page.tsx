'use client';

import { useState, useEffect, useCallback } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { useAuthStore } from '@/store/auth-store';
import {
  User, Lock, Ruler, Check, AlertCircle, Search, Loader2,
  ChevronRight, X,
} from 'lucide-react';
import apiClient from '@/lib/api-client';

interface MetricSearchResult {
  id: number;
  name: string;
  category: string | null;
  canonical_unit: string | null;
  available_units: string[];
  preferred_unit: string | null;
}

interface UnitPreference {
  id: number;
  metric_definition_id: number;
  metric_name: string;
  canonical_unit: string | null;
  preferred_unit: string;
}

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
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<MetricSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [selectedMetric, setSelectedMetric] = useState<MetricSearchResult | null>(null);
  const [savingUnit, setSavingUnit] = useState(false);
  const [unitMessage, setUnitMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [currentPrefs, setCurrentPrefs] = useState<UnitPreference[]>([]);

  // Load current user profile and preferences
  useEffect(() => {
    if (user) setEmail(user.email || '');
    loadPreferences();
  }, [user]);

  const loadPreferences = async () => {
    try {
      const res = await apiClient.get('/users/me/unit-preferences');
      setCurrentPrefs(res.data || []);
    } catch (err) {
      console.error('Failed to load unit preferences:', err);
    }
  };

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
  const searchMetrics = useCallback(async (query: string) => {
    setSearching(true);
    try {
      const res = await apiClient.get('/users/me/unit-preferences/search', {
        params: { q: query },
      });
      setSearchResults(res.data || []);
    } catch (err) {
      console.error('Failed to search metrics:', err);
    } finally {
      setSearching(false);
    }
  }, []);

  // Debounced search
  useEffect(() => {
    if (activeTab !== 'units') return;
    const timer = setTimeout(() => searchMetrics(searchQuery), 300);
    return () => clearTimeout(timer);
  }, [searchQuery, activeTab, searchMetrics]);

  const handleSelectUnit = async (metric: MetricSearchResult, unit: string) => {
    setSavingUnit(true);
    setUnitMessage(null);
    try {
      await apiClient.put('/users/me/unit-preferences', {
        metric_definition_id: metric.id,
        preferred_unit: unit,
      });
      // Update local state
      setSearchResults((prev) =>
        prev.map((r) => (r.id === metric.id ? { ...r, preferred_unit: unit } : r))
      );
      if (selectedMetric?.id === metric.id) {
        setSelectedMetric({ ...metric, preferred_unit: unit });
      }
      setUnitMessage({ type: 'success', text: `${metric.name} will now display in ${unit}` });
      loadPreferences();
    } catch (err: any) {
      setUnitMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to save preference' });
    } finally {
      setSavingUnit(false);
    }
  };

  const handleRemovePreference = async (metricDefinitionId: number) => {
    try {
      await apiClient.delete(`/users/me/unit-preferences/${metricDefinitionId}`);
      setUnitMessage({ type: 'success', text: 'Preference removed — reverted to default unit' });
      setSearchResults((prev) =>
        prev.map((r) => (r.id === metricDefinitionId ? { ...r, preferred_unit: null } : r))
      );
      loadPreferences();
    } catch (err) {
      console.error('Failed to remove preference:', err);
    }
  };

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
                className="input"
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
              className="btn btn-primary"
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
                className="input"
                autoComplete="current-password"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">New Password</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="input"
                autoComplete="new-password"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Confirm New Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="input"
                autoComplete="new-password"
              />
            </div>

            <button
              onClick={handlePasswordChange}
              disabled={passwordSaving || !currentPassword || !newPassword || !confirmPassword}
              className="btn btn-primary"
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
        <div className="max-w-3xl">
          {/* Status message */}
          {unitMessage && (
            <div className={`flex items-center gap-2 px-4 py-3 rounded-lg mb-4 text-sm ${
              unitMessage.type === 'success'
                ? 'bg-green-50 border border-green-200 text-green-700'
                : 'bg-red-50 border border-red-200 text-red-700'
            }`}>
              {unitMessage.type === 'success' ? <Check className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
              {unitMessage.text}
            </div>
          )}

          {/* Current preferences summary */}
          {currentPrefs.length > 0 && (
            <div className="card mb-6">
              <h3 className="text-sm font-semibold text-gray-900 mb-3">Your Current Preferences</h3>
              <div className="flex flex-wrap gap-2">
                {currentPrefs.map((pref) => (
                  <span
                    key={pref.id}
                    className="inline-flex items-center gap-1.5 bg-blue-50 text-blue-700 px-3 py-1.5 rounded-full text-sm"
                  >
                    {pref.metric_name}: <strong>{pref.preferred_unit}</strong>
                    <button
                      onClick={() => handleRemovePreference(pref.metric_definition_id)}
                      className="ml-1 hover:text-blue-900"
                      title="Remove preference"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Search */}
          <div className="card">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Unit of Measure Preferences</h3>
            <p className="text-sm text-gray-600 mb-4">
              Search for a metric to configure which unit it displays in. For example, search &quot;weight&quot; to change from kg to lbs.
            </p>

            <div className="relative mb-4">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  setSelectedMetric(null);
                }}
                placeholder="Search for a metric (e.g. weight, glucose, height)..."
                className="input pl-10"
              />
              {searching && (
                <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 animate-spin text-gray-400" />
              )}
            </div>

            {/* Search Results */}
            {!selectedMetric && searchResults.length > 0 && (
              <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
                {searchResults.map((metric) => (
                  <button
                    key={metric.id}
                    onClick={() => setSelectedMetric(metric)}
                    className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition-colors text-left"
                  >
                    <div>
                      <span className="font-medium text-gray-900">{metric.name}</span>
                      {metric.category && (
                        <span className="ml-2 text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
                          {metric.category}
                        </span>
                      )}
                      <div className="text-sm text-gray-500 mt-0.5">
                        Default: {metric.canonical_unit || 'N/A'}
                        {metric.preferred_unit && metric.preferred_unit !== metric.canonical_unit && (
                          <span className="ml-2 text-blue-600 font-medium">
                            → Your preference: {metric.preferred_unit}
                          </span>
                        )}
                      </div>
                    </div>
                    <ChevronRight className="h-4 w-4 text-gray-400" />
                  </button>
                ))}
              </div>
            )}

            {!selectedMetric && searchQuery && !searching && searchResults.length === 0 && (
              <p className="text-sm text-gray-500 py-4 text-center">
                No metrics found matching &quot;{searchQuery}&quot;
              </p>
            )}

            {/* Unit Selection for Selected Metric */}
            {selectedMetric && (
              <div className="border border-blue-200 bg-blue-50/50 rounded-lg p-4">
                <div className="flex items-center justify-between mb-3">
                  <h4 className="font-semibold text-gray-900">{selectedMetric.name}</h4>
                  <button
                    onClick={() => setSelectedMetric(null)}
                    className="text-sm text-gray-500 hover:text-gray-700"
                  >
                    Back to results
                  </button>
                </div>

                {selectedMetric.category && (
                  <p className="text-sm text-gray-600 mb-3">Category: {selectedMetric.category}</p>
                )}

                <p className="text-sm text-gray-600 mb-3">
                  Select your preferred display unit:
                </p>

                <div className="space-y-2">
                  {selectedMetric.available_units.map((unit) => {
                    const isDefault = unit === selectedMetric.canonical_unit;
                    const isSelected = selectedMetric.preferred_unit
                      ? unit === selectedMetric.preferred_unit
                      : isDefault;

                    return (
                      <label
                        key={unit}
                        className={`flex items-center gap-3 px-4 py-3 rounded-lg border cursor-pointer transition-colors ${
                          isSelected
                            ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-500'
                            : 'border-gray-200 bg-white hover:border-gray-300'
                        }`}
                      >
                        <input
                          type="radio"
                          name={`unit-${selectedMetric.id}`}
                          checked={isSelected}
                          onChange={() => handleSelectUnit(selectedMetric, unit)}
                          disabled={savingUnit}
                          className="h-4 w-4 text-blue-600 border-gray-300 focus:ring-blue-500"
                        />
                        <div className="flex-1">
                          <span className={`font-medium ${isSelected ? 'text-blue-900' : 'text-gray-900'}`}>
                            {unit}
                          </span>
                          {isDefault && (
                            <span className="ml-2 text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
                              System Default
                            </span>
                          )}
                        </div>
                        {isSelected && (
                          <Check className="h-5 w-5 text-blue-600" />
                        )}
                        {savingUnit && isSelected && (
                          <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                        )}
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </AuthLayout>
  );
}
