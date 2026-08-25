import type { Region } from '../api/types'
import { REGION_LABELS } from '../api/types'
import clsx from 'clsx'

interface Props {
  value: Region
  onChange: (region: Region) => void
}

export default function RegionSelector({ value, onChange }: Props) {
  const regions: Region[] = ['buenosaires', 'santiago']

  return (
    <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden">
      {regions.map((r) => (
        <button
          key={r}
          onClick={() => onChange(r)}
          className={clsx(
            'px-4 py-2 font-medium text-sm transition-colors',
            value === r
              ? 'bg-huawei-red text-white'
              : 'bg-white text-gray-600 hover:bg-gray-50',
          )}
        >
          {REGION_LABELS[r]}
        </button>
      ))}
    </div>
  )
}
