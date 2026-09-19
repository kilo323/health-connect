'use client';

import Sidebar from '@/components/Sidebar';
import Header from '@/components/Header';
import { useAuthStore } from '@/store/auth-store';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Wait for Zustand persist to rehydrate from localStorage.
    // In Zustand v5 the old onFinishHydration/hasHydrated helpers can
    // fire out of order, so we check localStorage directly as a fallback.
    const token = localStorage.getItem('token');
    if (!token) {
      router.push('/login');
      setReady(true); // allow redirect effect to fire
      return;
    }

    // Token exists — mark ready once Zustand has rehydrated the store
    const tryReady = () => setReady(true);
    if (useAuthStore.persist.hasHydrated()) {
      tryReady();
    } else {
      const unsub = useAuthStore.persist.onFinishHydration(tryReady);
      return unsub;
    }
  }, [router]);

  // Redirect after hydration confirms the user is not authenticated
  useEffect(() => {
    if (ready && !isAuthenticated) {
      router.push('/login');
    }
  }, [ready, isAuthenticated, router]);

  if (!ready) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>;
  }

  if (!isAuthenticated) {
    return null; // Will redirect via useEffect
  }

  return (
    <div className="flex h-screen bg-gray-50">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
