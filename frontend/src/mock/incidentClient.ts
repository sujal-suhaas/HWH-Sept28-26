/**
 * In-memory implementation of `IncidentClient`.
 *
 * State is held per client instance, so chat accumulates during a session and a
 * fresh client starts clean. Replies are derived from the incident's own state
 * rather than canned, so the mock cannot claim a memory that is not there.
 *
 * It has no agent and no memory layer. Where the live path would record
 * `memory_retained`, this records `mock_no_memory` — the mock must never claim
 * to have written memory it does not have.
 */

import type { IncidentClient } from '../incidentClient'
import type {
  AlertPayload,
  FeedbackRequest,
  FeedbackType,
  IncidentResponse,
  IncidentState,
  MemoryMode,
  MemoryTrace,
  TimelineEntry,
} from '../types'
import { MOCK_INCIDENTS, MOCK_TRACES } from './fixtures'

export interface MockClientOptions {
  /** Simulated round-trip delay. Set to 0 in tests. */
  latencyMs?: number
  now?: () => Date
}

/** Mirrors the backend's FEEDBACK_STATE mapping. The backend is the authority. */
const FEEDBACK_STATE: Record<FeedbackType, IncidentState> = {
  DIAGNOSIS_CONFIRMED: 'RESOLVING',
  DIAGNOSIS_REJECTED: 'DIAGNOSING',
  OPERATOR_CORRECTION: 'RESOLVING',
  RESOLUTION_CONFIRMED: 'RESOLVED',
  RESOLUTION_FAILED: 'RESOLVING',
  INCONCLUSIVE: 'INCONCLUSIVE',
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      },
      { once: true },
    )
  })
}

/**
 * An honest reply about the current state of this incident.
 *
 * Deliberately a function of the incident rather than a lookup table: a mock
 * that says "I recalled a similar incident" for a run where recall failed would
 * teach the UI the wrong thing.
 */
export function draftAgentReply(incident: IncidentResponse, traces: MemoryTrace[]): string {
  if (incident.memory_mode === 'off') {
    return (
      'Memory is off for this run, so I have no historical context to work from. ' +
      'Any diagnosis I give will be ungrounded.'
    )
  }

  const failed = traces.find((trace) => !trace.success)
  if (failed) {
    return (
      `The memory service is unavailable (${failed.error_code}), so I could not check for ` +
      'prior incidents. Treat this as having no historical evidence, not as having no precedent.'
    )
  }

  const recalled = traces.filter((trace) => trace.operation === 'recall' && trace.hit_count > 0)
  const allRecallsEmpty =
    traces.some((trace) => trace.operation === 'recall') && recalled.length === 0
  if (allRecallsEmpty) {
    return (
      'No relevant historical incident was found for this service and symptom, so I have no ' +
      'precedent to ground a diagnosis on.'
    )
  }

  const diagnosis = incident.proposed_diagnosis
  if (diagnosis) {
    const cited = diagnosis.cited_memory_ids.length
    if (cited === 0) {
      return (
        `My proposed diagnosis is: ${diagnosis.content} ` +
        'It cites no recalled memory, so it is not grounded in incident history. ' +
        'It is awaiting your confirmation.'
      )
    }
    return (
      `My proposed diagnosis is: ${diagnosis.content} ` +
      `It cites ${cited} recalled memor${cited === 1 ? 'y' : 'ies'} and is awaiting your confirmation.`
    )
  }

  return 'I am still gathering evidence. Nothing has been proposed yet.'
}

