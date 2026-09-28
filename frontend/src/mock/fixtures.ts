/**
 * Mock incident fixtures.
 *
 * These are hand-written, not produced by the agent. Every one is labelled as
 * mock in the UI so nobody mistakes a fixture for a real run.
 *
 * Between them they exercise all five memory states the UI must distinguish
 * (AGENTS.md §4): recalled, no relevant memory found, memory service
 * unavailable, memory off, and retained.
 */

import type { AlertPayload, IncidentResponse, MemoryTrace } from '../types'

const DAY = '2026-09-22'
const at = (time: string) => `${DAY}T${time}:00Z`

/**
 * The demo scenario: checkout latency after a payments-ledger deploy, matching a
 * prior incident whose validated fix is runbook RB-014.
 */
const ledgerDeployLatency: IncidentResponse = {
  incident_id: 'INC-1038',
  state: 'WAITING_FOR_OPERATOR',
  alert: {
    service: 'checkout-api',
    severity: 'p1',
    incident_type: 'latency',
    title: 'checkout-api p99 latency above 2s',
    summary:
      'Checkout latency spiked to p99 3.4s immediately after the payments-ledger deploy. Ledger confirmation calls are timing out.',
    source: 'pagerduty-sim',
    environment: 'prod',
    fired_at: at('15:00'),
    error_samples: [
      'checkout_request_duration_p99 3.41',
      'upstream timeout waiting for payments-ledger confirmation',
      'ledger_confirmation_timeout_total 1842',
    ],
  },
  timeline: [
    {
      at: at('15:00'),
      actor: 'pager',
      event: 'alert_fired',
      detail: 'p99 3.41s against an 800ms SLO on checkout-api',
    },
    {
      at: at('15:01'),
      actor: 'system',
      event: 'incident_opened',
      detail: 'normalized alert retained as INCIDENT_OPEN memory',
    },
    {
      at: at('15:02'),
      actor: 'agent',
      event: 'recall',
      detail: 'recalled 5 memories for checkout-api (latency)',
    },
    {
      at: at('15:02'),
      actor: 'agent',
      event: 'lookup_runbook',
      detail: 'found validated runbook RB-014 for the payments-ledger consumer group',
    },
    {
      at: at('15:03'),
      actor: 'agent',
      event: 'propose_diagnosis',
      detail: 'Kafka consumer lag on payments-ledger after the deploy (confidence: high)',
    },
    {
      at: at('15:04'),
      actor: 'agent',
      event: 'propose_resolution',
      detail: 'scale the consumer group and replay the affected partition (RB-014)',
    },
    {
      at: at('15:06'),
      actor: 'operator',
      event: 'acknowledged',
      detail: 'm.iyer picked up the incident',
    },
  ],
  proposed_diagnosis: {
    kind: 'diagnosis',
    status: 'proposed',
    content:
      'Kafka consumer lag on the payments-ledger topic after the v1.8.3 deploy, which pushed checkout confirmation calls past their timeout.',
    evidence_summary:
      'p99 latency spiked immediately after the payments-ledger deploy, and four prior incidents on this service show the same ledger-confirmation-timeout signature.',
    cited_memory_ids: ['mem-inc1010-open', 'mem-inc1017-postmortem', 'mem-inc1038-res'],
    confidence: 'high',
    root_cause_id: 'RC-001',
    runbook_id: null,
    proposed_by: 'agent',
    proposed_at: at('15:03'),
  },
  proposed_resolution: {
    kind: 'resolution',
    status: 'pending_confirmation',
    content:
      'Scale the payments-ledger consumer group from 4 to 12 replicas, then replay the affected partitions from the last committed offset.',
    evidence_summary:
      'Matches validated runbook RB-014, which resolved the same signature in INC-1017 and INC-1024.',
    cited_memory_ids: ['mem-inc1017-res', 'mem-rb014'],
    confidence: 'high',
    runbook_id: 'RB-014',
    proposed_by: 'agent',
    proposed_at: at('15:04'),
  },
  operator_outcome: null,
  operator: 'm.iyer',
  root_cause_id: null,
  validated_runbook_id: null,
  memory_mode: 'on',
  model_used: 'openai/gpt-oss-120b',
  agent_status: 'completed',
  memory_trace_ids: ['trace-mock-1038-a', 'trace-mock-1038-b', 'trace-mock-1038-c'],
  tool_trace_ids: ['tool-mock-1038-1', 'tool-mock-1038-2'],
  created_at: at('15:01'),
  updated_at: at('15:06'),
}

