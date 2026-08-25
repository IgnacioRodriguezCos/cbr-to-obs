import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import type { Credentials } from '../api/types'

interface AuthContextValue {
  credentials: Credentials | null
  isAuthenticated: boolean
  login: (creds: Credentials) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

const STORAGE_KEY = 'cbr-obs-creds'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [credentials, setCredentials] = useState<Credentials | null>(null)

  useEffect(() => {
    const stored = sessionStorage.getItem(STORAGE_KEY)
    if (stored) {
      try {
        setCredentials(JSON.parse(stored))
      } catch {
        sessionStorage.removeItem(STORAGE_KEY)
      }
    }
  }, [])

  const login = (creds: Credentials) => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(creds))
    setCredentials(creds)
  }

  const logout = () => {
    sessionStorage.removeItem(STORAGE_KEY)
    setCredentials(null)
  }

  return (
    <AuthContext.Provider
      value={{
        credentials,
        isAuthenticated: credentials !== null,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return ctx
}
