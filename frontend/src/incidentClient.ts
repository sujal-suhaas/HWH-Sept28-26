/**
 * The incident data source the UI depends on.
 *
 * Two implementations exist so the UI never needs to know which one it has:
 * a mock (this phase) and a live HTTP client (phase 3). Keeping the interface
 * this small is deliberate — every method here is one the backend contract
 * already defines.
 */

import type { IncidentResponse, MemoryTrace } from './types'

export interface IncidentClient {
  /** `mock` renders a visible MOCK DATA badge. Never lie about which is which. */
  readonly kind: 'mock' | 'live'
  readonly label: string

  listIncidents(signal?: AbortSignal): Promise<IncidentResponse[]>
  getIncident(incidentId: string, signal?: AbortSignal): Promise<IncidentResponse>
  /** Returns the incident as it stands after the message was recorded. */
  postChatMessage(
    incidentId: string,
    message: string,
    signal?: AbortSignal,
  ): Promise<IncidentResponse>
  listMemoryTraces(incidentId: string, signal?: AbortSignal): Promise<MemoryTrace[]>
}
