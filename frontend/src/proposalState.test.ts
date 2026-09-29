/**
 * The rule this pins: a proposal's displayed state follows the *incident's*
 * operator outcome, never the agent's own optimistic status. Getting this wrong
 * is how a UI ends up showing "awaiting confirmation" after an operator
 * confirmed, or "confirmed" after an operator rejected.
 */

import { describe, expect, it } from 'vitest'
import { outcomeForProposal, proposalDisplayState, proposalDisplayStateFor } from './proposalState'
import type { FeedbackType, IncidentResponse, Proposal } from './types'

const diagnosis: Proposal = {
  kind: 'diagnosis',
  status: 'proposed',
  content: 'Kafka consumer lag.',
  evidence_summary: 'Latency after the deploy.',
  cited_memory_ids: [],
  confidence: 'high',
  runbook_id: null,
  proposed_by: 'agent',
  proposed_at: '2026-09-22T15:00:00Z',
}

const resolution: Proposal = {
  ...diagnosis,
  kind: 'resolution',
  status: 'pending_confirmation',
  runbook_id: 'RB-014',
}

function stateFor(kind: 'diagnosis' | 'resolution', outcome: FeedbackType | null) {
  return proposalDisplayState(kind === 'diagnosis' ? diagnosis : resolution, outcome)
}

describe('proposalDisplayState', () => {
  it('awaits the operator when no outcome exists', () => {
    const state = stateFor('diagnosis', null)
    expect(state.awaitingOperator).toBe(true)
    expect(state.label).toBe('proposed')
    expect(stateFor('resolution', null).label).toBe('pending confirmation')
  })

  it('marks a confirmed diagnosis as confirmed, not as resolved', () => {
    const state = stateFor('diagnosis', 'DIAGNOSIS_CONFIRMED')
    expect(state.label).toBe('confirmed by operator')
    expect(state.awaitingOperator).toBe(false)
    // The proposal is still a proposal; only its display changed.
    expect(state.status).toBe('proposed')
  })

  it('never turns a rejected diagnosis into a confirmation', () => {
    const state = stateFor('diagnosis', 'DIAGNOSIS_REJECTED')
    expect(state.label).toBe('rejected by operator')
    expect(state.tone).toBe('bad')
  })

  it('calls a corrected hypothesis corrected, not confirmed', () => {
    expect(stateFor('diagnosis', 'OPERATOR_CORRECTION').label).toBe('corrected by operator')
  })

  it('never turns a failed resolution into a validated fix', () => {
    const state = stateFor('resolution', 'RESOLUTION_FAILED')
    expect(state.label).toBe('failed — not a validated fix')
    expect(state.label).not.toMatch(/confirmed/)
  })

  it('does not let a diagnosis outcome relabel the resolution', () => {
    expect(stateFor('resolution', 'DIAGNOSIS_CONFIRMED').awaitingOperator).toBe(true)
    expect(stateFor('resolution', 'OPERATOR_CORRECTION').awaitingOperator).toBe(true)
  })

  it('does not let a resolution outcome relabel the diagnosis', () => {
    expect(stateFor('diagnosis', 'RESOLUTION_CONFIRMED').awaitingOperator).toBe(true)
    expect(stateFor('diagnosis', 'RESOLUTION_FAILED').awaitingOperator).toBe(true)
  })

  it('marks both proposals inconclusive when the incident is inconclusive', () => {
    for (const kind of ['diagnosis', 'resolution'] as const) {
      const state = stateFor(kind, 'INCONCLUSIVE')
      expect(state.label).toBe('inconclusive — no outcome confirmed')
      expect(state.awaitingOperator).toBe(false)
    }
  })
})

describe('outcomeForProposal', () => {
  // Found on a live run: after the operator confirmed both the diagnosis and the
  // resolution, the resolved incident showed the diagnosis card back at
  // "proposed". `operator_outcome` holds only the most recent feedback, so the
  // resolution confirmation had overwritten the diagnosis one.
  const incident = (over: Partial<IncidentResponse>): IncidentResponse =>
    ({
      incident_id: 'INC-9001',
      state: 'RESOLVED',
      operator_outcome: 'RESOLUTION_CONFIRMED',
      diagnosis_outcome: 'DIAGNOSIS_CONFIRMED',
      resolution_outcome: 'RESOLUTION_CONFIRMED',
      ...over,
    }) as IncidentResponse

  it('reads the diagnosis slot for a diagnosis', () => {
    expect(outcomeForProposal(incident({}), diagnosis)).toBe('DIAGNOSIS_CONFIRMED')
  })

  it('reads the resolution slot for a resolution', () => {
    expect(outcomeForProposal(incident({}), resolution)).toBe('RESOLUTION_CONFIRMED')
  })

  it('does not fall back to the latest outcome when a slot is empty', () => {
    // A rejected-then-failed incident: the diagnosis slot is set, the resolution
    // slot is not. Reading `operator_outcome` would give the wrong answer here.
    const rejected = incident({
      operator_outcome: 'RESOLUTION_FAILED',
      diagnosis_outcome: 'DIAGNOSIS_REJECTED',
      resolution_outcome: 'RESOLUTION_FAILED',
    })
    expect(outcomeForProposal(rejected, diagnosis)).toBe('DIAGNOSIS_REJECTED')
  })

  it('leaves the diagnosis card alone when only the resolution was settled', () => {
    const onlyResolution = incident({
      diagnosis_outcome: null,
      resolution_outcome: 'RESOLUTION_CONFIRMED',
    })
    expect(proposalDisplayStateFor(onlyResolution, diagnosis).label).toBe('proposed')
    expect(proposalDisplayStateFor(onlyResolution, resolution).label).toBe('confirmed by operator')
  })
})
