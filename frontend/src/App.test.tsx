import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const HEALTH = {
  status: 'ok',
  version: '0.1.0',
  memory_mode: 'on',
  bank_id: 'dejaops-prod',
  model_primary: 'openai/gpt-oss-120b',
  model_fallback: 'qwen/qwen3.8-27b',
}

function stubFetch(handler?: () => Promise<Response>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      handler ??
        (async () =>
          new Response(JSON.stringify(HEALTH), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })),
    ),
  )
}

function inspector(): HTMLElement {
  return screen.getByRole('region', { name: 'Memory inspector' })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('App', () => {
  it('renders the alert feed and labels the data source as mock', async () => {
    stubFetch()
    render(<App />)

    expect(screen.getByRole('heading', { name: 'DejaOps' })).toBeTruthy()
    expect(screen.getByTestId('data-source').textContent).toBe('mock data')

    expect(await screen.findByRole('button', { name: /checkout-api p99 latency/ })).toBeTruthy()
  })

  it('shows the selected incident with its proposals and timeline', async () => {
    stubFetch()
    render(<App />)

    const cards = await screen.findAllByTestId('proposal-card')
    expect(cards.map((card) => card.getAttribute('data-status'))).toEqual([
      'proposed',
      'pending_confirmation',
    ])

    const diagnosis = cards[0]!
    expect(within(diagnosis).getByText('Proposed diagnosis')).toBeTruthy()
    expect(within(diagnosis).getByText(/mem-inc1010-open/)).toBeTruthy()
    // A proposal must never be presented as an outcome.
    expect(within(diagnosis).getByText(/Awaiting operator confirmation/)).toBeTruthy()

    expect(screen.getByText('lookup_runbook')).toBeTruthy()
  })

  it('distinguishes a memory-off run from an empty or failed recall', async () => {
    stubFetch()
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: /fraud-scorer inference/ }))

    await waitFor(() =>
      expect(within(inspector()).getByText(/Hindsight was not called for this run/)).toBeTruthy(),
    )
    expect(within(inspector()).getByTestId('memory-state').getAttribute('data-state')).toBe(
      'memory off',
    )
  })

  it('renders a degraded recall as unavailable, not as no match', async () => {
    stubFetch()
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: /auth-service token validation/ }))

    await waitFor(() =>
      expect(within(inspector()).getAllByText('memory service unavailable').length).toBeGreaterThan(
        0,
      ),
    )
    expect(within(inspector()).queryByText('no relevant memory found')).toBeNull()
  })

  it('reports a successful recall with no hits as no relevant memory found', async () => {
    stubFetch()
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: /connection pool saturation/ }))

    await waitFor(() =>
      expect(within(inspector()).getByText('no relevant memory found')).toBeTruthy(),
    )
    // The threshold that produced the empty result is shown, not implied.
    expect(within(inspector()).getByText(/min_score 0\.2/)).toBeTruthy()
  })

  it('sends a chat message and renders the agent reply from the timeline', async () => {
    stubFetch()
    render(<App />)

    await screen.findAllByTestId('proposal-card')

    fireEvent.change(screen.getByLabelText('Message the agent'), {
      target: { value: 'Is this the ledger deploy again?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    // The message lands in the chat transcript and in the incident timeline.
    const chat = screen.getByRole('region', { name: 'Incident chat' })
    expect(await within(chat).findByText('Is this the ledger deploy again?')).toBeTruthy()
    expect(await within(chat).findByText(/My proposed diagnosis is/)).toBeTruthy()
    expect(await screen.findByText('chat_reply')).toBeTruthy()
  })

  it('survives a backend that is not running', async () => {
    stubFetch(async () => {
      throw new Error('connection refused')
    })
    render(<App />)

    // The incident workspace is mock-backed, so it still renders without a backend.
    expect((await screen.findAllByTestId('proposal-card')).length).toBe(2)
  })
})
