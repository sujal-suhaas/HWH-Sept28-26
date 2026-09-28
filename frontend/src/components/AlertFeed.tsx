import type { IncidentResponse, Severity } from '../types'
import { INCIDENT_STATE_LABEL, SEVERITY_LABEL } from '../types'

const SEVERITY_CLASSES: Record<Severity, string> = {
  p1: 'bg-rose-500/15 text-rose-300 ring-rose-500/30',
  p2: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  p3: 'bg-sky-500/15 text-sky-300 ring-sky-500/30',
  p4: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${SEVERITY_CLASSES[severity]}`}
    >
      {SEVERITY_LABEL[severity]}
    </span>
  )
}

export interface AlertFeedProps {
  incidents: IncidentResponse[]
  selectedId: string | null
  onSelect: (incidentId: string) => void
  loading: boolean
  error: string | null
}

export function AlertFeed({ incidents, selectedId, onSelect, loading, error }: AlertFeedProps) {
  return (
    <section aria-label="Alert feed" className="flex flex-col gap-2">
      <h2 className="px-1 text-xs font-semibold tracking-widest text-slate-400 uppercase">
        Alert feed
      </h2>

      {loading && <p className="px-1 text-sm text-slate-500">Loading incidents…</p>}

      {error && (
        <p className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300 ring-1 ring-rose-500/30 ring-inset">
          {error}
        </p>
      )}

      {!loading && !error && incidents.length === 0 && (
        <p className="px-1 text-sm text-slate-500">No incidents.</p>
      )}

      <ul className="flex flex-col gap-1.5">
        {incidents.map((incident) => {
          const selected = incident.incident_id === selectedId
          return (
            <li key={incident.incident_id}>
              <button
                type="button"
                onClick={() => onSelect(incident.incident_id)}
                aria-current={selected ? 'true' : undefined}
                className={`w-full rounded-lg px-3 py-2.5 text-left ring-1 ring-inset transition ${
                  selected
                    ? 'bg-slate-800 ring-sky-500/40'
                    : 'bg-slate-900/60 ring-slate-800 hover:bg-slate-800/60'
                }`}
              >
                <div className="flex items-center gap-2">
                  <SeverityBadge severity={incident.alert.severity} />
                  <span className="font-mono text-xs text-slate-400">{incident.incident_id}</span>
                  {incident.memory_mode === 'off' && (
                    <span className="ml-auto rounded bg-slate-700/60 px-1.5 py-0.5 text-[10px] font-medium text-slate-300">
                      memory off
                    </span>
                  )}
                </div>
                <p className="mt-1.5 line-clamp-2 text-sm text-slate-200">{incident.alert.title}</p>
                <p className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                  <span className="font-mono">{incident.alert.service}</span>
                  <span aria-hidden="true">·</span>
                  <span>{INCIDENT_STATE_LABEL[incident.state]}</span>
                </p>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
