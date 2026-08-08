'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Users, Bot, Database, Activity, TrendingUp, AlertCircle } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface AdminStats {
  totalUsers: number;
  activeUsers: number;
  pendingDocuments: number;
  schedulerStatus: string;
}

export default function AdminDashboardPage() {
  const [stats, setStats] = useState<AdminStats>({
    totalUsers: 0,
    activeUsers: 0,
    pendingDocuments: 0,
    schedulerStatus: 'unknown',
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadStats();
  }, []);

  const loadStats = async () => {
    try {
      const [usersRes, docsRes, schedulerRes] = await Promise.all([
        apiClient.get('/admin/users').catch(() => ({ data: [] })),
        apiClient.get('/health/documents?status=pending').catch(() => ({ data: [] })),
        apiClient.get('/admin/status/scheduler').catch(() => ({ data: { is_running: false } })),
      ]);

      setStats({
        totalUsers: usersRes.data?.length || 0,
        activeUsers: usersRes.data?.filter((u: any) => u.is_active).length || 0,
        pendingDocuments: docsRes.data?.length || 0,
        schedulerStatus: schedulerRes.data?.is_running ? 'running' : 'stopped',
      });
    } catch (error) {
      console.error('Failed to load admin stats:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  const adminCards = [
    { title: 'Total Users', value: stats.totalUsers, icon: Users, color: 'text-blue-600', href: '/admin/users' },
    { title: 'Active Users', value: stats.activeUsers, icon: TrendingUp, color: 'text-green-600', href: '/admin/users' },
    { title: 'Pending Documents', value: stats.pendingDocuments, icon: Activity, color: 'text-yellow-600', href: '/documents' },
    { 
      title: 'Scheduler Status', 
      value: stats.schedulerStatus === 'running' ? 'Running' : 'Stopped', 
      icon: Database, 
      color: stats.schedulerStatus === 'running' ? 'text-green-600' : 'text-gray-500',
      href: '/admin/schedule'
    },
  ];

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Admin Dashboard</h1>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        {adminCards.map((card) => (
          <a key={card.title} href={card.href} className="card hover:shadow-md transition-shadow cursor-pointer">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-gray-500">{card.title}</span>
              <card.icon className={`h-6 w-6 ${card.color}`} />
            </div>
            <p className={`text-2xl font-bold ${card.color}`}>{card.value}</p>
          </a>
        ))}
      </div>

      {/* Quick Links */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">User Management</h2>
          <p className="text-sm text-gray-600 mb-4">Manage user accounts, roles, and permissions.</p>
          <a href="/admin/users" className="btn-primary inline-block text-center">View Users</a>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">LLM Configuration</h2>
          <p className="text-sm text-gray-600 mb-4">Configure AI model settings for document analysis.</p>
          <a href="/admin/llm-config" className="btn-primary inline-block text-center">Configure LLM</a>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Schedule Settings</h2>
          <p className="text-sm text-gray-600 mb-4">Manage automated sync schedules and cron jobs.</p>
          <a href="/admin/schedule" className="btn-primary inline-block text-center">Configure Schedule</a>
        </div>
      </div>

      {/* Info Banner */}
      <div className="mt-6 bg-blue-50 border border-blue-200 rounded-lg p-4 flex items-start gap-3">
        <AlertCircle className="h-5 w-5 text-blue-600 mt-0.5" />
        <div>
          <p className="text-sm text-blue-700 font-medium mb-1">Admin Access Required</p>
          <p className="text-sm text-blue-600">
            Only users with admin role can access these settings. Contact your system administrator if you need elevated permissions.
          </p>
        </div>
      </div>
    </AuthLayout>
  );
}
