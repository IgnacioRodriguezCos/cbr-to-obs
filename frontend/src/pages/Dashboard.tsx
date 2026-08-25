import { Link } from 'react-router-dom'
import { useJobs } from '../hooks/useJobs'
import StatsCard from '../components/StatsCard'
import StatusBadge from '../components/StatusBadge'
import { HardDrive, CheckCircle, AlertCircle, Activity, ArrowRight, Plus } from 'lucide-react'
import type { MigrationJob } from '../api/types'
import { REGION_LABELS, type Region } from '../api/types'

function regionLabel(r: string): string {
  return REGION_LABELS[r as Region] || r
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'ahora'
  if (mins < 60) return `${mins} min`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

export default function Dashboard() {
  const { jobs, loading } = useJobs()

  const active = jobs.filter((j) => j.step !== 'completed' && j.step !== 'failed')
  const completed = jobs.filter((j) => j.step === 'completed')
  const failed = jobs.filter((j) => j.step === 'failed')

  const recent = [...jobs]
    .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    .slice(0, 10)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <Link to="/backups" className="btn-primary">
          <Plus className="w-4 h-4 mr-2" />
          Nueva Migracion
        </Link>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatsCard title="Total Jobs" value={jobs.length} icon={<HardDrive className="w-6 h-6" />} />
        <StatsCard title="Activos" value={active.length} icon={<Activity className="w-6 h-6" />} color="text-blue-500" />
        <StatsCard title="Completados" value={completed.length} icon={<CheckCircle className="w-6 h-6" />} color="text-green-500" />
        <StatsCard title="Fallidos" value={failed.length} icon={<AlertCircle className="w-6 h-6" />} color="text-red-500" />
      </div>

      <div className="card">
        <h2 className="text-lg font-bold mb-4">Jobs Recientes</h2>
        {loading ? (
          <p className="text-gray-500">Cargando...</p>
        ) : recent.length === 0 ? (
          <p className="text-gray-500 text-center py-8">No hay migraciones. Ve a Backups para iniciar una.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
                  <th className="pb-2 font-medium">Backup</th>
                  <th className="pb-2 font-medium">Origen</th>
                  <th className="pb-2 font-medium">Destino</th>
                  <th className="pb-2 font-medium">Estado</th>
                  <th className="pb-2 font-medium">Actualizado</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {recent.map((job: MigrationJob) => (
                  <tr key={job.job_id} className="table-row border-b border-gray-100">
                    <td className="py-3 text-sm">{job.backup_name}</td>
                    <td className="py-3 text-sm">{regionLabel(job.source_region)}</td>
                    <td className="py-3 text-sm">{regionLabel(job.target_region)}</td>
                    <td className="py-3"><StatusBadge step={job.step} /></td>
                    <td className="py-3 text-sm text-gray-500">{timeAgo(job.updated_at)}</td>
                    <td className="py-3">
                      <Link to={`/jobs/${job.job_id}`} className="text-huawei-blue hover:underline">
                        <ArrowRight className="w-4 h-4" />
                      </Link>
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
