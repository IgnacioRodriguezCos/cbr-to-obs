import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { MigrationJob } from '../api/types'
import { useAuth } from '../context/AuthContext'

export function useJobs(intervalMs = 10000) {
  const { credentials } = useAuth()
  const [jobs, setJobs] = useState<MigrationJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchJobs = useCallback(async () => {
    if (!credentials) return
    try {
      const data = await api.listJobs(credentials)
      setJobs(data)
      setError(null)
    } catch (e: any) {
      setError(e.message || 'Error loading jobs')
    } finally {
      setLoading(false)
    }
  }, [credentials])

  useEffect(() => {
    fetchJobs()
    const hasActive = jobs.some(
      (j) => j.step !== 'completed' && j.step !== 'failed',
    )
    if (hasActive) {
      const timer = setInterval(fetchJobs, intervalMs)
      return () => clearInterval(timer)
    }
  }, [fetchJobs, intervalMs, jobs])

  return { jobs, loading, error, refetch: fetchJobs }
}
