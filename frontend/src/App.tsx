import { AuthProvider, useAuth } from './context/AuthContext'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Backups from './pages/Backups'
import Migrations from './pages/Migrations'
import JobDetail from './pages/JobDetail'
import { Routes, Route, Navigate } from 'react-router-dom'

function AppRoutes() {
  const { isAuthenticated } = useAuth()

  if (!isAuthenticated) {
    return <Login />
  }

  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/backups" element={<Backups />} />
        <Route path="/migrations" element={<Migrations />} />
        <Route path="/jobs/:jobId" element={<JobDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  )
}
