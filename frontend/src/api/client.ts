import type { Backup, MigrationJob, Credentials, Region } from './types'

const API_BASE = import.meta.env.VITE_API_BASE || ''

function getAuthHeaders(creds: Credentials): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-HW-AK': creds.ak,
    'X-HW-SK': creds.sk,
    'X-HW-Project-Id-BA': creds.pid_ba,
    'X-HW-Project-Id-CL': creds.pid_cl,
  }
}

async function request<T>(
  path: string,
  creds: Credentials,
  options: RequestInit = {},
): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...getAuthHeaders(creds),
      ...options.headers,
    },
  })

  const text = await resp.text()
  let body: any = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = { error: text }
    }
  }

  if (!resp.ok) {
    throw new Error(body?.error || `HTTP ${resp.status}`)
  }

  return body as T
}

export const api = {
  async listBackups(creds: Credentials, region: Region): Promise<Backup[]> {
    const data = await request<{ backups: Backup[] }>(
      `/api/backups?region=${region}`,
      creds,
    )
    return data.backups || []
  },

  async getBackup(creds: Credentials, backupId: string, region: Region): Promise<Backup> {
    const data = await request<{ backup: Backup }>(
      `/api/backups/${backupId}?region=${region}`,
      creds,
    )
    return data.backup
  },

  async listJobs(creds: Credentials): Promise<MigrationJob[]> {
    const data = await request<{ jobs: MigrationJob[] }>(`/api/jobs`, creds)
    return data.jobs || []
  },

  async getJob(creds: Credentials, jobId: string): Promise<MigrationJob> {
    const data = await request<{ job: MigrationJob }>(`/api/jobs/${jobId}`, creds)
    return data.job
  },

  async migrate(
    creds: Credentials,
    backupId: string,
    sourceRegion: Region,
    targetRegion?: Region,
  ): Promise<{ job_id: string; step: string }> {
    return request(`/api/migrate`, creds, {
      method: 'POST',
      body: JSON.stringify({
        backup_id: backupId,
        source_region: sourceRegion,
        target_region: targetRegion || sourceRegion,
      }),
    })
  },

  async retryJob(creds: Credentials, jobId: string): Promise<void> {
    await request(`/api/jobs/${jobId}/retry`, creds, { method: 'POST' })
  },

  async deleteJob(creds: Credentials, jobId: string): Promise<void> {
    await request(`/api/jobs/${jobId}`, creds, { method: 'DELETE' })
  },
}
