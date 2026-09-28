import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const HEALTH = {
  status: 'ok',
  version: '0.1.0',
  memory_mode: 'on',
  bank_id: 'dejaops-prod',
  model_primary: 'openai/gpt-oss-120b',
  model_fallback: 'qwen/qwen3.8-27b',
}

const CATALOG = {
  company: 'NimbusPay',
  services: [
    { name: 'checkout-api', tier: 1, owner: 'Checkout', dependencies: [], slo: 'p99 < 800ms' },
  ],
  root_causes: [
    { id: 'RC-001', name: 'kafka consumer lag', summary: 'Consumer group behind', detail: '' },
  ],
  runbooks: [
    {
      id: 'RB-014',
      title: 'Scale the ledger consumer group',
      root_cause_id: 'RC-001',
      steps: ['Scale the group'],
      verified: true,
    },
  ],
}

/** Routes by path so the catalog and health calls both behave like the backend. */
function stubFetch(overrides: Record<string, () => Promise<Response>> = {}) {
  const routes: Record<string, () => Promise<Response>> = {
    '/health': async () => json(HEALTH),
    '/catalog': async () => json(CATALOG),
    ...overrides,
  }

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      for (const [path, handler] of Object.entries(routes)) {
        if (url.includes(path)) return handler()
      }
      throw new Error(`unexpected request to ${url}`)
    }),
  )
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function inspector(): HTMLElement {
  return screen.getByRole('region', { name: 'Memory inspector' })
}

function feedback(): HTMLElement {
  return screen.getByRole('region', { name: 'Operator feedback' })
}

