import { useEffect, useMemo, useState, type FormEvent } from 'react'
import type { CatalogResponse, FeedbackRequest, FeedbackType, IncidentResponse } from '../types'
import { FEEDBACK_LABEL } from '../types'

/**
 * The six operator outcomes, in the order an incident produces them.
 *
 * Each one declares only the fields the backend actually requires, so the form
 * cannot ask for something the API ignores, or omit something it rejects.
 */
interface FeedbackSpec {
  requiresRootCause: boolean
  requiresCorrectedCause: boolean
  requiresFix: boolean
  offersRunbook: boolean
  /** Shown under the form so the operator knows what this writes to memory. */
  consequence: string
}

const SPECS: Record<FeedbackType, FeedbackSpec> = {
  DIAGNOSIS_CONFIRMED: {
    requiresRootCause: true,
    requiresCorrectedCause: false,
    requiresFix: false,
    offersRunbook: false,
    consequence: 'Writes a confirmed DIAGNOSIS memory. The agent hypothesis is kept alongside it.',
  },
  DIAGNOSIS_REJECTED: {
    requiresRootCause: false,
    requiresCorrectedCause: false,
    requiresFix: false,
    offersRunbook: false,
    consequence:
      'Writes a rejected DIAGNOSIS memory. The hypothesis is never recorded as a root cause.',
  },
  OPERATOR_CORRECTION: {
    requiresRootCause: false,
    requiresCorrectedCause: true,
    requiresFix: true,
    offersRunbook: true,
    consequence:
      'Writes an OPERATOR_CORRECTION memory. It overrides the agent proposal for this incident.',
  },
  RESOLUTION_CONFIRMED: {
    requiresRootCause: false,
    requiresCorrectedCause: false,
    requiresFix: true,
    offersRunbook: true,
    consequence:
      'Writes a confirmed RESOLUTION memory, and promotes the runbook to a RUNBOOK_ENTRY.',
  },
  RESOLUTION_FAILED: {
    requiresRootCause: false,
    requiresCorrectedCause: false,
    requiresFix: false,
    offersRunbook: false,
    consequence:
      'Writes a failed RESOLUTION memory so this fix is not proposed again. No runbook is promoted.',
  },
  INCONCLUSIVE: {
    requiresRootCause: false,
    requiresCorrectedCause: false,
    requiresFix: false,
    offersRunbook: false,
    consequence: 'Closes the incident as INCONCLUSIVE. Writes no resolution memory.',
  },
}

const ORDER: FeedbackType[] = [
  'DIAGNOSIS_CONFIRMED',
  'DIAGNOSIS_REJECTED',
  'OPERATOR_CORRECTION',
  'RESOLUTION_CONFIRMED',
  'RESOLUTION_FAILED',
  'INCONCLUSIVE',
]

export interface FeedbackControlsProps {
  incident: IncidentResponse
  catalog: CatalogResponse | null
  onSubmit: (feedback: FeedbackRequest) => void
  submitting: boolean
  /** A rejection from the backend, shown verbatim. */
  error: string | null
  operator: string
}