/**
 * Memory ON, recall succeeded, nothing cleared the relevance threshold. The UI
 * must say "no relevant memory found" rather than inventing a precedent.
 */
const novelSaturation: IncidentResponse = {
  incident_id: 'INC-1044',
  state: 'INCONCLUSIVE',
  alert: {
    service: 'checkout-api',
    severity: 'p2',
    incident_type: 'saturation',
    title: 'checkout-api connection pool saturation',
    summary:
      'Connection pool waiters queued to 90% of the pool ceiling. No deploy in the preceding 24 hours and no ledger deploy correlation.',
    source: 'pagerduty-sim',
    environment: 'prod',
    fired_at: at('09:14'),
    error_samples: ['db_pool_waiters 180', 'db_pool_utilization 0.9'],
  },
  timeline: [
    { at: at('09:14'), actor: 'pager', event: 'alert_fired', detail: 'pool utilization 0.9' },
    { at: at('09:15'), actor: 'system', event: 'incident_opened', detail: 'normalized alert retained' },
    {
      at: at('09:16'),
      actor: 'agent',
      event: 'recall',
      detail: 'no relevant memory found for checkout-api (saturation)',
    },
    {
      at: at('09:17'),
      actor: 'agent',
      event: 'propose_diagnosis',
      detail: 'unable to ground a diagnosis; confidence low',
    },
    {
      at: at('09:31'),
      actor: 'operator',
      event: 'marked_inconclusive',
      detail: 's.rao: pool ceiling raised manually, cause not established',
    },
  ],
  proposed_diagnosis: {
    kind: 'diagnosis',
    status: 'proposed',
    content:
      'Pool exhaustion of unknown origin. No deploy or dependency change correlates with the onset.',
    evidence_summary:
      'No matching historical incident was found for this service and symptom, so this diagnosis is ungrounded.',
    cited_memory_ids: [],
    confidence: 'low',
    // Deliberately null: the mock's ungrounded diagnosis names no cause, so the
    // feedback form cannot prefill one and the operator must supply it.
    root_cause_id: null,
    runbook_id: null,
    proposed_by: 'agent',
    proposed_at: at('09:17'),
  },
  proposed_resolution: null,
  operator_outcome: 'INCONCLUSIVE',
  operator: 's.rao',
  root_cause_id: null,
  validated_runbook_id: null,
  memory_mode: 'on',
  model_used: 'openai/gpt-oss-120b',
  agent_status: 'completed',
  memory_trace_ids: ['trace-mock-1044-a'],
  tool_trace_ids: ['tool-mock-1044-1'],
  created_at: at('09:15'),
  updated_at: at('09:31'),
}

/**
 * Hindsight is down. The run degrades and says so — it must not report "no
 * relevant memory found", which would be a different and false claim.
 */
