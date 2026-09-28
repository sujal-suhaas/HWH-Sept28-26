import type { IncidentResponse, MemoryTrace } from '../types'
import { describeMemoryState, MemoryStatePill, memoryOffState } from './MemoryStatusBadge'

function TraceRow({ trace }: { trace: MemoryTrace }) {
  const state = describeMemoryState(trace)

  return (
    <li className="rounded-lg bg-slate-900/60 p-3 ring-1 ring-slate-800 ring-inset">
      <div className="flex flex-wrap items-center gap-2">
        <MemoryStatePill state={state} />
        <span className="font-mono text-xs text-slate-500">{trace.operation}</span>
        <span className="ml-auto font-mono text-xs text-slate-500">
          {trace.hit_count} hit(s) · {Math.round(trace.latency_ms)}ms · {trace.attempts} attempt(s)
        </span>
      </div>

      {trace.query && <p className="mt-2 text-xs break-words text-slate-400">{trace.query}</p>}

      {trace.no_match && trace.min_score !== null && (
        <p className="mt-1 font-mono text-[11px] text-slate-500">
          min_score {trace.min_score}
          {trace.min_score === 0 && ' (exact tag scope)'}
        </p>
      )}

      {trace.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {trace.tags.map((tag) => (
            <code key={tag} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-400">
              {tag}
            </code>
          ))}
        </div>
      )}

      <p className="mt-2 text-xs text-slate-500">{state.detail}</p>
    </li>
  )
}

export interface MemoryInspectorProps {
  incident: IncidentResponse
  traces: MemoryTrace[]
  loading: boolean
  error: string | null
}

/**
 * Shows what the memory layer actually did — including when it did nothing.
 *
 * A memory-off run has no traces by definition, so an empty list is rendered as
 * "Hindsight was not called" rather than as an absence of results. Those are
 * different claims and the UI must not blur them.
 */
export function MemoryInspector({ incident, traces, loading, error }: MemoryInspectorProps) {
  const memoryOff = incident.memory_mode === 'off'

  return (
    <section aria-label="Memory inspector" className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h2 className="text-xs font-semibold tracking-widest text-slate-400 uppercase">
          Memory inspector
        </h2>
        {memoryOff && <MemoryStatePill state={memoryOffState()} />}
      </div>

      {loading && <p className="text-sm text-slate-500">Loading memory traces…</p>}

      {error && (
        <p className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300 ring-1 ring-rose-500/30 ring-inset">
          {error}
        </p>
      )}

      {!loading && !error && memoryOff && (
        <p className="rounded-lg border border-dashed border-slate-800 p-3 text-sm text-slate-500">
          Hindsight was not called for this run. No recall and no retain happened, so there is no
          memory evidence to inspect — this is not a failed recall.
        </p>
      )}

      {!loading && !error && !memoryOff && traces.length === 0 && (
        <p className="rounded-lg border border-dashed border-slate-800 p-3 text-sm text-slate-500">
          No memory operations recorded for this incident yet.
        </p>
      )}

      {!loading && !error && traces.length > 0 && (
        <ul className="flex flex-col gap-2">
          {traces.map((trace) => (
            <TraceRow key={trace.trace_id} trace={trace} />
          ))}
        </ul>
      )}
    </section>
  )
}
