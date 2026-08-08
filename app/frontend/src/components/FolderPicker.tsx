'use client';

import { useState, useCallback, useEffect } from 'react';
import { Database, Loader2, Plus } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface FolderPickerProps {
  currentPath: string;
  onSelect: (path: string) => void;
}

export default function FolderPicker({ currentPath, onSelect }: FolderPickerProps) {
  const [browsePath, setBrowsePath] = useState(currentPath);
  const [folders, setFolders] = useState<{ path: string; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);

  // Sync browsePath when currentPath changes from parent
  useEffect(() => {
    setBrowsePath(currentPath);
  }, [currentPath]);

  const browse = useCallback(async (path: string) => {
    setLoading(true);
    try {
      const res = await apiClient.get('/health/nextcloud/browse', { params: { path } });
      setFolders(res.data.folders || []);
      setBrowsePath(res.data.path || '/');
    } catch (error) {
      console.error('Failed to browse:', error);
      setFolders([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const createFolder = useCallback(async () => {
    if (!newFolderName) return;
    setCreatingFolder(true);
    try {
      const newPath = browsePath === '/' ? `/${newFolderName}` : `${browsePath}${newFolderName}`;
      await apiClient.post('/health/nextcloud/mkdir', { path: newPath + '/' });
      setNewFolderName('');
      await browse(browsePath);
    } catch (error) {
      console.error('Failed to create folder:', error);
    } finally {
      setCreatingFolder(false);
    }
  }, [newFolderName, browsePath, browse]);

  const pathParts = browsePath.split('/').filter(Boolean);

  return (
    <div className="border border-gray-200 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Sync Path</h3>
          <p className="text-xs text-gray-500">Choose the folder to sync health documents from</p>
        </div>
        <button type="button" onClick={() => browse(browsePath)} className="btn-secondary text-sm px-3 py-1.5">
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Browse'}
        </button>
      </div>

      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm text-gray-700 font-medium">Path:</span>
        <code className="text-sm bg-gray-100 px-2 py-1 rounded font-mono">{currentPath}</code>
      </div>

      <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
        {/* Breadcrumb */}
        <div className="flex items-center gap-1 text-xs text-gray-500 mb-2 flex-wrap">
          <button onClick={() => browse('/')} className="hover:text-blue-600 font-medium">/</button>
          {pathParts.map((part, i) => {
            const fullPath = '/' + pathParts.slice(0, i + 1).join('/') + '/';
            return (
              <span key={i} className="flex items-center gap-1">
                <span>/</span>
                <button onClick={() => browse(fullPath)} className="hover:text-blue-600 font-medium">{part}</button>
              </span>
            );
          })}
        </div>

        {/* Folder list */}
        {loading ? (
          <div className="flex items-center gap-2 py-4 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading folders...
          </div>
        ) : folders.length === 0 ? (
          <p className="text-sm text-gray-400 py-2">No subfolders</p>
        ) : (
          <div className="max-h-48 overflow-y-auto space-y-1">
            {folders.map((folder) => (
              <button
                key={folder.path}
                onClick={() => browse(folder.path)}
                className="w-full text-left px-3 py-1.5 rounded text-sm hover:bg-white hover:border-blue-300 border border-transparent flex items-center gap-2 text-gray-700"
              >
                <Database className="h-3.5 w-3.5 text-gray-400" />
                {folder.name}
              </button>
            ))}
          </div>
        )}

        {/* Select & Create */}
        <div className="mt-3 pt-3 border-t border-gray-200 space-y-3">
          <button type="button" onClick={() => onSelect(browsePath)} className="btn-primary text-sm px-3 py-1.5">
            Select This Folder
          </button>

          <div className="flex gap-2">
            <input
              type="text"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              placeholder="New folder name"
              className="input-field flex-1 text-sm py-1.5"
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); createFolder(); } }}
            />
            <button
              type="button"
              onClick={createFolder}
              disabled={creatingFolder || !newFolderName}
              className="btn-secondary text-sm px-3 py-1.5 flex items-center gap-1"
            >
              {creatingFolder ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
              Create
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