export function FeedbackControls({
  incident,
  catalog,
  onSubmit,
  submitting,
  error,
  operator,
}: FeedbackControlsProps) {
  const [feedbackType, setFeedbackType] = useState<FeedbackType>('DIAGNOSIS_CONFIRMED')
  const [rootCauseId, setRootCauseId] = useState('')
  const [correctedCause, setCorrectedCause] = useState('')
  const [fix, setFix] = useState('')
  const [runbookId, setRunbookId] = useState('')
  const [note, setNote] = useState('')

  const spec = SPECS[feedbackType]
  const proposedRunbookId = incident.proposed_resolution?.runbook_id ?? null
  const proposedRootCauseId = incident.proposed_diagnosis?.root_cause_id ?? null

  // The agent may name the suspected cause itself. That wins over the runbook's
  // cause, because it is what the agent actually proposed and it exists even when
  // no runbook was retrieved. The catalog remains the authority for the id space.
  useEffect(() => {
    if (proposedRootCauseId) setRootCauseId((current) => current || proposedRootCauseId)
  }, [proposedRootCauseId])

  // Otherwise prefill from what the agent already proposed, and from the runbook's
  // own root cause. The catalog is the authority for that mapping, so the operator
  // is not asked to look up an id the system already knows.
  useEffect(() => {
    if (proposedRunbookId) {
      setRunbookId((current) => current || proposedRunbookId)
      setFix((current) => current || incident.proposed_resolution?.content || '')
    }
  }, [proposedRunbookId, incident.proposed_resolution?.content])

  useEffect(() => {
    if (!runbookId || !catalog) return
    const runbook = catalog.runbooks.find((item) => item.id === runbookId)
    if (runbook) setRootCauseId((current) => current || runbook.root_cause_id)
  }, [runbookId, catalog])

  const rootCauses = useMemo(() => catalog?.root_causes ?? [], [catalog])
  const runbooks = useMemo(
    () => catalog?.runbooks.filter((item) => item.verified) ?? [],
    [catalog],
  )

  const resolved = incident.state === 'RESOLVED'

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting || resolved) return

    const feedback: FeedbackRequest = { feedback_type: feedbackType, operator }
    if (note.trim()) feedback.note = note.trim()
    if (spec.requiresRootCause && rootCauseId) feedback.root_cause_id = rootCauseId
    if (spec.requiresCorrectedCause && correctedCause.trim()) {
      feedback.corrected_root_cause = correctedCause.trim()
    }
    if (spec.requiresFix && fix.trim()) feedback.validated_fix = fix.trim()
    if (spec.offersRunbook && runbookId) feedback.validated_runbook_id = runbookId

    onSubmit(feedback)
  }

  return (
    <section aria-label="Operator feedback" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-xs font-semibold tracking-widest text-slate-400 uppercase">
          Operator outcome
        </h2>
        <span className="text-xs text-slate-500">
          Only this creates authoritative memory. The agent cannot.
        </span>
      </div>

      {resolved ? (
        <p className="rounded-lg border border-dashed border-slate-800 p-3 text-sm text-slate-400">
          This incident is RESOLVED. Its outcome is closed, so it cannot be changed — open a new
          incident instead. Reopening would rewrite history that memory has already learned from.
        </p>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Outcome
            <select
              aria-label="Outcome"
              value={feedbackType}
              onChange={(event) => setFeedbackType(event.target.value as FeedbackType)}
              className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-800 ring-inset focus:ring-sky-500/50 focus:outline-none"
            >
              {ORDER.map((type) => (
                <option key={type} value={type}>
                  {FEEDBACK_LABEL[type]}
                </option>
              ))}
            </select>
          </label>

          {spec.requiresRootCause && (
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              Confirmed root cause
              <select
                aria-label="Confirmed root cause"
                value={rootCauseId}
                onChange={(event) => setRootCauseId(event.target.value)}
                required
                className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-800 ring-inset focus:ring-sky-500/50 focus:outline-none"
              >
                <option value="">Select a root cause…</option>
                {rootCauses.map((cause) => (
                  <option key={cause.id} value={cause.id}>
                    {cause.id} — {cause.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {spec.requiresCorrectedCause && (
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              Actual root cause
              <textarea
                aria-label="Actual root cause"
                value={correctedCause}
                onChange={(event) => setCorrectedCause(event.target.value)}
                required
                rows={2}
                placeholder="What actually caused this, in your words"
                className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-800 ring-inset placeholder:text-slate-600 focus:ring-sky-500/50 focus:outline-none"
              />
            </label>
          )}

          {(spec.requiresFix || feedbackType === 'OPERATOR_CORRECTION') && (
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              {feedbackType === 'RESOLUTION_FAILED' ? 'Fix that failed' : 'Validated fix'}
              <textarea
                aria-label="Validated fix"
                value={fix}
                onChange={(event) => setFix(event.target.value)}
                required={spec.requiresFix}
                rows={2}
                className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-800 ring-inset placeholder:text-slate-600 focus:ring-sky-500/50 focus:outline-none"
              />
            </label>
          )}

          {spec.offersRunbook && (
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              Runbook
              <select
                aria-label="Runbook"
                value={runbookId}
                onChange={(event) => setRunbookId(event.target.value)}
                className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-800 ring-inset focus:ring-sky-500/50 focus:outline-none"
              >
                <option value="">No runbook</option>
                {runbooks.map((runbook) => (
                  <option key={runbook.id} value={runbook.id}>
                    {runbook.id} — {runbook.title}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Note
            <input
              aria-label="Note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Optional context for whoever reads this next"
              className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-800 ring-inset placeholder:text-slate-600 focus:ring-sky-500/50 focus:outline-none"
            />
          </label>

          <p data-testid="feedback-consequence" className="text-xs text-slate-500">
            {spec.consequence}
          </p>

          {incident.memory_mode === 'off' && (
            <p className="rounded-md bg-slate-800/60 px-3 py-2 text-xs text-slate-400">
              Memory is off for this incident, so this outcome will be recorded on the incident but
              written to no memory.
            </p>
          )}

          {error && (
            <p
              role="alert"
              data-testid="feedback-error"
              className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300 ring-1 ring-rose-500/30 ring-inset"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="self-start rounded-lg bg-sky-500/90 px-4 py-2 text-sm font-medium text-slate-950 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
          >
            {submitting ? 'Recording…' : `Record ${FEEDBACK_LABEL[feedbackType]}`}
          </button>
        </form>
      )}
    </section>
  )
}