export function createMockIncidentClient(options: MockClientOptions = {}): IncidentClient {
  const latencyMs = options.latencyMs ?? 120
  const now = options.now ?? (() => new Date())
  let nextId = 9100

  const incidents = new Map<string, IncidentResponse>(
    MOCK_INCIDENTS.map((incident) => [incident.incident_id, structuredClone(incident)]),
  )
  const traces = new Map<string, MemoryTrace[]>(
    Object.entries(MOCK_TRACES).map(([id, list]) => [id, structuredClone(list)]),
  )

  function require(incidentId: string): IncidentResponse {
    const incident = incidents.get(incidentId)
    if (!incident) throw new Error(`unknown incident ${incidentId}`)
    return incident
  }

  return {
    kind: 'mock',
    label: 'mock data',

    async listIncidents(signal) {
      await delay(latencyMs, signal)
      return [...incidents.values()].map((incident) => structuredClone(incident))
    },

    async getIncident(incidentId, signal) {
      await delay(latencyMs, signal)
      return structuredClone(require(incidentId))
    },

    async openIncident(alert: AlertPayload, memoryMode: MemoryMode, signal) {
      await delay(latencyMs, signal)
      const timestamp = now().toISOString()
      const incidentId = `INC-${nextId++}`

      const timeline: TimelineEntry[] = [
        { at: timestamp, actor: 'pager', event: 'alert_fired', detail: alert.title },
        { at: timestamp, actor: 'system', event: 'incident_opened', detail: 'alert normalized' },
        {
          at: timestamp,
          actor: 'system',
          event: 'mock_no_memory',
          detail:
            'the mock data source has no agent and no memory layer, so no run happened and ' +
            'no memory was written. Point the UI at the live API for a real run.',
        },
      ]

      const incident: IncidentResponse = {
        incident_id: incidentId,
        state: 'OPEN',
        alert,
        timeline,
        proposed_diagnosis: null,
        proposed_resolution: null,
        operator_outcome: null,
        diagnosis_outcome: null,
        resolution_outcome: null,
        operator: null,
        root_cause_id: null,
        validated_runbook_id: null,
        memory_mode: memoryMode,
        model_used: null,
        agent_status: 'not_run',
        memory_trace_ids: [],
        tool_trace_ids: [],
        created_at: timestamp,
        updated_at: timestamp,
      }

      incidents.set(incidentId, incident)
      traces.set(incidentId, [])
      return structuredClone(incident)
    },

    async postChatMessage(incidentId, message, signal) {
      await delay(latencyMs, signal)
      const incident = require(incidentId)
      const timestamp = now().toISOString()
      const incidentTraces = traces.get(incidentId) ?? []

      const entries: TimelineEntry[] = [
        { at: timestamp, actor: 'operator', event: 'chat_message', detail: message },
        {
          at: timestamp,
          actor: 'agent',
          event: 'chat_reply',
          detail: draftAgentReply(incident, incidentTraces),
        },
      ]

      const updated: IncidentResponse = {
        ...incident,
        timeline: [...incident.timeline, ...entries],
        updated_at: timestamp,
      }
      incidents.set(incidentId, updated)
      return structuredClone(updated)
    },

    async postFeedback(incidentId, feedback: FeedbackRequest, signal) {
      await delay(latencyMs, signal)
      const incident = require(incidentId)

      // The backend is the authority on this; the mock mirrors it so the UI's
      // closed-incident path is reachable without a backend.
      if (incident.state === 'RESOLVED') {
        throw new Error(
          'incident is already RESOLVED; open a new incident rather than changing a closed outcome',
        )
      }
      // Mirrors the contract's own required-fields rule, not the catalog check:
      // a missing required field is a contract violation the mock can know about.
      if (
        (feedback.feedback_type === 'DIAGNOSIS_CONFIRMED' ||
          feedback.feedback_type === 'OPERATOR_CORRECTION') &&
        !feedback.root_cause_id &&
        !feedback.corrected_root_cause
      ) {
        throw new Error(
          `${feedback.feedback_type} requires root_cause_id or corrected_root_cause`,
        )
      }
      if (
        feedback.feedback_type === 'RESOLUTION_CONFIRMED' &&
        !feedback.validated_fix
      ) {
        throw new Error('RESOLUTION_CONFIRMED requires validated_fix')
      }

      const timestamp = now().toISOString()
      const entries: TimelineEntry[] = [
        {
          at: timestamp,
          actor: 'operator',
          event: feedback.feedback_type,
          detail: feedback.note
            ? `recorded by ${feedback.operator}: ${feedback.note}`
            : `recorded by ${feedback.operator}`,
        },
        {
          at: timestamp,
          actor: 'system',
          event: 'mock_no_memory',
          detail:
            incident.memory_mode === 'off'
              ? 'memory_mode=off: this outcome was recorded on the incident but written to no memory'
              : 'the mock data source has no memory layer, so this outcome was recorded on the incident but written to no memory',
        },
      ]

      // Mirrors src/api/lifecycle.py: the outcome is recorded against its own
      // kind, so confirming the resolution cannot reset the diagnosis card.
      const DIAGNOSIS_KINDS = ['DIAGNOSIS_CONFIRMED', 'DIAGNOSIS_REJECTED', 'OPERATOR_CORRECTION']
      const isDiagnosis = DIAGNOSIS_KINDS.includes(feedback.feedback_type)
      const settlesNothing = feedback.feedback_type === 'INCONCLUSIVE'

      const updated: IncidentResponse = {
        ...incident,
        state: FEEDBACK_STATE[feedback.feedback_type],
        timeline: [...incident.timeline, ...entries],
        operator_outcome: feedback.feedback_type,
        diagnosis_outcome: isDiagnosis ? feedback.feedback_type : incident.diagnosis_outcome,
        resolution_outcome:
          !isDiagnosis && !settlesNothing ? feedback.feedback_type : incident.resolution_outcome,
        operator: feedback.operator,
        root_cause_id: feedback.root_cause_id ?? incident.root_cause_id,
        validated_runbook_id: feedback.validated_runbook_id ?? incident.validated_runbook_id,
        updated_at: timestamp,
      }
      incidents.set(incidentId, updated)
      return structuredClone(updated)
    },

    async listMemoryTraces(incidentId, signal) {
      await delay(latencyMs, signal)
      require(incidentId)
      return structuredClone(traces.get(incidentId) ?? [])
    },
  }
}
