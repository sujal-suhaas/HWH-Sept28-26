import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchCatalog, fetchHealth } from './api'
import { AlertComposer } from './components/AlertComposer'
import { AlertFeed } from './components/AlertFeed'
import { ChatPanel } from './components/ChatPanel'
import { FeedbackControls } from './components/FeedbackControls'
import { IncidentTimeline } from './components/IncidentTimeline'
import { MemoryInspector } from './components/MemoryInspector'
import { ProposalCard } from './components/ProposalCard'
import { createLiveIncidentClient } from './live/incidentClient'
import { createMockIncidentClient } from './mock/incidentClient'
import type { IncidentClient } from './incidentClient'
import type {
  AlertPayload,
  CatalogResponse,
  FeedbackRequest,
  HealthResponse,
  IncidentResponse,
  MemoryMode,
  MemoryTrace,
} from './types'
import { INCIDENT_STATE_LABEL, INCIDENT_TYPE_LABEL } from './types'

/** The operator whose outcomes are recorded. Feedback is attributed to a person. */
const OPERATOR = 'm.iyer'

/**
 * Which data source to use.
 *
 * Live by default: the whole point is a real run against real memory. The mock
 * is opt-in (`?source=mock`) so a failure to reach the backend surfaces as an
 * error instead of being silently papered over with fixtures.
 */
function selectClient(): IncidentClient {
  const requested = new URLSearchParams(window.location.search).get('source')
  return requested === 'mock' ? createMockIncidentClient() : createLiveIncidentClient()
}

