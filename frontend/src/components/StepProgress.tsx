import type { JobStep } from '../api/types'
import { STEP_LABELS, STEP_ORDER } from '../api/types'
import clsx from 'clsx'
import { Check, X, Loader } from 'lucide-react'

const ALL_STEPS: JobStep[] = ['replicating', 'restoring', 'creating_image', 'exporting', 'completed']

export default function StepProgress({ step, crossRegion }: { step: JobStep; crossRegion: boolean }) {
  const steps = crossRegion ? ALL_STEPS : ALL_STEPS.filter((s) => s !== 'replicating')
  const failed = step === 'failed'
  const currentIdx = STEP_ORDER.indexOf(step)

  return (
    <div className="space-y-3">
      {steps.map((s, idx) => {
        const stepIdx = STEP_ORDER.indexOf(s)
        const isDone = !failed && stepIdx < currentIdx
        const isCurrent = !failed && stepIdx === currentIdx
        const isFailed = failed && stepIdx === currentIdx

        return (
          <div key={s} className="flex items-center gap-3">
            <div
              className={clsx(
                'w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold',
                isDone && 'bg-green-500 text-white',
                isCurrent && 'bg-blue-500 text-white',
                isFailed && 'bg-red-500 text-white',
                !isDone && !isCurrent && !isFailed && 'bg-gray-200 text-gray-500',
              )}
            >
              {isDone && <Check className="w-4 h-4" />}
              {isCurrent && <Loader className="w-4 h-4 animate-spin" />}
              {isFailed && <X className="w-4 h-4" />}
              {!isDone && !isCurrent && !isFailed && idx + 1}
            </div>
            <span
              className={clsx(
                'text-sm font-medium',
                isDone && 'text-gray-500',
                isCurrent && 'text-blue-600',
                isFailed && 'text-red-600',
                !isDone && !isCurrent && !isFailed && 'text-gray-400',
              )}
            >
              {STEP_LABELS[s]}
            </span>
          </div>
        )
      })}
    </div>
  )
}
