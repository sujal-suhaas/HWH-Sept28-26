import type { Proposal } from '../types'

const KIND_LABEL: Record<Proposal['kind'], string> = {
  diagnosis: 'Proposed diagnosis',
  resolution: 'Proposed resolution',
}

const CONFIDENCE_CLASSES: Record<NonNullable<Proposal['confidence']>, string> = {
  low: 'text-amber-300',
  medium: 'text-sky-300',
  high: 'text-emerald-300',
}

/**
 * A proposal is never an outcome.
 *
 * The card exists to make the operator-confirmation boundary visible: it says
 * "awaiting confirmation" and never "resolved", because only an operator
 * outcome can promote a proposal into authoritative memory.
 */
export function ProposalCard({ proposal }: { proposal: Proposal }) {
  const awaiting = proposal.status === 'pending_confirmation'
  const citations = proposal.cited_memory_ids

  return (
    <article
      data-testid="proposal-card"
      data-status={proposal.status}
      className="rounded-lg bg-slate-900 p-4 ring-1 ring-slate-800 ring-inset"
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-200">{KIND_LABEL[proposal.kind]}</h3>
        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-amber-300 uppercase ring-1 ring-amber-500/30 ring-inset">
          {awaiting ? 'pending confirmation' : 'proposed'}
        </span>
        {proposal.confidence && (
          <span className={`text-xs ${CONFIDENCE_CLASSES[proposal.confidence]}`}>
            confidence: {proposal.confidence}
          </span>
        )}
        {proposal.runbook_id && (
          <span className="ml-auto rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-slate-300">
            {proposal.runbook_id}
          </span>
        )}
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

        <p className="mt-2 text-xs text-slate-500">
          Awaiting operator confirmation. Nothing here is authoritative memory yet.
        </p>
      </footer>
    </article>
  )
}
