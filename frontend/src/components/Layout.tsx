import { type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { LayoutDashboard, HardDrive, ArrowRightLeft, LogOut, Cloud } from 'lucide-react'
import clsx from 'clsx'

const navItems = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/backups', label: 'Backups', icon: HardDrive },
  { to: '/migrations', label: 'Migraciones', icon: ArrowRightLeft },
]

export default function Layout({ children }: { children: ReactNode }) {
  const { logout } = useAuth()
  const location = useLocation()

  return (
    <div className="min-h-screen flex">
      <aside className="w-64 bg-huawei-dark text-white flex flex-col">
        <div className="p-6 flex items-center gap-3 border-b border-gray-700">
          <Cloud className="w-8 h-8 text-huawei-red" />
          <div>
            <h1 className="text-lg font-bold">CBR → OBS</h1>
            <p className="text-xs text-gray-400">Migration Tool</p>
          </div>
        </div>

        <nav className="flex-1 p-4 space-y-1">
          {navItems.map((item) => {
            const active = location.pathname === item.to
            return (
              <Link
                key={item.to}
                to={item.to}
                className={clsx(
                  'flex items-center gap-3 px-4 py-3 rounded-lg transition-colors',
                  active
                    ? 'bg-huawei-red text-white'
                    : 'text-gray-400 hover:bg-gray-800 hover:text-white',
                )}
              >
                <item.icon className="w-5 h-5" />
                <span className="font-medium">{item.label}</span>
              </Link>
            )
          })}
        </nav>

        <div className="p-4 border-t border-gray-700">
          <button
            onClick={logout}
            className="flex items-center gap-3 px-4 py-3 rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white transition-colors w-full"
          >
            <LogOut className="w-5 h-5" />
            <span className="font-medium">Cerrar Sesion</span>
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <div className="p-8">{children}</div>
      </main>
    </div>
  )
}
