/**
 * The HTTP layer.
 *
 * Two things here are load-bearing and easy to get wrong: the backend's two
 * error shapes must both reach the operator as readable text, and every route
 * the UI uses must hit the path the API actually defines.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, fetchCatalog, request } from './api'
import { createLiveIncidentClient } from './live/incidentClient'
import type { AlertPayload } from './types'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function stub(routes: Record<string, (init: RequestInit) => Response>) {
  const calls: Array<{ url: string; init: RequestInit }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = typeof input === 'string' ? input : input.toString()
      calls.push({ url, init })
      for (const [path, handler] of Object.entries(routes)) {
        if (url.endsWith(path)) return handler(init)
      }
      throw new Error(`unexpected request to ${url}`)
    }),
  )
  return calls
}

const ALERT: AlertPayload = {
  service: 'checkout-api',
  severity: 'p1',
  incident_type: 'latency',
  title: 'p99 above 2s',
  summary: 'Ledger confirmation calls are timing out.',
  source: 'pagerduty-sim',
  environment: 'prod',
  fired_at: '2026-09-22T15:00:00Z',
  error_samples: [],
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('error flattening', () => {
  it('uses a string detail verbatim', async () => {
    stub({ '/incidents/NOPE': () => json({ detail: "unknown incident 'NOPE'" }, 404) })

    await expect(request('/incidents/NOPE')).rejects.toThrow("unknown incident 'NOPE'")
  })

  it('names the field for FastAPI validation errors', async () => {
    stub({
      '/alerts': () =>
        json(
          {
            detail: [
              { loc: ['body', 'alert', 'service'], msg: 'must not be blank', type: 'value_error' },
              { loc: ['body', 'alert', 'title'], msg: 'Field required', type: 'missing' },
            ],
          },
          422,
        ),
    })

    await expect(request('/alerts', { method: 'POST', body: '{}' })).rejects.toThrow(
      'alert.service: must not be blank; alert.title: Field required',
    )
  })

  it('falls back to the status when the body is not JSON', async () => {
    stub({ '/health': () => new Response('<html>502</html>', { status: 502 }) })

    await expect(request('/health')).rejects.toThrow('request failed with HTTP 502')
  })

  it('carries the status so callers can branch on it', async () => {
    stub({ '/health': () => json({ detail: 'down' }, 503) })

    const error = await request('/health').catch((cause: unknown) => cause)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(503)
  })
})

describe('live client routes', () => {
  const incident = {
    incident_id: 'INC-9001',
    state: 'WAITING_FOR_OPERATOR',
    alert: ALERT,
    timeline: [],
    proposed_diagnosis: null,
    proposed_resolution: null,
    operator_outcome: null,
    operator: null,
    root_cause_id: null,
    validated_runbook_id: null,
    memory_mode: 'on',
    model_used: null,
    agent_status: null,
    memory_trace_ids: [],
    tool_trace_ids: [],
    created_at: '2026-09-22T15:00:00Z',
    updated_at: '2026-09-22T15:00:00Z',
  }

  it('is labelled live so the UI cannot show the mock badge', () => {
    const client = createLiveIncidentClient()
    expect(client.kind).toBe('live')
    expect(client.label).toBe('live api')
  })

  it('unwraps the incident list envelope', async () => {
    stub({ '/incidents': () => json({ count: 1, incidents: [incident] }) })

    expect(await createLiveIncidentClient().listIncidents()).toHaveLength(1)
  })

  it('unwraps the memory-trace envelope', async () => {
    stub({
      '/incidents/INC-9001/memory-trace': () =>
        json({ incident_id: 'INC-9001', memory_mode: 'on', count: 0, traces: [] }),
    })

    expect(await createLiveIncidentClient().listMemoryTraces('INC-9001')).toEqual([])
  })

  it('sends the memory mode with the alert, which is what makes ON/OFF comparable', async () => {
    const calls = stub({ '/alerts': () => json(incident, 201) })

    await createLiveIncidentClient().openIncident(ALERT, 'off')

    const body = JSON.parse(String(calls[0]!.init.body))
    expect(calls[0]!.init.method).toBe('POST')
    expect(body).toEqual({ alert: ALERT, memory_mode: 'off' })
  })

  it('posts feedback to the incident feedback route', async () => {
    const calls = stub({ '/incidents/INC-9001/feedback': () => json(incident) })

    await createLiveIncidentClient().postFeedback('INC-9001', {
      feedback_type: 'DIAGNOSIS_CONFIRMED',
      operator: 'm.iyer',
      root_cause_id: 'RC-001',
    })

    expect(calls[0]!.url).toMatch(/\/incidents\/INC-9001\/feedback$/)
    expect(JSON.parse(String(calls[0]!.init.body)).root_cause_id).toBe('RC-001')
  })

  it('encodes an incident id in the path', async () => {
    const calls = stub({ '/incidents/A%2FB': () => json(incident) })

    await createLiveIncidentClient().getIncident('A/B')

    expect(calls[0]!.url).toMatch(/\/incidents\/A%2FB$/)
  })

  it('fetches the catalog', async () => {
    stub({ '/catalog': () => json({ company: 'NimbusPay', services: [], root_causes: [], runbooks: [] }) })

    expect((await fetchCatalog()).company).toBe('NimbusPay')
  })
})
