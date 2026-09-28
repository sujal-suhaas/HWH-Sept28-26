/**
 * TypeScript mirror of `src/contracts.py`.
 *
 * `src/contracts.py` is the source of truth. When it changes, this file changes
 * with it — that is the cost of a frozen contract, and it is cheaper than three
 * slightly different shapes drifting apart.
 */

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

export type IncidentState =
  | 'OPEN'
  | 'DIAGNOSING'
  | 'WAITING_FOR_OPERATOR'
  | 'RESOLVING'
  | 'RESOLVED'
  | 'INCONCLUSIVE'
  | 'FAILED'

export type Severity = 'p1' | 'p2' | 'p3' | 'p4'

export type IncidentType =
  | 'latency'
  | 'error_rate'
  | 'saturation'
  | 'availability'
  | 'data_corruption'

export type MemoryMode = 'on' | 'off'

export type Confidence = 'low' | 'medium' | 'high'

export type ProposalKind = 'diagnosis' | 'resolution'

/**
 * A proposal is never authoritative. `proposed` means the agent has suggested a
 * diagnosis; `pending_confirmation` means it has suggested a fix. Neither is a
 * resolution, and the UI must never render one as if it were.
 */
export type ProposalStatus = 'proposed' | 'pending_confirmation'

export type FeedbackType =
  | 'DIAGNOSIS_CONFIRMED'
  | 'DIAGNOSIS_REJECTED'
  | 'OPERATOR_CORRECTION'
  | 'RESOLUTION_CONFIRMED'
  | 'RESOLUTION_FAILED'
  | 'INCONCLUSIVE'

export type TimelineActor = 'pager' | 'agent' | 'operator' | 'system'

// ---------------------------------------------------------------------------
// Contract models
// ---------------------------------------------------------------------------

export interface AlertPayload {
  service: string
  severity: Severity
  incident_type: IncidentType
  title: string
  summary: string
  source: string
  environment: string
  fired_at: string
  error_samples: string[]
}

export interface Proposal {
  kind: ProposalKind
  status: ProposalStatus
  content: string
  /** At most two sentences. Concise evidence, never model reasoning. */
  evidence_summary: string
  /** Memory ids returned by a tool in the same run. Validated server-side. */
  cited_memory_ids: string[]
  confidence: Confidence | null
  runbook_id: string | null
  /** Only a diagnosis carries this. Catalog-validated server-side. */
  root_cause_id?: string | null
  proposed_by: string
  proposed_at: string
}

export interface TimelineEntry {
  at: string
  actor: TimelineActor
  event: string
  detail: string
}

export interface IncidentResponse {
  incident_id: string
  state: IncidentState
  alert: AlertPayload
  timeline: TimelineEntry[]

  proposed_diagnosis: Proposal | null
  proposed_resolution: Proposal | null

  operator_outcome: FeedbackType | null
  operator: string | null
  root_cause_id: string | null
  validated_runbook_id: string | null

  memory_mode: MemoryMode
  model_used: string | null
  agent_status: string | null

  memory_trace_ids: string[]
  tool_trace_ids: string[]

  created_at: string
  updated_at: string
}

export interface IncidentListResponse {
  count: number
  incidents: IncidentResponse[]
}

export interface FeedbackRequest {
  feedback_type: FeedbackType
  operator: string
  note?: string | null
  root_cause_id?: string | null
  corrected_root_cause?: string | null
  validated_fix?: string | null
  validated_runbook_id?: string | null
}

/** Body of `POST /alerts`. `memory_mode` overrides the deployment default. */
export interface OpenIncidentRequest {
  alert: AlertPayload
  memory_mode?: MemoryMode | null
}

/** Body of `POST /chat/{incident_id}`. */
export interface ChatRequest {
  message: string
  operator: string
}

/**
 * An error the backend reported deliberately.
 *
 * FastAPI's own validation failures use `detail` as a list of `{loc, msg}`; our
 * handlers use it as a string. Both reach the operator, so both are flattened
 * into one message rather than shown as "422".
 */
export interface ApiErrorBody {
  detail: string | Array<{ loc?: Array<string | number>; msg?: string; type?: string }>
  context?: Record<string, unknown> | null
}

// ---------------------------------------------------------------------------
// Catalog
// ---------------------------------------------------------------------------

export interface CatalogService {
  name: string
  tier: number
  owner: string
  dependencies: string[]
  slo: string
}

export interface CatalogRootCause {
  id: string
  name: string
  summary: string
  detail: string
}

export interface CatalogRunbook {
  id: string
  title: string
  /** Every runbook names its cause, which is how the UI can prefill it. */
  root_cause_id: string
  steps: string[]
  verified: boolean
}

export interface CatalogResponse {
  company: string
  services: CatalogService[]
  root_causes: CatalogRootCause[]
  runbooks: CatalogRunbook[]
}

/**
 * `GET /incidents/{id}/memory-trace`.
 *
 * The envelope's `memory_mode` is the *incident's* mode, so an empty `traces`
 * list can be read as "memory was off" rather than "nothing happened".
 */
export interface IncidentMemoryTraceResponse {
  incident_id: string
  memory_mode: MemoryMode
  count: number
  traces: MemoryTrace[]
}

// ---------------------------------------------------------------------------
// System / memory
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string
  version: string
  memory_mode: MemoryMode
  bank_id: string
  model_primary: string
  model_fallback: string
}

export interface MemoryTrace {
  trace_id: string
  operation: string
  /** The trace's own mode: 'on' | 'off' | 'degraded'. Not the incident's. */
  mode: string
  started_at: string
  finished_at: string
  latency_ms: number
  success: boolean
  bank_id: string | null
  hit_count: number
  attempts: number
  error_code: string
  error_message: string | null
  degraded: boolean
  query: string | null
  tags: string[]
  /** Relevance threshold in force. `0` means an exact tag scope was the signal. */
  min_score: number | null
  no_match: boolean
  // `provider_trace` is deliberately not mirrored here. The backend sanitizes it
  // and the UI has no use for it; typing it would invite rendering raw provider
  // output, which AGENTS.md forbids.
}

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

export const SEVERITY_LABEL: Record<Severity, string> = {
  p1: 'P1',
  p2: 'P2',
  p3: 'P3',
  p4: 'P4',
}

export const INCIDENT_STATE_LABEL: Record<IncidentState, string> = {
  OPEN: 'open',
  DIAGNOSING: 'diagnosing',
  WAITING_FOR_OPERATOR: 'waiting for operator',
  RESOLVING: 'resolving',
  RESOLVED: 'resolved',
  INCONCLUSIVE: 'inconclusive',
  FAILED: 'failed',
}

export const INCIDENT_TYPE_LABEL: Record<IncidentType, string> = {
  latency: 'latency',
  error_rate: 'error rate',
  saturation: 'saturation',
  availability: 'availability',
  data_corruption: 'data corruption',
}

export const FEEDBACK_LABEL: Record<FeedbackType, string> = {
  DIAGNOSIS_CONFIRMED: 'diagnosis confirmed',
  DIAGNOSIS_REJECTED: 'diagnosis rejected',
  OPERATOR_CORRECTION: 'operator correction',
  RESOLUTION_CONFIRMED: 'resolution confirmed',
  RESOLUTION_FAILED: 'resolution failed',
  INCONCLUSIVE: 'inconclusive',
}
