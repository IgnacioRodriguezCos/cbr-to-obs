import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useJobs } from '../hooks/useJobs'
import { useAuth } from '../context/AuthContext'
import { api } from '../api/client'
import StatusBadge from '../components/StatusBadge'
import { RefreshCw, RotateCcw, Trash2, ArrowRight, Loader, AlertCircle } from 'lucide-react'
import type { MigrationJob, JobStep, Region } from '../api/types'
import { REGION_LABELS } from '../api/types'

const FILTERS: { label: string; value: string }[] = [
  { label: 'Todos', value: 'all' },
  { label: 'Activos', value: 'active' },
  { label: 'Completados', value: 'completed' },
  { label: 'Fallidos', value: 'failed' },
]

function regionLabel(r: string): string {
  return REGION_LABELS[r as Region] || r
}

export default function Migrations() {
  const { credentials } = useAuth()
  const { jobs, loading, error, refetch } = useJobs()
  const [filter, setFilter] = useState('all')
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const filtered = jobs.filter((job) => {
    if (filter === 'all') return true
    if (filter === 'active') return job.step !== 'completed' && job.step !== 'failed'
    return job.step === filter
  })

  const sorted = [...filtered].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
  )

  const handleRetry = async (jobId: string) => {
    if (!credentials) return
    setActionLoading(jobId)
    try {
      await api.retryJob(credentials, jobId)
      refetch()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setActionLoading(null)
    }
  }

  const handleDelete = async (jobId: string) => {
    if (!credentials) return
    if (!confirm('Eliminar este job?')) return
    setActionLoading(jobId)
    try {
      await api.deleteJob(credentials, jobId)
      refetch()
    } catch (e: any) {
      alert(`Error: ${e.message}`)
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Migraciones</h1>
        <button onClick={refetch} className="btn-secondary">
          <RefreshCw className="w-4 h-4 mr-2" />
          Actualizar
        </button>
      </div>

      <div className="flex gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`px-4 py-2 rounded-lg font-medium text-sm ${
              filter === f.value ? 'bg-huawei-red text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 text-red-600 bg-red-50 p-4 rounded-lg">
          <AlertCircle className="w-5 h-5" />
          <span>{error}</span>
        </div>
      )}

      <div className="card">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : sorted.length === 0 ? (
          <p className="text-gray-500 text-center py-12">No hay migraciones.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
                  <th className="pb-2 font-medium">Backup</th>
                  <th className="pb-2 font-medium">Origen</th>
                  <th className="pb-2 font-medium">Destino</th>
                  <th className="pb-2 font-medium">Paso</th>
                  <th className="pb-2 font-medium">Tamano</th>
                  <th className="pb-2 font-medium">Actualizado</th>
                  <th className="pb-2 font-medium">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((job: MigrationJob) => (
                  <tr key={job.job_id} className="table-row border-b border-gray-100">
                    <td className="py-3 text-sm font-medium">{job.backup_name}</td>
                    <td className="py-3 text-sm">{regionLabel(job.source_region)}</td>
                    <td className="py-3 text-sm">{regionLabel(job.target_region)}</td>
                    <td className="py-3"><StatusBadge step={job.step as JobStep} /></td>
                    <td className="py-3 text-sm">{job.resource_size_gb} GB</td>
                    <td className="py-3 text-sm text-gray-500">
                      {new Date(job.updated_at).toLocaleString('es')}
                    </td>
                    <td className="py-3">
                      <div className="flex items-center gap-2">
                        <Link to={`/jobs/${job.job_id}`} className="text-huawei-blue hover:underline">
                          <ArrowRight className="w-4 h-4" />
                        </Link>
                        {job.step === 'failed' && (
                          <button
                            onClick={() => handleRetry(job.job_id)}
                            disabled={actionLoading === job.job_id}
                            className="text-blue-600 hover:text-blue-800"
                            title="Reintentar"
                          >
                            {actionLoading === job.job_id ? (
                              <Loader className="w-4 h-4 animate-spin" />
                            ) : (
                              <RotateCcw className="w-4 h-4" />
                            )}
                          </button>
                        )}
                        {(job.step === 'completed' || job.step === 'failed') && (
                          <button
                            onClick={() => handleDelete(job.job_id)}
                            disabled={actionLoading === job.job_id}
                            className="text-red-500 hover:text-red-700"
                            title="Eliminar"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
