import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchHealth } from './api'
import { AlertFeed } from './components/AlertFeed'
import { ChatPanel } from './components/ChatPanel'
import { IncidentTimeline } from './components/IncidentTimeline'
import { MemoryInspector } from './components/MemoryInspector'
import { ProposalCard } from './components/ProposalCard'
import { createMockIncidentClient } from './mock/incidentClient'
import type { IncidentClient } from './incidentClient'
import type { HealthResponse, IncidentResponse, MemoryTrace } from './types'
import { INCIDENT_STATE_LABEL, INCIDENT_TYPE_LABEL } from './types'

export default function App() {
  // The only place the data source is chosen. Phase 3 swaps this line for the
  // live HTTP client; nothing else in the UI needs to change.
  const [client] = useState<IncidentClient>(() => createMockIncidentClient())

  const [incidents, setIncidents] = useState<IncidentResponse[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [incident, setIncident] = useState<IncidentResponse | null>(null)
  const [traces, setTraces] = useState<MemoryTrace[]>([])
  const [health, setHealth] = useState<HealthResponse | null>(null)

  const [loadingList, setLoadingList] = useState(true)
  const [loadingIncident, setLoadingIncident] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const list = await client.listIncidents(controller.signal)
        setIncidents(list)
        setSelectedId((current) => current ?? list[0]?.incident_id ?? null)
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        if (!controller.signal.aborted) setLoadingList(false)
      }
    })()
    return () => controller.abort()
  }, [client])

  // System status is a backend concern; a mock-data UI still needs to say which
  // models the real system is configured with.
  useEffect(() => {
    const controller = new AbortController()
    void fetchHealth(controller.signal)
      .then(setHealth)
      .catch(() => setHealth(null))
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!selectedId) return
    const controller = new AbortController()
    setLoadingIncident(true)
    void (async () => {
      try {
        const [loadedIncident, loadedTraces] = await Promise.all([
          client.getIncident(selectedId, controller.signal),
          client.listMemoryTraces(selectedId, controller.signal),
        ])
        setIncident(loadedIncident)
        setTraces(loadedTraces)
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        if (!controller.signal.aborted) setLoadingIncident(false)
      }
    })()
    return () => controller.abort()
  }, [client, selectedId])

  const handleSend = useCallback(
    async (message: string) => {
      if (!selectedId) return
      setSending(true)
      try {
        const updated = await client.postChatMessage(selectedId, message)
        setIncident(updated)
        setIncidents((current) =>
          current.map((item) => (item.incident_id === updated.incident_id ? updated : item)),
        )
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setSending(false)
      }
    },
    [client, selectedId],
  )

  const proposals = useMemo(
    () =>
      [incident?.proposed_diagnosis, incident?.proposed_resolution].filter(
        (proposal) => proposal !== null && proposal !== undefined,
      ),
    [incident],
  )

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/40">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-6 py-4">
          <h1 className="text-lg font-semibold tracking-tight">DejaOps</h1>
          <span className="text-sm text-slate-500">NimbusPay on-call</span>

          {client.kind === 'mock' && (
            <span
              data-testid="data-source"
              className="rounded bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold tracking-wide text-amber-300 uppercase ring-1 ring-amber-500/30 ring-inset"
            >
              mock data
            </span>
          )}

          {health && (
            <span className="ml-auto font-mono text-xs text-slate-500">
              {health.bank_id} · {health.model_primary}
            </span>
          )}
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-6 py-6 lg:grid-cols-[20rem_1fr]">
        <AlertFeed
          incidents={incidents}
          selectedId={selectedId}
          onSelect={setSelectedId}
          loading={loadingList}
          error={error}
        />

        {incident ? (
          <div className="flex flex-col gap-5">
            <section className="rounded-xl bg-slate-900 p-5 ring-1 ring-slate-800 ring-inset">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                <span className="font-mono">{incident.incident_id}</span>
                <span aria-hidden="true">·</span>
                <span className="font-mono">{incident.alert.service}</span>
                <span aria-hidden="true">·</span>
                <span>{INCIDENT_TYPE_LABEL[incident.alert.incident_type]}</span>
                <span className="ml-auto rounded bg-slate-800 px-2 py-0.5 font-medium text-slate-300">
                  {INCIDENT_STATE_LABEL[incident.state]}
                </span>
              </div>

              <h2 className="mt-2 text-xl font-semibold tracking-tight">{incident.alert.title}</h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">
                {incident.alert.summary}
              </p>

              {incident.alert.error_samples.length > 0 && (
                <ul className="mt-3 flex flex-col gap-1">
                  {incident.alert.error_samples.map((sample) => (
                    <li
                      key={sample}
                      className="truncate rounded bg-slate-950 px-2 py-1 font-mono text-xs text-slate-500"
                    >
                      {sample}
                    </li>
                  ))}
                </ul>
              )}

              {incident.operator_outcome && (
                <p className="mt-3 rounded-md bg-slate-800/60 px-3 py-2 text-xs text-slate-300">
                  Operator outcome: <strong>{incident.operator_outcome}</strong>
                  {incident.operator ? ` — ${incident.operator}` : ''}
                </p>
              )}
            </section>

            {loadingIncident && <p className="text-sm text-slate-500">Loading incident…</p>}

            {proposals.map((proposal) => (
              <ProposalCard key={proposal.kind} proposal={proposal} />
            ))}

            {!incident.proposed_diagnosis && !incident.proposed_resolution && (
              <p className="rounded-xl border border-dashed border-slate-800 p-4 text-sm text-slate-500">
                The agent has not proposed a diagnosis or a resolution for this incident.
              </p>
            )}

            <section className="rounded-xl bg-slate-900/40 p-5 ring-1 ring-slate-800 ring-inset">
              <h2 className="mb-3 text-xs font-semibold tracking-widest text-slate-400 uppercase">
                Timeline
              </h2>
              <IncidentTimeline entries={incident.timeline} />
            </section>

            <div className="rounded-xl bg-slate-900/40 p-5 ring-1 ring-slate-800 ring-inset">
              <ChatPanel incident={incident} onSend={handleSend} sending={sending} />
            </div>

            <div className="rounded-xl bg-slate-900/40 p-5 ring-1 ring-slate-800 ring-inset">
              <MemoryInspector
                incident={incident}
                traces={traces}
                loading={loadingIncident}
                error={null}
              />
            </div>
          </div>
        ) : (
          !loadingList && <p className="text-sm text-slate-500">Select an incident to begin.</p>
        )}
      </main>
    </div>
  )
}
