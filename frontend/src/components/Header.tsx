'use client';

import { useAuthStore } from '@/store/auth-store';
import Link from 'next/link';
import { Activity, Bell, User } from 'lucide-react';

export default function Header() {
  const user = useAuthStore((state) => state.user);

  return (
    <header className="bg-white border-b border-gray-200 px-6 py-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Health Tracker</h1>

        <div className="flex items-center gap-4">
          <button className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
            <Bell className="h-5 w-5 text-gray-600" />
          </button>

          {user && (
            <div className="flex items-center gap-3 pl-4 border-l border-gray-200">
              <User className="h-5 w-5 text-gray-600" />
              <span className="text-sm font-medium text-gray-700">{user.username}</span>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
