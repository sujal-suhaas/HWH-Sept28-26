import { useState, type FormEvent } from 'react'
import type { IncidentResponse } from '../types'

interface ChatMessage {
  role: 'operator' | 'agent'
  text: string
  at: string
}

/**
 * The chat transcript is derived from the incident timeline rather than held in
 * separate component state, so there is exactly one record of what was said.
 */
export function messagesFromTimeline(incident: IncidentResponse): ChatMessage[] {
  const messages: ChatMessage[] = []
  for (const entry of incident.timeline) {
    if (entry.event === 'chat_message') {
      messages.push({ role: 'operator', text: entry.detail, at: entry.at })
    } else if (entry.event === 'chat_reply') {
      messages.push({ role: 'agent', text: entry.detail, at: entry.at })
    }
  }
  return messages
}

export interface ChatPanelProps {
  incident: IncidentResponse
  onSend: (message: string) => void
  sending: boolean
}

export function ChatPanel({ incident, onSend, sending }: ChatPanelProps) {
  const [draft, setDraft] = useState('')
  const messages = messagesFromTimeline(incident)

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = draft.trim()
    if (!message || sending) return
    onSend(message)
    setDraft('')
  }

  return (
    <section aria-label="Incident chat" className="flex flex-col gap-2">
      <h2 className="text-xs font-semibold tracking-widest text-slate-400 uppercase">Chat</h2>

      <div className="flex max-h-72 flex-col gap-2 overflow-y-auto rounded-lg bg-slate-900/60 p-3 ring-1 ring-slate-800 ring-inset">
        {messages.length === 0 && (
          <p className="text-sm text-slate-500">
            Ask the agent about this incident. Replies cite recalled memory or say plainly that
            there is none.
          </p>
        )}

        {messages.map((message, index) => (
          <div
            key={`${message.at}-${index}`}
            data-role={message.role}
            className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
              message.role === 'operator'
                ? 'self-end bg-sky-500/15 text-sky-100'
                : 'self-start bg-slate-800 text-slate-200'
            }`}
          >
            <p className="mb-0.5 text-[10px] font-semibold tracking-wide text-slate-400 uppercase">
              {message.role === 'operator' ? incident.operator ?? 'operator' : 'agent'}
            </p>
            <p className="leading-relaxed break-words">{message.text}</p>
          </div>
        ))}

        {sending && <p className="self-start text-xs text-slate-500">agent is responding…</p>}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <label htmlFor="chat-input" className="sr-only">
          Message the agent
        </label>
        <input
          id="chat-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask about this incident…"
          autoComplete="off"
          className="min-w-0 flex-1 rounded-lg bg-slate-900 px-3 py-2 text-sm text-slate-100 ring-1 ring-slate-800 ring-inset placeholder:text-slate-600 focus:ring-sky-500/50 focus:outline-none"
        />
        <button
          type="submit"
          disabled={sending || draft.trim().length === 0}
          className="rounded-lg bg-sky-500/90 px-4 py-2 text-sm font-medium text-slate-950 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
        >
          Send
        </button>
      </form>
    </section>
  )
}
