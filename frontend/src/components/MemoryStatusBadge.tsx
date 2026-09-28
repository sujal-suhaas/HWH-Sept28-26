import type { MemoryTrace } from '../api'

export type MemoryStateLabel =
  | 'memory recalled'
  | 'memory retained'
  | 'no relevant memory found'
  | 'memory service unavailable'
  | 'memory off'

export interface MemoryState {
  label: MemoryStateLabel
  tone: 'good' | 'neutral' | 'warn' | 'bad'
  detail: string
}

/**
 * Map a memory trace to exactly one honest state.
 *
 * The rule this component exists to enforce: never render "recalled" for a
 * recall that failed, was empty, or never happened.
 */
export function describeMemoryState(trace: MemoryTrace): MemoryState {
  if (trace.mode === 'off') {
    return {
      label: 'memory off',
      tone: 'neutral',
      detail: 'memory_mode=off — Hindsight was not called',
    }
  }

  if (!trace.success) {
    return {
      label: 'memory service unavailable',
      tone: 'bad',
      detail: trace.error_message ?? trace.error_code,
    }
  }

  if (trace.operation === 'retain') {
    return {
      label: 'memory retained',
      tone: 'good',
      detail: `retained ${trace.hit_count} memory item(s)`,
    }
  }

  if (trace.operation === 'recall') {
    if (trace.no_match || trace.hit_count === 0) {
      return {
        label: 'no relevant memory found',
        tone: 'warn',
        detail: 'recall succeeded but nothing cleared the relevance threshold',
      }
    }
    return {
      label: 'memory recalled',
      tone: 'good',
      detail: `${trace.hit_count} relevant memory item(s)`,
    }
  }

  return {
    label: 'memory service unavailable',
    tone: 'warn',
    detail: `unhandled operation ${trace.operation}`,
  }
}

const TONE_CLASSES: Record<MemoryState['tone'], string> = {
  good: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
  neutral: 'bg-slate-500/15 text-slate-300 ring-slate-500/30',
  warn: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  bad: 'bg-rose-500/15 text-rose-300 ring-rose-500/30',
}

export function MemoryStatusBadge({ trace }: { trace: MemoryTrace }) {
  const state = describeMemoryState(trace)
  return (
    <span
      data-testid="memory-state"
      data-state={state.label}
      title={state.detail}
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${TONE_CLASSES[state.tone]}`}
    >
      {state.label}
    </span>
  )
}
