'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { 
  LayoutDashboard, 
  Activity, 
  FileText, 
  Settings, 
  LogOut,
  Stethoscope,
  Database,
  Bot,
  Users
} from 'lucide-react';

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Health Data', href: '/health-data', icon: Activity },
  { name: 'Documents', href: '/documents', icon: FileText },
  { name: 'Pending Analysis', href: '/pending-analysis', icon: Stethoscope },
  { name: 'Settings', href: '/settings', icon: Settings },
];

const adminNavigation = [
  { name: 'Admin Dashboard', href: '/admin/dashboard', icon: LayoutDashboard },
  { name: 'Users', href: '/admin/users', icon: Users },
  { name: 'LLM Config', href: '/admin/llm-config', icon: Bot },
  { name: 'Schedule', href: '/admin/schedule', icon: Database },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  
  return (
    <div className="w-64 bg-white border-r border-gray-200 h-screen flex flex-col">
      <div className="p-6 border-b border-gray-100">
        <Link href="/dashboard" className="flex items-center gap-3">
          <Activity className="h-8 w-8 text-blue-600" />
          <span className="text-xl font-bold text-gray-900">Health Tracker</span>
        </Link>
      </div>

      <nav className="flex-1 p-4 space-y-1 overflow-y-auto">
        {navigation.map((item) => (
          <Link
            key={item.name}
            href={item.href}
            className={`sidebar-link ${pathname === item.href ? 'sidebar-link-active' : ''}`}
          >
            <item.icon className="h-5 w-5" />
            {item.name}
          </Link>
        ))}

        {pathname.startsWith('/admin') && (
          <>
            <div className="pt-4 pb-2">
              <p className="px-4 text-xs font-semibold text-gray-400 uppercase tracking-wider">
                Admin
              </p>
            </div>
            {adminNavigation.map((item) => (
              <Link
                key={item.name}
                href={item.href}
                className={`sidebar-link ${pathname === item.href ? 'sidebar-link-active' : ''}`}
              >
                <item.icon className="h-5 w-5" />
                {item.name}
              </Link>
            ))}
          </>
        )}
      </nav>

      <div className="p-4 border-t border-gray-100">
        <button
          onClick={() => router.push('/logout')}
          className="sidebar-link text-red-600 hover:bg-red-50 hover:text-red-700"
        >
          <LogOut className="h-5 w-5" />
          Sign Out
        </button>
      </div>
    </div>
  );
}
