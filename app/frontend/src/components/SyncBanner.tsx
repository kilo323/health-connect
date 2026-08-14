'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, RefreshCw, AlertTriangle } from 'lucide-react';
import { useSyncStore } from '@/store/sync-store';
import apiClient from '@/lib/api-client';

const POLL_INTERVAL_MS = 3000;
// If the backend reports a sync has been running longer than this, show a
// warning and offer a way to clear the stale flag.
const STUCK_THRESHOLD_MS = 5 * 60 * 1000;
// After this many consecutive poll failures, assume the status endpoint is
// unreachable and hide the banner rather than leave a phantom "syncing" up.
const MAX_CONSECUTIVE_FAILURES = 5;

export default function SyncBanner() {
  const { syncInProgress, setSyncInProgress } = useSyncStore();
  const [syncStartedAt, setSyncStartedAt] = useState<number | null>(null);
  const failureCount = useRef(0);

  useEffect(() => {
    let cancelled = false;

    const checkStatus = async () => {
      try {
        const res = await apiClient.get('/admin/status/sync');
        const inProgress = res.data?.sync_in_progress ?? false;
        if (!cancelled) {
          failureCount.current = 0; // successful poll resets the failure streak
          setSyncInProgress(inProgress);
          if (inProgress && syncStartedAt === null) {
            setSyncStartedAt(Date.now());
          } else if (!inProgress) {
            setSyncStartedAt(null);
          }
        }
      } catch (error) {
        // Fail-open: if we can't reach the status endpoint repeatedly, stop
        // showing the banner instead of leaving stale state on screen.
        failureCount.current += 1;
        if (!cancelled && failureCount.current >= MAX_CONSECUTIVE_FAILURES) {
          setSyncInProgress(false);
          setSyncStartedAt(null);
        }
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [setSyncInProgress, syncStartedAt]);

  const isStuck = useMemo(() => {
    return syncInProgress && syncStartedAt !== null && Date.now() - syncStartedAt > STUCK_THRESHOLD_MS;
  }, [syncInProgress, syncStartedAt]);

  const handleClear = async () => {
    try {
      await apiClient.post('/admin/sync/reset');
      setSyncInProgress(false);
      setSyncStartedAt(null);
    } catch (error) {
      // ignore — user may not be admin
    }
  };

  if (!syncInProgress) return null;

  return (
    <div className={`text-white px-6 py-2.5 flex items-center justify-center gap-2 text-sm font-medium shadow-sm ${isStuck ? 'bg-amber-600' : 'bg-blue-600'}`}>
      {isStuck ? <AlertTriangle className="h-4 w-4" /> : <Loader2 className="h-4 w-4 animate-spin" />}
      <RefreshCw className="h-4 w-4" />
      <span>
        {isStuck
          ? 'Health data sync appears to be stuck.'
          : 'Health data sync is in progress. Some features may be temporarily slower until it finishes.'}
      </span>
      {isStuck && (
        <button
          onClick={handleClear}
          className="ml-2 underline hover:no-underline focus:outline-none"
        >
          Clear status
        </button>
      )}
    </div>
  );
}
