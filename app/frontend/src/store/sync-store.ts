'use client';

import { create } from 'zustand';

interface SyncState {
  syncInProgress: boolean;
  setSyncInProgress: (value: boolean) => void;
}

// Deliberately NOT persisted: the banner must always reflect live backend
// state. Persisting to localStorage let a stale `true` survive across
// sessions and show a phantom "sync in progress" banner. The banner polls
// /admin/status/sync on mount, so live state is learned within seconds.
export const useSyncStore = create<SyncState>()((set) => ({
  syncInProgress: false,
  setSyncInProgress: (value) => set({ syncInProgress: value }),
}));
