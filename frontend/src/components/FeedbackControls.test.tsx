import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FeedbackControls } from './FeedbackControls'
import type { CatalogResponse, IncidentResponse } from '../types'

const CATALOG: CatalogResponse = {
  company: 'NimbusPay',
  services: [],
  root_causes: [
    { id: 'RC-001', name: 'kafka_consumer_lag', summary: '', detail: '' },
    { id: 'RC-002', name: 'redis_failover_flap', summary: '', detail: '' },
  ],
  runbooks: [
    {
      id: 'RB-014',
      title: 'Scale the consumer group',
      root_cause_id: 'RC-001',
      steps: [],
      verified: true,
    },
  ],
}

function incidentWith(overrides: Partial<IncidentResponse>): IncidentResponse {
  return {
    incident_id: 'INC-1',
    service: 'checkout-api',
    severity: 'p1',
    incident_type: 'latency',
    environment: 'prod',
    state: 'WAITING_FOR_OPERATOR',
    alert: {
      service: 'checkout-api',
      severity: 'p1',
      incident_type: 'latency',
      title: 't',
      summary: 's',
      source: 'pagerduty-sim',
      environment: 'prod',
      fired_at: '2026-09-01T00:00:00Z',
      error_samples: [],
    },
    timeline: [],
    chat: [],
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
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  } as IncidentResponse
}

function renderForm(incident: IncidentResponse) {
  render(
    <FeedbackControls
      incident={incident}
      catalog={CATALOG}
      onSubmit={() => {}}
      submitting={false}
      error={null}
      operator="m.iyer"
    />,
  )
  return screen.getByRole('region', { name: 'Operator feedback' })
}

function rootCauseValue(form: HTMLElement): string {
  return (within(form).getByLabelText('Confirmed root cause') as HTMLSelectElement).value
}

describe('FeedbackControls prefill precedence', () => {
  it("prefers the cause the diagnosis named over the runbook's cause", () => {
    // They disagree on purpose. RB-014 treats RC-001, but the agent named RC-002.
    // What the agent actually proposed is what the operator is being asked to
    // confirm, so RC-002 must win.
    const form = renderForm(
      incidentWith({
        proposed_diagnosis: {
          kind: 'diagnosis',
          status: 'proposed',
          content: 'redis failover flap',
          evidence_summary: 'e',
          cited_memory_ids: [],
          confidence: 'medium',
          runbook_id: null,
          root_cause_id: 'RC-002',
          proposed_by: 'agent',
          proposed_at: '2026-09-01T00:00:00Z',
        },
        proposed_resolution: {
          kind: 'resolution',
          status: 'pending_confirmation',
          content: 'Scale the consumer group',
          evidence_summary: 'e',
          cited_memory_ids: [],
          confidence: null,
          runbook_id: 'RB-014',
          root_cause_id: null,
          proposed_by: 'agent',
          proposed_at: '2026-09-01T00:00:00Z',
        },
      }),
    )
    expect(rootCauseValue(form)).toBe('RC-002')
  })

  it("falls back to the runbook's cause when the diagnosis named none", () => {
    const form = renderForm(
      incidentWith({
        proposed_diagnosis: {
          kind: 'diagnosis',
          status: 'proposed',
          content: 'unknown',
          evidence_summary: 'e',
          cited_memory_ids: [],
          confidence: 'low',
          runbook_id: null,
          root_cause_id: null,
          proposed_by: 'agent',
          proposed_at: '2026-09-01T00:00:00Z',
        },
        proposed_resolution: {
          kind: 'resolution',
          status: 'pending_confirmation',
          content: 'Scale the consumer group',
          evidence_summary: 'e',
          cited_memory_ids: [],
          confidence: null,
          runbook_id: 'RB-014',
          root_cause_id: null,
          proposed_by: 'agent',
          proposed_at: '2026-09-01T00:00:00Z',
        },
      }),
    )
    expect(rootCauseValue(form)).toBe('RC-001')
  })

  it('leaves the cause empty when nothing named one', () => {
    const form = renderForm(incidentWith({}))
    expect(rootCauseValue(form)).toBe('')
  })
})
