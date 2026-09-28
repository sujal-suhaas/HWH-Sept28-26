import type { TimelineActor, TimelineEntry } from '../types'

const ACTOR_CLASSES: Record<TimelineActor, string> = {
  pager: 'bg-rose-400',
  agent: 'bg-sky-400',
  operator: 'bg-emerald-400',
  system: 'bg-slate-500',
}

function formatTime(iso: string): string {
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toISOString().slice(11, 16)
}

export function IncidentTimeline({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-slate-500">No timeline entries yet.</p>
  }

  return (
    <ol className="flex flex-col gap-3">
      {entries.map((entry, index) => (
        <li key={`${entry.at}-${entry.event}-${index}`} className="flex gap-3">
          <span
            aria-hidden="true"
            className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${ACTOR_CLASSES[entry.actor]}`}
          />
          <div className="min-w-0">
            <p className="flex items-baseline gap-2 text-xs">
              <time className="font-mono text-slate-500">{formatTime(entry.at)}</time>
              <span className="font-medium text-slate-300">{entry.actor}</span>
              <span className="font-mono text-slate-500">{entry.event}</span>
            </p>
            <p className="mt-0.5 text-sm break-words text-slate-400">{entry.detail}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}
