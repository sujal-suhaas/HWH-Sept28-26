/** Minimal typed client for the DejaOps system endpoints. */

import type { HealthResponse, MemoryTrace } from './types'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/health`, { signal })
  if (!response.ok) throw new Error(`GET /health failed with ${response.status}`)
  return (await response.json()) as HealthResponse
}

export async function fetchMemoryHealth(signal?: AbortSignal): Promise<MemoryTrace> {
  const response = await fetch(`${API_BASE}/health/memory`, { signal })
  if (!response.ok) throw new Error(`GET /health/memory failed with ${response.status}`)
  return (await response.json()) as MemoryTrace
}

export async function fetchMemoryTraces(limit = 20, signal?: AbortSignal): Promise<MemoryTrace[]> {
  const response = await fetch(`${API_BASE}/api/memory/traces?limit=${limit}`, { signal })
  if (!response.ok) throw new Error(`GET /api/memory/traces failed with ${response.status}`)
  const body = (await response.json()) as { traces: MemoryTrace[] }
  return body.traces
}
