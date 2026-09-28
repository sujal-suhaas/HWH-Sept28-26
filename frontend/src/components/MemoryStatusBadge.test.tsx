import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { MemoryTrace } from '../types'
import { describeMemoryState, MemoryStatusBadge } from './MemoryStatusBadge'

function trace(overrides: Partial<MemoryTrace> = {}): MemoryTrace {
  return {
    trace_id: 't1',
    operation: 'recall',
    mode: 'on',
    success: true,
    degraded: false,
    bank_id: 'dejaops-prod',
    hit_count: 0,
    latency_ms: 120,
    attempts: 1,
    error_code: 'none',
    error_message: null,
    query: 'checkout latency',
    tags: ['service:checkout-api'],
    no_match: false,
    ...overrides,
  }
}

describe('describeMemoryState', () => {
  it('reports recalled only when a recall returned hits', () => {
    expect(describeMemoryState(trace({ hit_count: 3, no_match: false })).label).toBe(
      'memory recalled',
    )
  })

  it('reports no relevant memory found for an empty successful recall', () => {
    expect(describeMemoryState(trace({ hit_count: 0, no_match: true })).label).toBe(
      'no relevant memory found',
    )
  })

  it('reports retained for a successful retain', () => {
    expect(
      describeMemoryState(trace({ operation: 'retain', hit_count: 1 })).label,
    ).toBe('memory retained')
  })

  it('reports unavailable for a failed call, never recalled', () => {
    const state = describeMemoryState(
      trace({ success: false, degraded: true, error_code: 'hindsight_unavailable' }),
    )
    expect(state.label).toBe('memory service unavailable')
    expect(state.tone).toBe('bad')
  })

  it('reports memory off distinctly from unavailable', () => {
    const state = describeMemoryState(
      trace({ mode: 'off', success: false, error_code: 'memory_off' }),
    )
    expect(state.label).toBe('memory off')
    expect(state.tone).toBe('neutral')
  })

  it('does not claim a memory was recalled when hits exist but the call failed', () => {
    const state = describeMemoryState(trace({ success: false, hit_count: 5 }))
    expect(state.label).toBe('memory service unavailable')
  })
})

describe('MemoryStatusBadge', () => {
  it('renders the state label', () => {
    render(<MemoryStatusBadge trace={trace({ hit_count: 2 })} />)
    expect(screen.getByTestId('memory-state').getAttribute('data-state')).toBe('memory recalled')
  })
})
