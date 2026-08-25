export interface Backup {
  id: string
  name: string
  resource_id: string
  resource_name: string
  resource_size: number
  resource_type: string
  status: string
  created_at: string
  updated_at: string
  expired_at: string
  description: string
  vault_id: string
  provider_id: string
  extend_info?: {
    auto_trigger?: boolean
    bootable?: boolean
    snapshot_id?: string
    system_disk?: boolean
    encrypted?: boolean
  }
}

export interface MigrationJob {
  job_id: string
  backup_id: string
  backup_name: string
  source_region: string
  target_region: string
  cross_region: boolean
  step: JobStep
  volume_id: string | null
  image_id: string | null
  export_job_id: string | null
  replication_record_id: string | null
  destination_backup_id: string | null
  bucket_name: string | null
  object_key: string | null
  resource_size_gb: number
  created_at: string
  updated_at: string
  error: string | null
  retry_count: number
}

export type JobStep =
  | 'replicating'
  | 'restoring'
  | 'creating_image'
  | 'exporting'
  | 'copying_obs'
  | 'cleanup_pending'
  | 'completed'
  | 'failed'

export interface Credentials {
  ak: string
  sk: string
  pid_ba: string
  pid_cl: string
}

export interface ApiResponse<T> {
  data: T | null
  error: string | null
  status: number
}

export type Region = 'buenosaires' | 'santiago'

export const STEP_LABELS: Record<JobStep, string> = {
  replicating: 'Replicando',
  restoring: 'Restaurando',
  creating_image: 'Creando Imagen',
  exporting: 'Exportando',
  copying_obs: 'Copiando OBS',
  cleanup_pending: 'Limpieza',
  completed: 'Completado',
  failed: 'Fallido',
}

export const STEP_ORDER: JobStep[] = [
  'replicating',
  'restoring',
  'creating_image',
  'exporting',
  'copying_obs',
  'completed',
]

export const REGION_LABELS: Record<Region, string> = {
  buenosaires: 'Buenos Aires',
  santiago: 'Santiago',
}
