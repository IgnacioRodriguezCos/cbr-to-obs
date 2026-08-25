import { useState } from 'react'
import { useBackups } from '../hooks/useBackups'
import { useAuth } from '../context/AuthContext'
import { api } from '../api/client'
import RegionSelector from '../components/RegionSelector'
import Modal from '../components/Modal'
import { RefreshCw, HardDrive, ArrowRight, Loader, AlertCircle, CheckCircle } from 'lucide-react'
import type { Region, Backup } from '../api/types'
import { REGION_LABELS } from '../api/types'

export default function Backups() {
  const { credentials } = useAuth()
  const [region, setRegion] = useState<Region>('buenosaires')
  const { backups, loading, error, refetch } = useBackups(region)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [migrating, setMigrating] = useState(false)
  const [migrateResult, setMigrateResult] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [targetRegion, setTargetRegion] = useState<Region | null>(null)
  const [singleBackup, setSingleBackup] = useState<Backup | null>(null)

  const toggleSelect = (id: string) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  const toggleAll = () => {
    if (selected.size === backups.length) setSelected(new Set())
    else setSelected(new Set(backups.map((b) => b.id)))
  }

  const handleMigrate = async (backupId: string, srcRegion: Region, tgtRegion: Region) => {
    if (!credentials) return
    setMigrating(true)
    setMigrateResult(null)
    try {
      const result = await api.migrate(credentials, backupId, srcRegion, tgtRegion)
      setMigrateResult(`Migracion iniciada. Job ID: ${result.job_id}`)
    } catch (e: any) {
      setMigrateResult(`Error: ${e.message}`)
    } finally {
      setMigrating(false)
    }
  }

  const handleBatchMigrate = async () => {
    if (!credentials || !targetRegion) return
    setMigrating(true)
    setMigrateResult(null)
    const results: string[] = []
    for (const id of selected) {
      try {
        const r = await api.migrate(credentials, id, region, targetRegion)
        results.push(`${id}: ${r.job_id}`)
      } catch (e: any) {
        results.push(`${id}: ERROR - ${e.message}`)
      }
    }
    setMigrateResult(results.join('\n'))
    setMigrating(false)
    setSelected(new Set())
    setShowModal(false)
  }

  const openSingleModal = (backup: Backup) => {
    setSingleBackup(backup)
    setTargetRegion(region)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Backups de EVS</h1>
        <div className="flex items-center gap-4">
          <RegionSelector value={region} onChange={(r) => { setRegion(r); setSelected(new Set()) }} />
          <button onClick={refetch} className="btn-secondary">
            <RefreshCw className="w-4 h-4 mr-2" />
            Actualizar
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-red-600 bg-red-50 p-4 rounded-lg">
          <AlertCircle className="w-5 h-5" />
          <span>{error}</span>
        </div>
      )}

      {migrateResult && (
        <div className="flex items-start gap-2 text-green-600 bg-green-50 p-4 rounded-lg whitespace-pre-wrap">
          <CheckCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <span>{migrateResult}</span>
        </div>
      )}

      <div className="card">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : backups.length === 0 ? (
          <p className="text-gray-500 text-center py-12">
            No hay backups de EVS en {REGION_LABELS[region]}
          </p>
        ) : (
          <>
            <div className="flex items-center justify-between mb-4">
              <label className="flex items-center gap-2 text-sm text-gray-600">
                <input
                  type="checkbox"
                  checked={selected.size === backups.length}
                  onChange={toggleAll}
                  className="rounded"
                />
                Seleccionar todos
              </label>
              {selected.size > 0 && (
                <button
                  onClick={() => { setTargetRegion(region); setShowModal(true) }}
                  className="btn-primary"
                >
                  Migrar {selected.size} seleccionado{selected.size > 1 ? 's' : ''}
                </button>
              )}
            </div>

            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-sm text-gray-500 border-b border-gray-200">
                    <th className="pb-2 w-8"></th>
                    <th className="pb-2 font-medium">Nombre</th>
                    <th className="pb-2 font-medium">ID</th>
                    <th className="pb-2 font-medium">Tamano</th>
                    <th className="pb-2 font-medium">Estado</th>
                    <th className="pb-2 font-medium">Fecha</th>
                    <th className="pb-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {backups.map((backup) => (
                    <tr key={backup.id} className="table-row border-b border-gray-100">
                      <td className="py-3">
                        <input
                          type="checkbox"
                          checked={selected.has(backup.id)}
                          onChange={() => toggleSelect(backup.id)}
                          className="rounded"
                        />
                      </td>
                      <td className="py-3 text-sm font-medium">{backup.name}</td>
                      <td className="py-3 text-sm text-gray-500 font-mono">{backup.id.slice(0, 12)}...</td>
                      <td className="py-3 text-sm">{backup.resource_size} GB</td>
                      <td className="py-3">
                        <span className="inline-flex items-center px-2 py-1 rounded-full text-xs bg-green-100 text-green-700">
                          {backup.status}
                        </span>
                      </td>
                      <td className="py-3 text-sm text-gray-500">
                        {new Date(backup.created_at).toLocaleDateString('es')}
                      </td>
                      <td className="py-3">
                        <button
                          onClick={() => openSingleModal(backup)}
                          className="btn-secondary text-sm px-3 py-1"
                        >
                          <ArrowRight className="w-3 h-3 mr-1" />
                          Migrar
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      <Modal
        open={showModal}
        onClose={() => setShowModal(false)}
        title={`Migrar ${selected.size} backup(s)`}
        footer={
          <>
            <button onClick={() => setShowModal(false)} className="btn-secondary">Cancelar</button>
            <button onClick={handleBatchMigrate} disabled={migrating} className="btn-primary">
              {migrating ? <Loader className="w-4 h-4 animate-spin" /> : 'Confirmar'}
            </button>
          </>
        }
      >
        <p className="text-sm text-gray-600 mb-4">
          Origen: <strong>{REGION_LABELS[region]}</strong>
        </p>
        <p className="text-sm text-gray-600 mb-2">Region destino:</p>
        <div className="flex gap-2">
          {(['buenosaires', 'santiago'] as Region[]).map((r) => (
            <button
              key={r}
              onClick={() => setTargetRegion(r)}
              className={`px-4 py-2 rounded-lg font-medium text-sm ${
                targetRegion === r ? 'bg-huawei-red text-white' : 'bg-gray-100 text-gray-600'
              }`}
            >
              {REGION_LABELS[r]}
            </button>
          ))}
        </div>
      </Modal>

      <Modal
        open={singleBackup !== null}
        onClose={() => setSingleBackup(null)}
        title="Migrar backup"
        footer={
          <>
            <button onClick={() => setSingleBackup(null)} className="btn-secondary">Cancelar</button>
            <button
              onClick={() => {
                if (singleBackup && targetRegion) {
                  handleMigrate(singleBackup.id, region, targetRegion)
                  setSingleBackup(null)
                }
              }}
              disabled={migrating || !targetRegion}
              className="btn-primary"
            >
              {migrating ? <Loader className="w-4 h-4 animate-spin" /> : 'Iniciar migracion'}
            </button>
          </>
        }
      >
        {singleBackup && (
          <>
            <p className="text-sm text-gray-600 mb-2">
              Backup: <strong>{singleBackup.name}</strong> ({singleBackup.resource_size} GB)
            </p>
            <p className="text-sm text-gray-600 mb-4">
              Origen: <strong>{REGION_LABELS[region]}</strong>
            </p>
            <p className="text-sm text-gray-600 mb-2">Region destino:</p>
            <div className="flex gap-2">
              {(['buenosaires', 'santiago'] as Region[]).map((r) => (
                <button
                  key={r}
                  onClick={() => setTargetRegion(r)}
                  className={`px-4 py-2 rounded-lg font-medium text-sm ${
                    targetRegion === r ? 'bg-huawei-red text-white' : 'bg-gray-100 text-gray-600'
                  }`}
                >
                  {REGION_LABELS[r]}
                </button>
              ))}
            </div>
          </>
        )}
      </Modal>
    </div>
  )
}