export default function App() {
  // The only place the data source is chosen.
  const [client] = useState<IncidentClient>(selectClient)

  const [incidents, setIncidents] = useState<IncidentResponse[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [incident, setIncident] = useState<IncidentResponse | null>(null)
  const [traces, setTraces] = useState<MemoryTrace[]>([])
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null)

  const [memoryMode, setMemoryMode] = useState<MemoryMode>('on')

  const [loadingList, setLoadingList] = useState(true)
  const [loadingIncident, setLoadingIncident] = useState(false)
  const [sending, setSending] = useState(false)
  const [opening, setOpening] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [openError, setOpenError] = useState<string | null>(null)
  const [feedbackError, setFeedbackError] = useState<string | null>(null)

  const refreshList = useCallback(
    async (signal?: AbortSignal) => {
      const list = await client.listIncidents(signal)
      setIncidents(list)
      return list
    },
    [client],
  )

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const list = await refreshList(controller.signal)
        setSelectedId((current) => current ?? list[0]?.incident_id ?? null)
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        if (!controller.signal.aborted) setLoadingList(false)
      }
    })()
    return () => controller.abort()
  }, [refreshList])

  useEffect(() => {
    const controller = new AbortController()
    void fetchHealth(controller.signal)
      .then(setHealth)
      .catch(() => setHealth(null))
    void fetchCatalog(controller.signal)
      .then(setCatalog)
      .catch(() => setCatalog(null))
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

  /** Apply an incident the backend just returned, without a second round trip. */
  const adopt = useCallback((updated: IncidentResponse) => {
    setIncident(updated)
    setIncidents((current) =>
      current.map((item) => (item.incident_id === updated.incident_id ? updated : item)),
    )
  }, [])

  const handleOpen = useCallback(
    async (alert: AlertPayload) => {
      setOpening(true)
      setOpenError(null)
      try {
        const created = await client.openIncident(alert, memoryMode)
        adopt(created)
        setSelectedId(created.incident_id)
        setTraces(await client.listMemoryTraces(created.incident_id))
        setIncidents(await refreshList())
      } catch (cause) {
        setOpenError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setOpening(false)
      }
    },
    [adopt, client, memoryMode, refreshList],
  )

  const handleSend = useCallback(
    async (message: string) => {
      if (!selectedId) return
      setSending(true)
      try {
        adopt(await client.postChatMessage(selectedId, message))
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setSending(false)
      }
    },
    [adopt, client, selectedId],
  )

  const handleFeedback = useCallback(
    async (feedback: FeedbackRequest) => {
      if (!selectedId) return
      setSubmitting(true)
      setFeedbackError(null)
      try {
        const updated = await client.postFeedback(selectedId, feedback)
        adopt(updated)
        // Feedback can retain memory and add retain traces, so re-read them.
        setTraces(await client.listMemoryTraces(selectedId))
      } catch (cause) {
        setFeedbackError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setSubmitting(false)
      }
    },
    [adopt, client, selectedId],
  )

  /** Run the same alert again in the other memory mode. The comparison, in a click. */
  const handleRerun = useCallback(
    async (mode: MemoryMode) => {
      if (!incident) return
      setOpening(true)
      setOpenError(null)
      try {
        const created = await client.openIncident(incident.alert, mode)
        adopt(created)
        setSelectedId(created.incident_id)
        setTraces(await client.listMemoryTraces(created.incident_id))
        setIncidents(await refreshList())
      } catch (cause) {
        setOpenError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setOpening(false)
      }
    },
    [adopt, client, incident, refreshList],
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

          <span
            data-testid="data-source"
            className={`rounded px-2 py-0.5 text-[11px] font-semibold tracking-wide uppercase ring-1 ring-inset ${
              client.kind === 'mock'
                ? 'bg-amber-500/15 text-amber-300 ring-amber-500/30'
                : 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30'
            }`}
          >
            {client.label}
          </span>

          {health && (
            <span className="ml-auto font-mono text-xs text-slate-500">
              {health.bank_id} · {health.model_primary}
            </span>
          )}
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl grid-cols-1 gap-6 px-6 py-6 lg:grid-cols-[22rem_1fr]">
        <div className="flex flex-col gap-5">
          <div className="rounded-xl bg-slate-900/40 p-4 ring-1 ring-slate-800 ring-inset">
            <AlertComposer
              memoryMode={memoryMode}
              onMemoryModeChange={setMemoryMode}
              onOpen={handleOpen}
              opening={opening}
              error={openError}
            />
          </div>

          <AlertFeed
            incidents={incidents}
            selectedId={selectedId}
            onSelect={setSelectedId}
            loading={loadingList}
            error={error}
          />
        </div>

        {incident ? (
          <div className="flex flex-col gap-5">
            <section className="rounded-xl bg-slate-900 p-5 ring-1 ring-slate-800 ring-inset">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                <span className="font-mono">{incident.incident_id}</span>
                <span aria-hidden="true">·</span>
                <span className="font-mono">{incident.alert.service}</span>
                <span aria-hidden="true">·</span>
                <span>{INCIDENT_TYPE_LABEL[incident.alert.incident_type]}</span>
                <span
                  data-testid="incident-memory-mode"
                  className={`rounded px-2 py-0.5 font-medium ${
                    incident.memory_mode === 'on'
                      ? 'bg-emerald-500/15 text-emerald-300'
                      : 'bg-slate-700 text-slate-300'
                  }`}
                >
                  memory {incident.memory_mode}
                </span>
                <span
                  data-testid="incident-state"
                  className="ml-auto rounded bg-slate-800 px-2 py-0.5 font-medium text-slate-300"
                >
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
                <p
                  data-testid="operator-outcome"
                  className="mt-3 rounded-md bg-slate-800/60 px-3 py-2 text-xs text-slate-300"
                >
                  Operator outcome: <strong>{incident.operator_outcome}</strong>
                  {incident.operator ? ` — ${incident.operator}` : ''}
                  {incident.root_cause_id ? ` · root cause ${incident.root_cause_id}` : ''}
                  {incident.validated_runbook_id
                    ? ` · validated runbook ${incident.validated_runbook_id}`
                    : ''}
                </p>
              )}

              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-800 pt-3">
                <span className="text-xs text-slate-500">
                  Same alert, other memory mode — this is the comparison:
                </span>
                <button
                  type="button"
                  disabled={opening}
                  onClick={() => handleRerun(incident.memory_mode === 'on' ? 'off' : 'on')}
                  className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700 disabled:cursor-not-allowed disabled:text-slate-500"
                >
                  {opening
                    ? 'Running…'
                    : `Re-run with memory ${incident.memory_mode === 'on' ? 'off' : 'on'}`}
                </button>
              </div>
            </section>

            {loadingIncident && <p className="text-sm text-slate-500">Loading incident…</p>}

            {proposals.map((proposal) => (
              <ProposalCard
                key={proposal.kind}
                proposal={proposal}
                operatorOutcome={incident.operator_outcome}
              />
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
              <FeedbackControls
                incident={incident}
                catalog={catalog}
                onSubmit={handleFeedback}
                submitting={submitting}
                error={feedbackError}
                operator={OPERATOR}
              />
            </div>

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
          !loadingList && (
            <p className="text-sm text-slate-500">
              No incidents yet. Open one from the alert panel to see the agent work.
            </p>
          )
        )}
      </main>
    </div>
  )
}
