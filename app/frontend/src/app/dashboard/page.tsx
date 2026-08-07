'use client';

import { useEffect, useState } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Activity, FileText, Stethoscope, TrendingUp } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface Stats {
  totalMetrics: number;
  pendingDocuments: number;
  pendingAnalysis: number;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats>({
    totalMetrics: 0,
    pendingDocuments: 0,
    pendingAnalysis: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadStats();
  }, []);

  const loadStats = async () => {
    try {
      const [documentsRes, analysisRes] = await Promise.allSettled([
        apiClient.get('/health/documents'),
        apiClient.get('/health/metrics/weight'),
      ]);

      const pendingDocs = documentsRes.status === 'fulfilled'
        ? (documentsRes.value.data as any[])?.filter((d: any) => d.status === 'pending').length || 0
        : 0;
      const totalMetrics = analysisRes.status === 'fulfilled'
        ? (analysisRes.value.data as any[])?.length || 0
        : 0;

      setStats({
        totalMetrics,
        pendingDocuments: pendingDocs,
        pendingAnalysis: 0,
      });
    } catch (error) {
      console.error('Failed to load stats:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center h-full">Loading...</div>;
  }

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="card">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-500 mb-1">Total Health Metrics</p>
              <p className="text-3xl font-bold text-gray-900">{stats.totalMetrics}</p>
            </div>
            <Activity className="h-8 w-8 text-blue-600" />
          </div>
        </div>

        <div className="card">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-500 mb-1">Pending Documents</p>
              <p className="text-3xl font-bold text-gray-900">{stats.pendingDocuments}</p>
            </div>
            <FileText className="h-8 w-8 text-yellow-600" />
          </div>
        </div>

        <div className="card">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-500 mb-1">Pending Analysis</p>
              <p className="text-3xl font-bold text-gray-900">{stats.pendingAnalysis}</p>
            </div>
            <Stethoscope className="h-8 w-8 text-purple-600" />
          </div>
        </div>
      </div>

      <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Recent Activity</h2>
          <p className="text-gray-500 text-sm">No recent activity to display.</p>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Quick Setup</h2>
          <ul className="space-y-3 text-sm text-gray-600">
            <li className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
              Connect Google Health Connect
            </li>
            <li className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 bg-yellow-500 rounded-full"></span>
              Configure Nextcloud integration
            </li>
          </ul>
        </div>
      </div>
    </AuthLayout>
  );
}