beforeEach(() => {
  // Live is the default; the component tests opt into the mock explicitly.
  window.history.replaceState({}, '', '?source=mock')
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('App', () => {
  it('labels the data source honestly and lists incidents', async () => {
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

    const chat = screen.getByRole('region', { name: 'Incident chat' })
    expect(await within(chat).findByText('Is this the ledger deploy again?')).toBeTruthy()
    expect(await within(chat).findByText(/My proposed diagnosis is/)).toBeTruthy()
    expect(await screen.findByText('chat_reply')).toBeTruthy()
  })

  it('survives a backend that is not running', async () => {
    stubFetch({
      '/health': async () => {
        throw new Error('connection refused')
      },
      '/catalog': async () => {
        throw new Error('connection refused')
      },
    })
    render(<App />)

    // The mock data source does not need the backend, so the workspace renders.
    expect((await screen.findAllByTestId('proposal-card')).length).toBe(2)
  })
})

describe('operator feedback', () => {
  /** The catalog fetch resolves after the incident, so prefill needs waiting for. */
  async function waitForCatalogPrefill() {
    await screen.findAllByTestId('proposal-card')
    await waitFor(() =>
      expect(
        (within(feedback()).getByLabelText('Confirmed root cause') as HTMLSelectElement).value,
      ).toBe('RC-001'),
    )
  }

  it('prefills the confirmed cause from the runbook the agent proposed', async () => {
    stubFetch()
    render(<App />)

    await waitForCatalogPrefill()
  })

  it('prefills the validated fix and runbook when confirming a resolution', async () => {
    stubFetch()
    render(<App />)

    await waitForCatalogPrefill()
    fireEvent.change(within(feedback()).getByLabelText('Outcome'), {
      target: { value: 'RESOLUTION_CONFIRMED' },
    })

    // RB-014 comes from the proposal; the fix text comes from the proposal too.
    await waitFor(() =>
      expect((within(feedback()).getByLabelText('Runbook') as HTMLSelectElement).value).toBe(
        'RB-014',
      ),
    )
    expect(
      (within(feedback()).getByLabelText('Validated fix') as HTMLTextAreaElement).value,
    ).toMatch(/Scale the payments-ledger consumer group/)
  })

  it('stops saying "awaiting confirmation" once an operator confirms', async () => {
    stubFetch()
    render(<App />)

    const cards = await screen.findAllByTestId('proposal-card')
    const diagnosis = cards[0]!
    // A diagnosis proposal is "proposed"; a resolution proposal is "pending
    // confirmation". Neither is an outcome.
    expect(within(diagnosis).getByTestId('proposal-state').textContent).toBe('proposed')
    expect(within(diagnosis).getByText(/Awaiting operator confirmation/)).toBeTruthy()

    await waitForCatalogPrefill()
    fireEvent.click(within(feedback()).getByRole('button', { name: /Record diagnosis confirmed/ }))

    await waitFor(() =>
      expect(within(diagnosis).getByTestId('proposal-state').textContent).toBe(
        'confirmed by operator',
      ),
    )
    expect(within(diagnosis).getByText(/Only that outcome/)).toBeTruthy()
    // The resolution proposal is untouched by a diagnosis outcome.
    expect(within(cards[1]!).getByTestId('proposal-state').textContent).toBe(
      'pending confirmation',
    )
    expect(screen.getByTestId('operator-outcome').textContent).toContain('DIAGNOSIS_CONFIRMED')
  })

  it('never renders a rejected diagnosis as a confirmed root cause', async () => {
    stubFetch()
    render(<App />)

    const [diagnosis] = await screen.findAllByTestId('proposal-card')
    await waitForCatalogPrefill()

    fireEvent.change(within(feedback()).getByLabelText('Outcome'), {
      target: { value: 'DIAGNOSIS_REJECTED' },
    })
    fireEvent.click(within(feedback()).getByRole('button', { name: /Record diagnosis rejected/ }))

    await waitFor(() =>
      expect(within(diagnosis!).getByTestId('proposal-state').textContent).toBe(
        'rejected by operator',
      ),
    )
    expect(screen.getByTestId('operator-outcome').textContent).not.toContain('root cause')
  })

  it('never renders a failed resolution as a validated fix', async () => {
    stubFetch()
    render(<App />)

    const cards = await screen.findAllByTestId('proposal-card')
    const resolution = cards[1]!
    await waitForCatalogPrefill()

    fireEvent.change(within(feedback()).getByLabelText('Outcome'), {
      target: { value: 'RESOLUTION_FAILED' },
    })
    fireEvent.click(within(feedback()).getByRole('button', { name: /Record resolution failed/ }))

    await waitFor(() =>
      expect(within(resolution).getByTestId('proposal-state').textContent).toBe(
        'failed — not a validated fix',
      ),
    )
    expect(screen.getByTestId('operator-outcome').textContent).not.toContain('validated runbook')
  })

  it('shows a rejected outcome verbatim instead of failing silently', async () => {
    stubFetch()
    render(<App />)

    await waitForCatalogPrefill()

    // Clear the prefilled root cause so the submission violates the contract.
    const rootCause = within(feedback()).getByLabelText('Confirmed root cause')
    fireEvent.change(rootCause, { target: { value: '' } })
    fireEvent.submit(rootCause.closest('form')!)

    const alert = await within(feedback()).findByRole('alert')
    expect(alert.textContent).toContain('requires root_cause_id or corrected_root_cause')
    // Nothing was recorded, so the proposal still awaits the operator.
    expect(screen.queryByTestId('operator-outcome')).toBeNull()
  })

  it('refuses to change an outcome on a resolved incident', async () => {
    stubFetch()
    render(<App />)

    await waitForCatalogPrefill()

    fireEvent.change(within(feedback()).getByLabelText('Outcome'), {
      target: { value: 'RESOLUTION_CONFIRMED' },
    })
    fireEvent.click(
      within(feedback()).getByRole('button', { name: /Record resolution confirmed/ }),
    )

    await waitFor(() =>
      expect(within(feedback()).getByText(/This incident is RESOLVED/)).toBeTruthy(),
    )
    expect(within(feedback()).queryByLabelText('Outcome')).toBeNull()
  })
})

describe('memory mode', () => {
  it('states which mode a run used, and what each mode does', async () => {
    stubFetch()
    render(<App />)

    await screen.findAllByTestId('proposal-card')

    expect(screen.getByTestId('incident-memory-mode').textContent).toBe('memory on')
    expect(screen.getByTestId('memory-mode-explainer').textContent).toMatch(/Hindsight is queried/)

    fireEvent.click(screen.getByTestId('memory-mode-off'))

    expect(screen.getByTestId('memory-mode-explainer').textContent).toMatch(
      /Hindsight is never called/,
    )
    expect(screen.getByTestId('memory-mode-off').getAttribute('aria-pressed')).toBe('true')
  })

  it('opens a new incident in the selected mode, leaving the other run intact', async () => {
    stubFetch()
    render(<App />)

    await screen.findAllByTestId('proposal-card')

    fireEvent.click(screen.getByTestId('memory-mode-off'))
    fireEvent.click(screen.getByRole('button', { name: /Open incident with memory off/ }))

    await waitFor(() =>
      expect(screen.getByTestId('incident-memory-mode').textContent).toBe('memory off'),
    )
    // The mock has no agent, so it must not pretend one ran.
    expect(screen.getByText('mock_no_memory')).toBeTruthy()
    expect(screen.queryAllByTestId('proposal-card')).toHaveLength(0)
  })

  it('re-runs the same alert in the other mode', async () => {
    stubFetch()
    render(<App />)

    await screen.findAllByTestId('proposal-card')
    expect(screen.getByTestId('incident-memory-mode').textContent).toBe('memory on')

    fireEvent.click(screen.getByRole('button', { name: /Re-run with memory off/ }))

    await waitFor(() =>
      expect(screen.getByTestId('incident-memory-mode').textContent).toBe('memory off'),
    )
  })
})
