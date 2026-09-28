import { useState } from 'react'
import { SENDABLE_ALERTS } from '../mock/fixtures'
import type { AlertPayload, MemoryMode } from '../types'
import { SEVERITY_LABEL } from '../types'

export interface AlertComposerProps {
  memoryMode: MemoryMode
  onMemoryModeChange: (mode: MemoryMode) => void
  onOpen: (alert: AlertPayload) => void
  opening: boolean
  error: string | null
}

/**
 * Open an incident from an alert, and choose the memory mode it runs in.
 *
 * The mode is per-request rather than a deployment setting, because the whole
 * comparison depends on running the *same* alert both ways. The toggle states
 * plainly what each mode will and will not do, so nobody has to infer it.
 */
export function AlertComposer({
  memoryMode,
  onMemoryModeChange,
  onOpen,
  opening,
  error,
}: AlertComposerProps) {
  const [index, setIndex] = useState(0)
  const alert = SENDABLE_ALERTS[index] ?? SENDABLE_ALERTS[0]!

  return (
    <section aria-label="Simulate alert" className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold tracking-widest text-slate-400 uppercase">
        Simulate alert
      </h2>

      <label className="flex flex-col gap-1 text-xs text-slate-400">
        Alert
        <select
          aria-label="Alert"
          value={index}
          onChange={(event) => setIndex(Number(event.target.value))}
          className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-800 ring-inset focus:ring-sky-500/50 focus:outline-none"
        >
          {SENDABLE_ALERTS.map((item, itemIndex) => (
            <option key={`${item.service}-${item.title}`} value={itemIndex}>
              {SEVERITY_LABEL[item.severity]} · {item.service} · {item.title}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="flex flex-col gap-1 rounded-lg bg-slate-900/60 p-3 ring-1 ring-slate-800 ring-inset">
        <legend className="px-1 text-xs text-slate-400">Memory mode for this run</legend>
        <div className="flex gap-1">
          {(['on', 'off'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={memoryMode === mode}
              data-testid={`memory-mode-${mode}`}
              onClick={() => onMemoryModeChange(mode)}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium ring-1 ring-inset transition ${
                memoryMode === mode
                  ? 'bg-sky-500/20 text-sky-200 ring-sky-500/40'
                  : 'bg-slate-900 text-slate-400 ring-slate-800 hover:text-slate-200'
              }`}
            >
              memory {mode}
            </button>
          ))}
        </div>
        <p data-testid="memory-mode-explainer" className="mt-1 text-[11px] leading-relaxed text-slate-500">
          {memoryMode === 'on'
            ? 'Hindsight is queried before diagnosis, and a confirmed outcome is written back to it.'
            : 'Hindsight is never called: no recall, no retain. The same alert and the same models are used, so only memory differs.'}
        </p>
      </fieldset>

      {error && (
        <p
          role="alert"
          data-testid="open-incident-error"
          className="rounded-md bg-rose-500/10 px-3 py-2 text-xs text-rose-300 ring-1 ring-rose-500/30 ring-inset"
        >
          {error}
        </p>
      )}

      <button
        type="button"
        disabled={opening}
        onClick={() => onOpen(alert)}
        className="rounded-lg bg-sky-500/90 px-4 py-2 text-sm font-medium text-slate-950 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
      >
        {opening ? 'Running the agent…' : `Open incident with memory ${memoryMode}`}
      </button>
    </section>
  )
}