const degradedRecall: IncidentResponse = {
  incident_id: 'INC-1051',
  state: 'DIAGNOSING',
  alert: {
    service: 'auth-service',
    severity: 'p3',
    incident_type: 'error_rate',
    title: 'auth-service token validation error rate 4%',
    summary:
      'Token validation is returning 500s for a subset of requests. TLS handshake errors appear in the edge logs.',
    source: 'pagerduty-sim',
    environment: 'prod',
    fired_at: at('11:42'),
    error_samples: ['auth_validate_5xx_rate 0.04', 'x509: certificate has expired or is not yet valid'],
  },
  timeline: [
    { at: at('11:42'), actor: 'pager', event: 'alert_fired', detail: '5xx rate 4% on token validation' },
    { at: at('11:43'), actor: 'system', event: 'incident_opened', detail: 'normalized alert retained' },
    {
      at: at('11:44'),
      actor: 'agent',
      event: 'recall',
      detail: 'memory service unavailable: recall failed after 3 attempts (hindsight_unavailable)',
    },
    {
      at: at('11:45'),
      actor: 'system',
      event: 'degraded',
      detail: 'run continued in degraded mode; no memory-derived context available',
    },
  ],
  proposed_diagnosis: null,
  proposed_resolution: null,
  operator_outcome: null,
  operator: null,
  root_cause_id: null,
  validated_runbook_id: null,
  memory_mode: 'on',
  model_used: 'qwen/qwen3.8-27b',
  agent_status: 'completed',
  memory_trace_ids: ['trace-mock-1051-a', 'trace-mock-1051-b'],
  tool_trace_ids: [],
  created_at: at('11:43'),
  updated_at: at('11:45'),
}

/**
 * Memory OFF. No Hindsight call is made at all, so there are no traces — which
 * is itself the honest signal, not an empty list of failures.
 */
const memoryOffRun: IncidentResponse = {
  incident_id: 'INC-1052',
  state: 'OPEN',
  alert: {
    service: 'fraud-scorer',
    severity: 'p2',
    incident_type: 'latency',
    title: 'fraud-scorer inference latency above budget',
    summary: 'Inference p99 rose to 1.9s against a 25ms budget for the decision path.',
    source: 'pagerduty-sim',
    environment: 'prod',
    fired_at: at('16:20'),
    error_samples: ['fraud_inference_duration_p99 1.91'],
  },
  timeline: [
    { at: at('16:20'), actor: 'pager', event: 'alert_fired', detail: 'inference p99 1.91s' },
    { at: at('16:21'), actor: 'system', event: 'incident_opened', detail: 'normalized alert retained' },
    {
      at: at('16:21'),
      actor: 'system',
      event: 'memory_off',
      detail: 'memory_mode=off: Hindsight was not called for this run',
    },
  ],
  proposed_diagnosis: null,
  proposed_resolution: null,
  operator_outcome: null,
  operator: null,
  root_cause_id: null,
  validated_runbook_id: null,
  memory_mode: 'off',
  model_used: 'openai/gpt-oss-120b',
  agent_status: 'completed',
  memory_trace_ids: [],
  tool_trace_ids: [],
  created_at: at('16:21'),
  updated_at: at('16:21'),
}

export const MOCK_INCIDENTS: IncidentResponse[] = [
  ledgerDeployLatency,
  degradedRecall,
  novelSaturation,
  memoryOffRun,
]

/**
 * A trace is `finished_at` plus the latency it reports; deriving `started_at`
 * keeps the two consistent instead of hand-writing timestamps that can drift.
 */
/**
 * The alerts the UI can send to `POST /alerts`.
 *
 * Reused from the fixtures rather than duplicated, so the alert a live run is
 * given and the mock incident that describes it stay the same NimbusPay story.
 * Each one is a plain `AlertPayload`: an alert is an input, not a memory.
 */
export const DEMO_ALERTS = MOCK_INCIDENTS.map((incident) => incident.alert)

/**
 * A novel alert: no incident like it exists in the seeded history.
 *
 * This is the teach-then-replay scenario. First run recalls nothing; after an
 * operator confirms the cause and fix, the same alert should recall them.
 */
export const NOVEL_ALERT: AlertPayload = {
  service: 'webhook-dispatcher',
  severity: 'p1',
  incident_type: 'data_corruption',
  title: 'webhook signature verification failing for in-flight deliveries',
  summary:
    'webhook-dispatcher delivery failure rate 38%. Every failure is a signature verification error, and only for deliveries queued before the key rotation.',
  source: 'pagerduty-sim',
  environment: 'prod',
  fired_at: '2026-09-21T15:00:00Z',
  error_samples: [
    'webhook_signature_verification_failed_total 18420',
    'ERROR SignatureMismatch: payload signature does not match any active key',
    'webhook_delivery_failure_rate 0.38',
  ],
}

