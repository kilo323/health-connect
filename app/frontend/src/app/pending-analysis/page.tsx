'use client';

import { useState, useEffect } from 'react';
import AuthLayout from '@/components/AuthLayout';
import { Stethoscope, CheckCircle, XCircle, ChevronDown, ChevronUp, RefreshCw, Calendar } from 'lucide-react';
import apiClient from '@/lib/api-client';

interface PendingAnalysis {
  id: number;
  document_id: number;
  filename: string;
  status: 'pending' | 'analyzing' | 'completed' | 'error' | 'approved' | 'rejected' | 'pending_review';
  analysis_summary?: string;
  test_date?: string | null;
  metrics_extracted?: Array<{
    name: string;
    value: string;
    unit: string;
    is_selected: boolean;
  }>;
  created_at: string;
}

export default function PendingAnalysisPage() {
  const [analyses, setAnalyses] = useState<PendingAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    loadAnalyses();
  }, []);

  const loadAnalyses = async () => {
    try {
      const res = await apiClient.get('/health/pending-analysis');
      setAnalyses(res.data || []);
    } catch (error) {
      console.error('Failed to load analyses:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async (id: number, analysisId: string) => {
    try {
      await apiClient.post(`/health/pending-analysis/${analysisId}/approve`);
      loadAnalyses();
    } catch (error) {
      console.error('Approval failed:', error);
    }
  };

  const handleReject = async (id: number, analysisId: string) => {
    if (!confirm('Are you sure? This will discard the extracted metrics.')) return;
    
    try {
      await apiClient.post(`/health/pending-analysis/${analysisId}/reject`);
      loadAnalyses();
    } catch (error) {
      console.error('Rejection failed:', error);
    }
  };

  const handleRetry = async (id: number) => {
    try {
      await apiClient.post(`/health/documents/${id}/analyze`);
      loadAnalyses();
    } catch (error) {
      console.error('Retry failed:', error);
    }
  };

  if (loading) {
    return <AuthLayout><div className="flex items-center justify-center h-full">Loading...</div></AuthLayout>;
  }

  return (
    <AuthLayout>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Pending Analysis</h1>

      {analyses.length === 0 ? (
        <div className="card text-center py-12">
          <Stethoscope className="h-12 w-12 text-gray-300 mx-auto mb-4" />
          <p className="text-gray-500">No pending analyses.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {analyses.map(analysis => (
            <div key={analysis.id} className="card hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <Stethoscope className={`h-5 w-5 ${
                    analysis.status === 'completed' ? 'text-green-600' :
                    analysis.status === 'approved' ? 'text-green-600' :
                    analysis.status === 'error' || analysis.status === 'rejected' ? 'text-red-600' :
                    'text-yellow-600'
                  }`} />
                  <h3 className="font-medium text-gray-900">{analysis.filename}</h3>
                  {analysis.test_date && (
                    <span className="flex items-center gap-1 text-xs text-blue-600 bg-blue-50 px-2 py-0.5 rounded-full">
                      <Calendar className="h-3 w-3" />
                      Test: {new Date(analysis.test_date).toLocaleDateString()}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  {(analysis.status === 'pending_review') && (
                    <>
                      <button
                        onClick={() => handleApprove(analysis.id, analysis.id.toString())}
                        className="btn-primary text-sm py-1.5 flex items-center gap-1"
                      >
                        <CheckCircle className="h-3 w-3" /> Approve & Save
                      </button>
                      <button
                        onClick={() => handleReject(analysis.id, analysis.id.toString())}
                        className="text-sm text-red-600 hover:text-red-800 font-medium px-3 py-1.5"
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {analysis.status === 'error' && (
                    <button
                      onClick={() => handleRetry(analysis.document_id)}
                      className="btn-secondary text-sm py-1.5 flex items-center gap-1"
                    >
                      <RefreshCw className="h-3 w-3" /> Retry
                    </button>
                  )}
                  <button
                    onClick={() => setExpandedId(expandedId === analysis.id ? null : analysis.id)}
                    className="p-1 hover:bg-gray-100 rounded"
                  >
                    {expandedId === analysis.id ? (
                      <ChevronUp className="h-4 w-4 text-gray-600" />
                    ) : (
                      <ChevronDown className="h-4 w-4 text-gray-600" />
                    )}
                  </button>
                </div>
              </div>

              {expandedId === analysis.id && (
                <div className="mt-4 pt-4 border-t border-gray-100">
                  {/* Summary */}
                  {analysis.analysis_summary && (
                    <div className="mb-4 p-3 bg-gray-50 rounded-lg">
                      <p className="text-sm text-gray-700">{analysis.analysis_summary}</p>
                    </div>
                  )}

                  {/* Extracted Metrics */}
                  {analysis.metrics_extracted && analysis.metrics_extracted.length > 0 && (
                    <div className="mb-4">
                      <h4 className="text-sm font-medium text-gray-700 mb-2">Extracted Metrics:</h4>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                        {analysis.metrics_extracted.map((metric, idx) => (
                          <div key={idx} className="flex items-center justify-between p-2 bg-gray-50 rounded">
                            <span className="text-sm text-gray-700">{metric.name}</span>
                            <span className="text-sm font-medium text-gray-900">
                              {metric.value} {metric.unit}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </AuthLayout>
  );
}
