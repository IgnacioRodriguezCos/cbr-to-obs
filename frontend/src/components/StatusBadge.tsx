import type { JobStep } from '../api/types'
import { STEP_LABELS } from '../api/types'
import clsx from 'clsx'

const COLORS: Record<JobStep, string> = {
  replicating: 'bg-blue-100 text-blue-700',
  restoring: 'bg-blue-100 text-blue-700',
  creating_image: 'bg-blue-100 text-blue-700',
  exporting: 'bg-blue-100 text-blue-700',
  copying_obs: 'bg-blue-100 text-blue-700',
  cleanup_pending: 'bg-yellow-100 text-yellow-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
}

export default function StatusBadge({ step }: { step: JobStep }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold',
        COLORS[step],
      )}
    >
      {STEP_LABELS[step]}
    </span>
  )
}
