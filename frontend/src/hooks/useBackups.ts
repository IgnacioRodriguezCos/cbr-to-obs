import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { Backup, Region } from '../api/types'
import { useAuth } from '../context/AuthContext'

export function useBackups(region: Region) {
  const { credentials } = useAuth()
  const [backups, setBackups] = useState<Backup[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchBackups = useCallback(async () => {
    if (!credentials) return
    setLoading(true)
    try {
      const data = await api.listBackups(credentials, region)
      setBackups(data)
      setError(null)
    } catch (e: any) {
      setError(e.message || 'Error loading backups')
    } finally {
      setLoading(false)
    }
  }, [credentials, region])

  useEffect(() => {
    fetchBackups()
  }, [fetchBackups])

  return { backups, loading, error, refetch: fetchBackups }
}
