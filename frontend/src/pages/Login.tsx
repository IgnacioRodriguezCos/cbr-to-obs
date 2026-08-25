import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { api } from '../api/client'
import { Cloud, Key, AlertCircle, Loader } from 'lucide-react'

export default function Login() {
  const { login } = useAuth()
  const [ak, setAk] = useState('')
  const [sk, setSk] = useState('')
  const [pidBa, setPidBa] = useState('')
  const [pidCl, setPidCl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)

    try {
      const creds = { ak, sk, pid_ba: pidBa, pid_cl: pidCl }
      await api.listBackups(creds, 'buenosaires')
      login(creds)
    } catch (err: any) {
      setError(err.message || 'Error de autenticacion')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-huawei-dark rounded-2xl mb-4">
            <Cloud className="w-8 h-8 text-huawei-red" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">CBR → OBS Migration</h1>
          <p className="text-gray-500 mt-1">Huawei Cloud Backup Migration Tool</p>
        </div>

        <form onSubmit={handleSubmit} className="card space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Access Key (AK)
            </label>
            <input
              type="text"
              value={ak}
              onChange={(e) => setAk(e.target.value)}
              className="input"
              placeholder="AKXXXXXXXX..."
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Secret Key (SK)
            </label>
            <input
              type="password"
              value={sk}
              onChange={(e) => setSk(e.target.value)}
              className="input"
              placeholder="sk-xxxx..."
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Project ID BA
              </label>
              <input
                type="text"
                value={pidBa}
                onChange={(e) => setPidBa(e.target.value)}
                className="input"
                placeholder="sa-argentina-1"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Project ID Santiago
              </label>
              <input
                type="text"
                value={pidCl}
                onChange={(e) => setPidCl(e.target.value)}
                className="input"
                placeholder="la-south-2"
                required
              />
            </div>
          </div>

          {error && (
            <div className="flex items-center gap-2 text-red-600 text-sm bg-red-50 p-3 rounded-lg">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button type="submit" disabled={loading} className="btn-primary w-full justify-center">
            {loading ? (
              <>
                <Loader className="w-4 h-4 animate-spin mr-2" />
                Validando...
              </>
            ) : (
              <>
                <Key className="w-4 h-4 mr-2" />
                Ingresar
              </>
            )}
          </button>
        </form>

        <p className="text-center text-xs text-gray-400 mt-6">
          Las credenciales se guardan solo en esta sesion del navegador.
        </p>
      </div>
    </div>
  )
}
