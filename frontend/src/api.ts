/**
 * The HTTP layer for the DejaOps backend.
 *
 * One place reads error responses, because the backend uses two shapes: our
 * handlers raise `HTTPException(422, detail="...")` while FastAPI's own request
 * validation returns `detail` as a list of `{loc, msg}`. An operator should see
 * which field was wrong, not "422".
 */

import type {
  ApiErrorBody,
  CatalogResponse,
  HealthResponse,
  IncidentMemoryTraceResponse,
} from './types'

export const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

/** An error the backend reported deliberately. `status` is the HTTP status. */
export class ApiError extends Error {
  readonly status: number
  readonly context: Record<string, unknown> | null

  constructor(status: number, message: string, context: Record<string, unknown> | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.context = context
  }
}

function flattenDetail(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const { detail } = body as ApiErrorBody

    if (typeof detail === 'string' && detail.trim()) return detail

    if (Array.isArray(detail)) {
      const parts = detail.map((item) => {
        const message = typeof item?.msg === 'string' ? item.msg : JSON.stringify(item)
        // `loc` starts with the literal "body"; the operator cares about the field.
        const path = (item?.loc ?? []).filter((part) => part !== 'body').join('.')
        return path ? `${path}: ${message}` : message
      })
      if (parts.length > 0) return parts.join('; ')
    }
  }
  return `request failed with HTTP ${status}`
}

/** A `fetch` that throws `ApiError` with a readable message on a non-2xx. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { 'content-type': 'application/json' } : {}),
      ...init.headers,
    },
  })

  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // A non-JSON error body (a proxy, a crash) is not worth a second failure.
    }
    const context =
      body && typeof body === 'object' && 'context' in body
        ? ((body as ApiErrorBody).context ?? null)
        : null
    throw new ApiError(response.status, flattenDetail(body, response.status), context)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { signal })
}

export function fetchCatalog(signal?: AbortSignal): Promise<CatalogResponse> {
  return request<CatalogResponse>('/catalog', { signal })
}

export function fetchIncidentMemoryTraces(
  incidentId: string,
  signal?: AbortSignal,
): Promise<IncidentMemoryTraceResponse> {
  return request<IncidentMemoryTraceResponse>(
    `/incidents/${encodeURIComponent(incidentId)}/memory-trace`,
    { signal },
  )
}
