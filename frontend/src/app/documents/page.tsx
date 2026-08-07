'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { FileText, Upload, Search, Bot, Eye, Trash2, CheckCircle, XCircle } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface Document {
  id: number;
  filename: string;
  file_path: string;
  file_type: string;
  status: 'unprocessed' | 'analyzing' | 'completed' | 'error';
  source: string;
  size_bytes: number;
  created_at: string;
}

const STATUS_CONFIG = {
  unprocessed: { color: 'bg-yellow-100 text-yellow-800', icon: FileText },
  analyzing: { color: 'bg-blue-100 text-blue-800', icon: Bot },
  completed: { color: 'bg-green-100 text-green-800', icon: CheckCircle },
  error: { color: 'bg-red-100 text-red-800', icon: XCircle },
};

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [uploading, setUploading] = useState(false);
  const [fileInputRef, setFileInputRef] = useState<HTMLInputElement | null>(null);

  useEffect(() => {
    loadDocuments();
  }, []);

  const loadDocuments = async () => {
    try {
      const res = await apiClient.get('/documents');
      setDocuments(res.data || []);
    } catch (error) {
      console.error('Failed to load documents:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleFileUpload = async () => {
    if (!fileInputRef?.files || fileInputRef.files.length === 0) return;

    setUploading(true);
    try {
      const formData = new FormData();
      for (let i = 0; i < fileInputRef.files.length; i++) {
        formData.append('files', fileInputRef.files[i]);
      }
      await apiClient.post('/documents/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      loadDocuments();
    } catch (error) {
      console.error('Upload failed:', error);
    } finally {
      setUploading(false);
      if (fileInputRef) fileInputRef.value = '';
    }
  };

  const handleAnalyze = async (id: number, filename: string) => {
    try {
      await apiClient.post(`/documents/${id}/analyze`);
      loadDocuments();
    } catch (error) {
      console.error('Analysis failed:', error);
    }
  };

  const handleDelete = async (id: number, filename: string) => {
    if (!confirm(`Delete "${filename}"? This cannot be undone.`)) return;
    
    try {
      await apiClient.delete(`/documents/${id}`);
      loadDocuments();
    } catch (error) {
      console.error('Delete failed:', error);
    }
  };

  const filteredDocs = documents.filter(doc =>
    doc.filename.toLowerCase().includes(searchQuery.toLowerCase()) ||
    doc.status.includes(searchQuery.toLowerCase())
  );

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  return (
    <AuthLayout>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Documents</h1>
        <button onClick={() => fileInputRef?.click()} className="btn-primary flex items-center gap-2">
          <Upload className="h-4 w-4" /> Upload Documents
        </button>
      </div>

      {/* Hidden file input */}
      <input
        ref={setFileInputRef}
        type="file"
        multiple
        accept=".pdf,.doc,.docx,.txt,.png,.jpg,.jpeg"
        className="hidden"
        onChange={handleFileUpload}
      />

      {/* Search & Upload progress */}
      <div className="flex items-center gap-3 mb-6">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search documents..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input-field pl-10"
          />
        </div>
        {uploading && (
          <span className="text-sm text-blue-600 font-medium">Uploading...</span>
        )}
      </div>

      {/* Documents List */}
      {filteredDocs.length === 0 ? (
        <div className="card text-center py-12">
          <FileText className="h-12 w-12 text-gray-300 mx-auto mb-4" />
          <p className="text-gray-500 mb-4">No documents uploaded yet.</p>
          <button onClick={() => fileInputRef?.click()} className="btn-primary">
            Upload Your First Document
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredDocs.map(doc => {
            const StatusIcon = STATUS_CONFIG[doc.status]?.icon || FileText;
            return (
              <div key={doc.id} className="card hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between mb-3">
                  <StatusIcon className={`h-5 w-5 ${
                    doc.status === 'completed' ? 'text-green-600' :
                    doc.status === 'error' ? 'text-red-600' :
                    doc.status === 'analyzing' ? 'text-blue-600' :
                    'text-yellow-600'
                  }`} />
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${STATUS_CONFIG[doc.status]?.color}`}>
                    {doc.status}
                  </span>
                </div>

                <h3 className="font-medium text-gray-900 mb-1 truncate" title={doc.filename}>
                  {doc.filename}
                </h3>

                <p className="text-sm text-gray-500 mb-4">
                  {(doc.size_bytes / 1024).toFixed(1)} KB · {new Date(doc.created_at).toLocaleDateString()}
                </p>

                <div className="flex items-center gap-2 pt-3 border-t border-gray-100">
                  {doc.status === 'unprocessed' && (
                    <button
                      onClick={() => handleAnalyze(doc.id, doc.filename)}
                      className="btn-primary flex-1 text-sm py-2"
                    >
                      <Bot className="h-3 w-3 mr-1" /> Analyze
                    </button>
                  )}
                  {doc.status === 'completed' && (
                    <button className="btn-secondary flex-1 text-sm py-2">
                      <Eye className="h-3 w-3 mr-1" /> View Result
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(doc.id, doc.filename)}
                    className="text-red-500 hover:text-red-700 p-2 hover:bg-red-50 rounded-lg transition-colors"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </AuthLayout>
  );
}
