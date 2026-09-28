/**
 * The incident data source the UI depends on.
 *
 * Two implementations exist so the UI never needs to know which one it has:
 * a mock (used by component tests and for offline work) and the live HTTP
 * client. Keeping the interface this small is deliberate — every method here is
 * one the backend contract already defines.
 */

import type {
  AlertPayload,
  FeedbackRequest,
  IncidentResponse,
  MemoryMode,
  MemoryTrace,
} from './types'

export interface IncidentClient {
  /** `mock` renders a visible MOCK DATA badge. Never lie about which is which. */
  readonly kind: 'mock' | 'live'
  readonly label: string

  listIncidents(signal?: AbortSignal): Promise<IncidentResponse[]>
  getIncident(incidentId: string, signal?: AbortSignal): Promise<IncidentResponse>

  /**
   * Open a new incident from an alert and run the agent over it.
   *
   * `memoryMode` is per-request, which is what makes the ON/OFF comparison
   * possible: the same alert, the same models, only memory differs.
   */
  openIncident(
    alert: AlertPayload,
    memoryMode: MemoryMode,
    signal?: AbortSignal,
  ): Promise<IncidentResponse>

  /** Returns the incident as it stands after the message was recorded. */
  postChatMessage(
    incidentId: string,
    message: string,
    signal?: AbortSignal,
  ): Promise<IncidentResponse>

  /** Record an explicit operator outcome. The only path to authoritative memory. */
  postFeedback(
    incidentId: string,
    feedback: FeedbackRequest,
    signal?: AbortSignal,
  ): Promise<IncidentResponse>

  listMemoryTraces(incidentId: string, signal?: AbortSignal): Promise<MemoryTrace[]>
}
