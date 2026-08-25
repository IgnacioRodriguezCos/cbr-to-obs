import { type ReactNode } from 'react'

interface Props {
  title: string
  value: string | number
  icon: ReactNode
  color?: string
}

export default function StatsCard({ title, value, icon, color = 'text-huawei-red' }: Props) {
  return (
    <div className="card flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl bg-gray-50 flex items-center justify-center ${color}`}>
        {icon}
      </div>
      <div>
        <p className="text-sm text-gray-500">{title}</p>
        <p className="text-2xl font-bold">{value}</p>
      </div>
    </div>
  )
}