/** Everything the composer can send, novel scenario first. */
export const SENDABLE_ALERTS: AlertPayload[] = [NOVEL_ALERT, ...DEMO_ALERTS]

const times = (endedAt: string, latencyMs: number) => {
  const finished = new Date(endedAt)
  return {
    started_at: new Date(finished.getTime() - latencyMs).toISOString(),
    finished_at: finished.toISOString(),
  }
}

export const MOCK_TRACES: Record<string, MemoryTrace[]> = {
  'INC-1038': [
    {
      ...times(at('15:02'), 412.7),
      trace_id: 'trace-mock-1038-a',
      operation: 'recall',
      mode: 'on',
      success: true,
      degraded: false,
      bank_id: 'dejaops-prod',
      hit_count: 5,
      latency_ms: 412.7,
      attempts: 1,
      error_code: 'none',
      error_message: null,
      query: 'checkout-api p99 latency spike after payments-ledger deploy',
      tags: ['service:checkout-api', 'event_type:incident_open'],
      min_score: 0.2,
      no_match: false,
    },
    {
      ...times(at('15:02'), 388.2),
      trace_id: 'trace-mock-1038-b',
      operation: 'recall',
      mode: 'on',
      success: true,
      degraded: false,
      bank_id: 'dejaops-prod',
      hit_count: 3,
      latency_ms: 388.2,
      attempts: 1,
      error_code: 'none',
      error_message: null,
      query: 'checkout-api ledger confirmation timeouts',
      tags: ['service:checkout-api', 'event_type:resolution'],
      min_score: 0.2,
      no_match: false,
    },
    {
      // An exact tag scope makes the tag the relevance signal, so the threshold
      // is lifted to 0 rather than hiding a runbook that scores below it.
      ...times(at('15:02'), 295.4),
      trace_id: 'trace-mock-1038-c',
      operation: 'recall',
      mode: 'on',
      success: true,
      degraded: false,
      bank_id: 'dejaops-prod',
      hit_count: 2,
      latency_ms: 295.4,
      attempts: 1,
      error_code: 'none',
      error_message: null,
      query: 'checkout-api Kafka consumer lag on the payments topic',
      tags: ['event_type:runbook_entry', 'service:checkout-api'],
      min_score: 0,
      no_match: false,
    },
  ],
  'INC-1044': [
    {
      ...times(at('09:16'), 356.1),
      trace_id: 'trace-mock-1044-a',
      operation: 'recall',
      mode: 'on',
      success: true,
      degraded: false,
      bank_id: 'dejaops-prod',
      hit_count: 0,
      latency_ms: 356.1,
      attempts: 1,
      error_code: 'none',
      error_message: null,
      query: 'checkout-api connection pool saturation',
      tags: ['service:checkout-api', 'event_type:incident_open'],
      min_score: 0.2,
      no_match: true,
    },
  ],
  'INC-1051': [
    {
      // The threshold never came into play: the call failed before scoring.
      ...times(at('11:44'), 30012.4),
      trace_id: 'trace-mock-1051-a',
      operation: 'recall',
      mode: 'degraded',
      success: false,
      degraded: true,
      bank_id: 'dejaops-prod',
      hit_count: 0,
      latency_ms: 30012.4,
      attempts: 3,
      error_code: 'hindsight_unavailable',
      error_message: 'HTTP 503 from the memory service after 3 attempts',
      query: 'auth-service TLS certificate errors token validation',
      tags: ['service:auth-service'],
      min_score: null,
      no_match: true,
    },
    {
      ...times(at('11:45'), 10004.9),
      trace_id: 'trace-mock-1051-b',
      operation: 'health',
      mode: 'degraded',
      success: false,
      degraded: true,
      bank_id: 'dejaops-prod',
      hit_count: 0,
      latency_ms: 10004.9,
      attempts: 3,
      error_code: 'hindsight_unavailable',
      error_message: 'HTTP 503 from the memory service after 3 attempts',
      query: null,
      tags: [],
      min_score: null,
      no_match: false,
    },
  ],
  'INC-1052': [],
}
