import { render, screen, waitFor } from '@testing-library/react'
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

const MEMORY = {
  trace_id: 't1',
  operation: 'health',
  mode: 'on',
  success: true,
  degraded: false,
  bank_id: 'dejaops-prod',
  hit_count: 0,
  latency_ms: 210,
  attempts: 1,
  error_code: 'none',
  error_message: null,
  query: null,
  tags: [],
  no_match: false,
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('App', () => {
  it('renders the product heading', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        const body = url.includes('/health/memory') ? MEMORY : HEALTH
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )

    render(<App />)

    expect(screen.getByRole('heading', { name: 'DejaOps' })).toBeTruthy()
    await waitFor(() => expect(screen.getByText('dejaops-prod')).toBeTruthy())
    expect(screen.getByText('openai/gpt-oss-120b')).toBeTruthy()
  })

  it('surfaces a backend failure instead of pretending everything is fine', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('connection refused')
      }),
    )

    render(<App />)

    await waitFor(() => expect(screen.getByText(/Backend unreachable/)).toBeTruthy())
  })
})
