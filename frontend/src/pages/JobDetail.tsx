import { useParams, Link } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { api } from '../api/client'
import StatusBadge from '../components/StatusBadge'
import StepProgress from '../components/StepProgress'
import { ArrowLeft, RotateCcw, Loader, AlertCircle, ExternalLink, HardDrive, Image, Cloud } from 'lucide-react'
import type { MigrationJob, Region } from '../api/types'
import { REGION_LABELS } from '../api/types'

function regionLabel(r: string): string {
  return REGION_LABELS[r as Region] || r
}

export default function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>()
  const { credentials } = useAuth()
  const [job, setJob] = useState<MigrationJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)

  useEffect(() => {
    if (!credentials || !jobId) return

    const fetchJob = async () => {
      try {
        const data = await api.getJob(credentials, jobId)
        setJob(data)
        setError(null)
      } catch (e: any) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }

    fetchJob()
    const isActive = job && job.step !== 'completed' && job.step !== 'failed'
    if (isActive) {
      const timer = setInterval(fetchJob, 5000)
      return () => clearInterval(timer)
    }
  }, [credentials, jobId, job?.step])

  const handleRetry = async () => {
    if (!credentials || !jobId) return
    setRetrying(true)
    try {
      await api.retryJob(credentials, jobId)
      const data = await api.getJob(credentials, jobId)
      setJob(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setRetrying(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader className="w-8 h-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (error || !job) {
    return (
      <div className="space-y-4">
        <Link to="/migrations" className="inline-flex items-center text-huawei-blue hover:underline">
          <ArrowLeft className="w-4 h-4 mr-1" /> Volver
        </Link>
        <div className="flex items-center gap-2 text-red-600 bg-red-50 p-4 rounded-lg">
          <AlertCircle className="w-5 h-5" />
          <span>{error || 'Job no encontrado'}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Link to="/migrations" className="inline-flex items-center text-huawei-blue hover:underline">
          <ArrowLeft className="w-4 h-4 mr-1" /> Volver a migraciones
        </Link>
        {job.step === 'failed' && (
          <button onClick={handleRetry} disabled={retrying} className="btn-primary">
            {retrying ? <Loader className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4 mr-2" />}
            Reintentar
          </button>
        )}
      </div>

      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-2xl font-bold">{job.backup_name}</h1>
          <StatusBadge step={job.step} />
        </div>
        <p className="text-sm text-gray-500 font-mono">Job ID: {job.job_id}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="text-lg font-bold mb-4">Progreso</h2>
          <StepProgress step={job.step} crossRegion={job.cross_region} />
        </div>

        <div className="card">
          <h2 className="text-lg font-bold mb-4">Detalles</h2>
          <dl className="space-y-3 text-sm">
            <div className="flex justify-between">
              <dt className="text-gray-500">Backup ID</dt>
              <dd className="font-mono text-gray-700">{job.backup_id.slice(0, 20)}...</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Origen</dt>
              <dd>{regionLabel(job.source_region)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Destino</dt>
              <dd>{regionLabel(job.target_region)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Cross-region</dt>
              <dd>{job.cross_region ? 'Si' : 'No'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Tamano</dt>
              <dd>{job.resource_size_gb} GB</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Creado</dt>
              <dd>{new Date(job.created_at).toLocaleString('es')}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Actualizado</dt>
              <dd>{new Date(job.updated_at).toLocaleString('es')}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Reintentos</dt>
              <dd>{job.retry_count}</dd>
            </div>
          </dl>
        </div>
      </div>

      {job.step === 'failed' && job.error && (
        <div className="card border-red-200">
          <h2 className="text-lg font-bold text-red-600 mb-2">Error</h2>
          <p className="text-sm text-red-600 bg-red-50 p-3 rounded-lg">{job.error}</p>
        </div>
      )}

      <div className="card">
        <h2 className="text-lg font-bold mb-4">Recursos</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
            <HardDrive className="w-5 h-5 text-gray-400" />
            <div>
              <p className="text-xs text-gray-500">Volumen EVS</p>
              <p className="text-sm font-mono">{job.volume_id || '-'}</p>
            </div>
          </div>
          <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
            <Image className="w-5 h-5 text-gray-400" />
            <div>
              <p className="text-xs text-gray-500">Imagen IMS</p>
              <p className="text-sm font-mono">{job.image_id || '-'}</p>
            </div>
          </div>
          <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
            <Cloud className="w-5 h-5 text-gray-400" />
            <div>
              <p className="text-xs text-gray-500">Bucket OBS</p>
              <p className="text-sm font-mono">{job.bucket_name || '-'}</p>
            </div>
          </div>
        </div>
        {job.object_key && job.step === 'completed' && (
          <div className="mt-4 flex items-center gap-2 text-sm text-green-600">
            <ExternalLink className="w-4 h-4" />
            <span>Archivo en OBS: {job.object_key}</span>
          </div>
        )}
      </div>
    </div>
  )
}
