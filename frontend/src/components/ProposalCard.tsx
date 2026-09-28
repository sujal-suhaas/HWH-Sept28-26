import { proposalDisplayState } from '../proposalState'
import type { FeedbackType, Proposal } from '../types'

const KIND_LABEL: Record<Proposal['kind'], string> = {
  diagnosis: 'Proposed diagnosis',
  resolution: 'Proposed resolution',
}

const CONFIDENCE_CLASSES: Record<NonNullable<Proposal['confidence']>, string> = {
  low: 'text-amber-300',
  medium: 'text-sky-300',
  high: 'text-emerald-300',
}

const TONE_CLASSES: Record<string, string> = {
  pending: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  good: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  bad: 'bg-rose-500/15 text-rose-300 ring-rose-500/30',
  neutral: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
}

/**
 * A proposal is never an outcome.
 *
 * The card exists to make the operator-confirmation boundary visible. The
 * displayed state comes from the incident's operator outcome, not from the
 * agent's own `status` field, so a confirmed diagnosis stops saying "awaiting
 * confirmation" and a failed resolution never reads as a validated fix.
 */
export function ProposalCard({
  proposal,
  operatorOutcome,
}: {
  proposal: Proposal
  operatorOutcome: FeedbackType | null
}) {
  const state = proposalDisplayState(proposal, operatorOutcome)
  const citations = proposal.cited_memory_ids

  return (
    <article
      data-testid="proposal-card"
      data-status={proposal.status}
      data-outcome={state.label}
      className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800 ring-inset"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-200">{KIND_LABEL[proposal.kind]}</h3>
        <span
          data-testid="proposal-state"
          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase ring-1 ring-inset ${TONE_CLASSES[state.tone]}`}
        >
          {state.label}
        </span>
        {proposal.confidence && (
          <span className={`text-xs ${CONFIDENCE_CLASSES[proposal.confidence]}`}>
            confidence: {proposal.confidence}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          {proposal.root_cause_id && (
            <span
              data-testid="proposal-root-cause"
              className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-slate-300"
            >
              {proposal.root_cause_id}
            </span>
          )}
          {proposal.runbook_id && (
            <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-slate-300">
              {proposal.runbook_id}
            </span>
          )}
        </span>
      </header>

      <p className="mt-2.5 text-sm leading-relaxed text-slate-200">{proposal.content}</p>

      <p className="mt-2.5 text-xs leading-relaxed text-slate-400">{proposal.evidence_summary}</p>

      <footer className="mt-3 border-t border-slate-800 pt-2.5">
        {citations.length > 0 ? (
          <p className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
            <span>Cited memory</span>
            {citations.map((id) => (
              <code key={id} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-slate-300">
                {id}
              </code>
            ))}
          </p>
        ) : (
          <p className="text-xs text-slate-500">
            No memory cited — this proposal is not grounded in recalled history.
          </p>
        )}

        {state.awaitingOperator ? (
          <p className="mt-2 text-xs text-slate-500">
            Awaiting operator confirmation. Nothing here is authoritative memory yet.
          </p>
        ) : (
          <p className="mt-2 text-xs text-slate-500">
            Operator outcome recorded. Only that outcome — not this proposal — became memory.
          </p>
        )}
      </footer>
    </article>
  )
}
