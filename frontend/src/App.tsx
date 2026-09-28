import { useEffect, useState } from 'react'
import {
  fetchHealth,
  fetchMemoryHealth,
  type HealthResponse,
  type MemoryTrace,
} from './api'
import { MemoryStatusBadge } from './components/MemoryStatusBadge'

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [memory, setMemory] = useState<MemoryTrace | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      try {
        const [healthResponse, memoryResponse] = await Promise.all([
          fetchHealth(controller.signal),
          fetchMemoryHealth(controller.signal),
        ])
        setHealth(healthResponse)
        setMemory(memoryResponse)
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : String(cause))
      }
    }
    void load()
    return () => controller.abort()
  }, [])

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-3xl px-6 py-16">
        <header>
          <p className="text-xs font-semibold tracking-widest text-sky-400 uppercase">
            NimbusPay on-call
          </p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight">DejaOps</h1>
          <p className="mt-3 text-slate-400">
            The on-call agent that has seen this incident before. Incident history, confirmed root
            causes, and validated runbooks are recalled from Hindsight memory before the agent
            diagnoses anything.
          </p>
        </header>

        {error && (
          <div className="mt-8 rounded-lg bg-rose-500/10 p-4 text-sm text-rose-300 ring-1 ring-rose-500/30 ring-inset">
            Backend unreachable: {error}. Start it with{' '}
            <code className="rounded bg-slate-800 px-1.5 py-0.5">
              uv run uvicorn src.api.app:app --reload
            </code>
          </div>
        )}

        <section className="mt-10 rounded-xl bg-slate-900 p-6 ring-1 ring-slate-800 ring-inset">
          <h2 className="text-sm font-semibold tracking-wide text-slate-300 uppercase">
            System status
          </h2>

          {health ? (
            <dl className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-slate-500">Backend</dt>
                <dd className="font-mono text-emerald-300">{health.status} v{health.version}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Memory bank</dt>
                <dd className="font-mono">{health.bank_id}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Primary model</dt>
                <dd className="font-mono">{health.model_primary}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Fallback model</dt>
                <dd className="font-mono">{health.model_fallback}</dd>
              </div>
            </dl>
          ) : (
            !error && <p className="mt-4 text-sm text-slate-500">Loading…</p>
          )}

          {memory && (
            <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-slate-800 pt-5">
              <MemoryStatusBadge trace={memory} />
              <span className="font-mono text-xs text-slate-500">
                {memory.operation} · {memory.latency_ms}ms · {memory.attempts} attempt(s)
              </span>
            </div>
          )}
        </section>

        <section className="mt-6 rounded-xl border border-dashed border-slate-800 p-6">
          <h2 className="text-sm font-semibold tracking-wide text-slate-300 uppercase">
            Coming next
          </h2>
          <ul className="mt-3 space-y-1.5 text-sm text-slate-400">
            <li>Alert feed and incident timeline</li>
            <li>Grounded diagnosis with cited recalled memory</li>
            <li>Operator confirm / reject / correct controls</li>
            <li>Memory Inspector with recall and retain traces</li>
            <li>Memory ON/OFF comparison on the same alert</li>
          </ul>
        </section>
      </div>
    </main>
  )
}
