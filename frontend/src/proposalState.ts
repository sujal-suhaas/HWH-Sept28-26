/**
 * What a proposal card should say, given the incident's operator outcome.
 *
 * `Proposal.status` is set by the agent and never changes afterwards, so it
 * cannot be the whole story: once an operator confirms, rejects or corrects an
 * outcome the card must stop saying "awaiting confirmation". The incident's
 * `operator_outcome` is the authoritative record, so the displayed state is
 * derived from it.
 *
 * The mapping is per proposal kind. A diagnosis outcome must never relabel the
 * resolution card, and a failed resolution must never read as a validated fix.
 */

import type { FeedbackType, IncidentResponse, Proposal, ProposalStatus } from './types'

export type ProposalTone = 'pending' | 'good' | 'bad' | 'neutral'

export interface ProposalDisplayState {
  status: ProposalStatus
  label: string
  tone: ProposalTone
  /** True while no operator outcome exists for this proposal's kind. */
  awaitingOperator: boolean
}

const DIAGNOSIS_OUTCOMES: Partial<Record<FeedbackType, ProposalDisplayState>> = {
  DIAGNOSIS_CONFIRMED: {
    status: 'proposed',
    label: 'confirmed by operator',
    tone: 'good',
    awaitingOperator: false,
  },
  DIAGNOSIS_REJECTED: {
    status: 'proposed',
    label: 'rejected by operator',
    tone: 'bad',
    awaitingOperator: false,
  },
  OPERATOR_CORRECTION: {
    // Deliberately not "confirmed": the model's hypothesis was wrong and the
    // operator supplied the real cause.
    status: 'proposed',
    label: 'corrected by operator',
    tone: 'bad',
    awaitingOperator: false,
  },
}

const RESOLUTION_OUTCOMES: Partial<Record<FeedbackType, ProposalDisplayState>> = {
  RESOLUTION_CONFIRMED: {
    status: 'pending_confirmation',
    label: 'confirmed by operator',
    tone: 'good',
    awaitingOperator: false,
  },
  RESOLUTION_FAILED: {
    status: 'pending_confirmation',
    label: 'failed — not a validated fix',
    tone: 'bad',
    awaitingOperator: false,
  },
}

function pending(proposal: Proposal): ProposalDisplayState {
  const awaiting = proposal.status === 'pending_confirmation'
  return {
    status: proposal.status,
    label: awaiting ? 'pending confirmation' : 'proposed',
    tone: 'pending',
    awaitingOperator: true,
  }
}

export function proposalDisplayState(
  proposal: Proposal,
  operatorOutcome: FeedbackType | null,
): ProposalDisplayState {
  if (operatorOutcome === null) return pending(proposal)

  if (operatorOutcome === 'INCONCLUSIVE') {
    return {
      status: proposal.status,
      label: 'inconclusive — no outcome confirmed',
      tone: 'neutral',
      awaitingOperator: false,
    }
  }

  const table = proposal.kind === 'diagnosis' ? DIAGNOSIS_OUTCOMES : RESOLUTION_OUTCOMES
  // An outcome for the other kind leaves this proposal exactly as it was.
  return table[operatorOutcome] ?? pending(proposal)
}

export function proposalDisplayStateFor(
  incident: IncidentResponse,
  proposal: Proposal,
): ProposalDisplayState {
  return proposalDisplayState(proposal, incident.operator_outcome)
}
