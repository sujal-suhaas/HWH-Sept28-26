/**
 * The live `IncidentClient`, backed by the DejaOps HTTP API.
 *
 * Every method is one route. No retries and no fallbacks: if the backend is
 * unreachable the UI must say so, not quietly serve mock data.
 */

import { request } from '../api'
import type { IncidentClient } from '../incidentClient'
import type {
  ChatRequest,
  FeedbackRequest,
  IncidentListResponse,
  IncidentResponse,
  OpenIncidentRequest,
  MemoryMode,
  MemoryTrace,
} from '../types'

function listMemoryTraces(incidentId: string, signal?: AbortSignal): Promise<MemoryTrace[]> {
  return request<{ traces: MemoryTrace[] }>(
    `/incidents/${encodeURIComponent(incidentId)}/memory-trace`,
    { signal },
  ).then((body) => body.traces)
}

export function createLiveIncidentClient(): IncidentClient {
  return {
    kind: 'live',
    label: 'live api',

    async listIncidents(signal) {
      const body = await request<IncidentListResponse>('/incidents', { signal })
      return body.incidents
    },

    getIncident(incidentId, signal) {
      return request<IncidentResponse>(`/incidents/${encodeURIComponent(incidentId)}`, { signal })
    },

    openIncident(alert, memoryMode: MemoryMode, signal) {
      const body: OpenIncidentRequest = { alert, memory_mode: memoryMode }
      return request<IncidentResponse>('/alerts', {
        method: 'POST',
        body: JSON.stringify(body),
        signal,
      })
    },

    postChatMessage(incidentId, message, signal) {
      const body: ChatRequest = { message, operator: 'm.iyer' }
      return request<IncidentResponse>(`/chat/${encodeURIComponent(incidentId)}`, {
        method: 'POST',
        body: JSON.stringify(body),
        signal,
      })
    },

    postFeedback(incidentId, feedback: FeedbackRequest, signal) {
      return request<IncidentResponse>(
        `/incidents/${encodeURIComponent(incidentId)}/feedback`,
        { method: 'POST', body: JSON.stringify(feedback), signal },
      )
    },

    listMemoryTraces,
  }
}
