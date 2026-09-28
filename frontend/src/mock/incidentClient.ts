/**
 * In-memory implementation of `IncidentClient`.
 *
 * State is held per client instance, so chat accumulates during a session and a
 * fresh client starts clean. Replies are derived from the incident's own state
 * rather than canned, so the mock cannot claim a memory that is not there.
 */

import type { IncidentClient } from '../incidentClient'
import type { IncidentResponse, MemoryTrace, TimelineEntry } from '../types'
import { MOCK_INCIDENTS, MOCK_TRACES } from './fixtures'

export interface MockClientOptions {
  /** Simulated round-trip delay. Set to 0 in tests. */
  latencyMs?: number
  now?: () => Date
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
  const allRecallsEmpty = traces.some((trace) => trace.operation === 'recall') && recalled.length === 0
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

    async listMemoryTraces(incidentId, signal) {
      await delay(latencyMs, signal)
      require(incidentId)
      return structuredClone(traces.get(incidentId) ?? [])
    },
  }
}
