import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProposalCard } from './ProposalCard'
import type { Proposal } from '../types'

function diagnosis(overrides: Partial<Proposal> = {}): Proposal {
  return {
    kind: 'diagnosis',
    status: 'proposed',
    content: 'Kafka consumer lag on payments-ledger',
    evidence_summary: 'Four prior incidents show the same signature.',
    cited_memory_ids: [],
    confidence: 'high',
    runbook_id: null,
    root_cause_id: null,
    proposed_by: 'agent',
    proposed_at: '2026-09-01T00:00:00Z',
    ...overrides,
  }
}

describe('ProposalCard root cause chip', () => {
  it('shows the cause the agent named', () => {
    render(<ProposalCard proposal={diagnosis({ root_cause_id: 'RC-007' })} operatorOutcome={null} />)
    expect(screen.getByTestId('proposal-root-cause').textContent).toBe('RC-007')
  })

  it('shows no cause chip when the agent named none', () => {
    render(<ProposalCard proposal={diagnosis()} operatorOutcome={null} />)
    expect(screen.queryByTestId('proposal-root-cause')).toBeNull()
  })

  it('shows the cause and the runbook as separate ids', () => {
    render(
      <ProposalCard
        proposal={diagnosis({ root_cause_id: 'RC-007', runbook_id: 'RB-018' })}
        operatorOutcome={null}
      />,
    )
    expect(screen.getByTestId('proposal-root-cause').textContent).toBe('RC-007')
    expect(screen.getByText('RB-018').textContent).toBe('RB-018')
  })

  it('still reads as awaiting confirmation while no outcome is recorded', () => {
    render(<ProposalCard proposal={diagnosis({ root_cause_id: 'RC-007' })} operatorOutcome={null} />)
    expect(screen.getByTestId('proposal-state').textContent).toBe('proposed')
    expect(screen.getByText(/Awaiting operator confirmation/).textContent).toMatch(
      /Awaiting operator confirmation/,
    )
  })

  it('does not present a failed resolution as a validated fix', () => {
    render(
      <ProposalCard
        proposal={diagnosis({
          kind: 'resolution',
          status: 'pending_confirmation',
          root_cause_id: null,
          runbook_id: 'RB-018',
        })}
        operatorOutcome="RESOLUTION_FAILED"
      />,
    )
    const state = screen.getByTestId('proposal-state')
    expect(state.textContent?.toLowerCase()).toContain('failed')
    expect(state.textContent?.toLowerCase()).toContain('not a validated fix')
  })
})
