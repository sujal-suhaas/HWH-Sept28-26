import { describe, expect, it } from 'vitest'
import { createMockIncidentClient, draftAgentReply } from './incidentClient'
import { MOCK_INCIDENTS, MOCK_TRACES } from './fixtures'

function client() {
  return createMockIncidentClient({ latencyMs: 0 })
}

describe('createMockIncidentClient', () => {
  it('lists every fixture incident', async () => {
    const incidents = await client().listIncidents()
    expect(incidents.map((incident) => incident.incident_id)).toEqual(
      MOCK_INCIDENTS.map((incident) => incident.incident_id),
    )
  })

  it('rejects an unknown incident id instead of returning a fabricated one', async () => {
    await expect(client().getIncident('INC-9999')).rejects.toThrow(/unknown incident/)
  })

  it('returns a deep copy, so callers cannot mutate the fixture store', async () => {
    const instance = client()
    const first = await instance.getIncident('INC-1038')
    first.alert.title = 'mutated'
    const second = await instance.getIncident('INC-1038')
    expect(second.alert.title).not.toBe('mutated')
  })

  it('appends the operator message and an agent reply to the timeline', async () => {
    const instance = client()
    const before = await instance.getIncident('INC-1038')
    const after = await instance.postChatMessage('INC-1038', 'why checkout?')

    expect(after.timeline.length).toBe(before.timeline.length + 2)
    expect(after.timeline.at(-2)).toMatchObject({ actor: 'operator', event: 'chat_message' })
    expect(after.timeline.at(-1)).toMatchObject({ actor: 'agent', event: 'chat_reply' })

    // And the change persists for the next read.
    expect((await instance.getIncident('INC-1038')).timeline.length).toBe(after.timeline.length)
  })

  it('reports memory-off incidents with no traces at all', async () => {
    expect(await client().listMemoryTraces('INC-1052')).toEqual([])
    expect(MOCK_TRACES['INC-1052']).toEqual([])
  })
})

describe('draftAgentReply', () => {
  const incident = (id: string) => MOCK_INCIDENTS.find((item) => item.incident_id === id)!

  it('says memory is off rather than inventing historical context', () => {
    const reply = draftAgentReply(incident('INC-1052'), [])
    expect(reply).toMatch(/Memory is off/)
  })

  it('says the memory service is unavailable for a failed recall', () => {
    const reply = draftAgentReply(incident('INC-1051'), MOCK_TRACES['INC-1051'])
    expect(reply).toMatch(/memory service is unavailable/)
    expect(reply).toMatch(/hindsight_unavailable/)
  })

  it('says there is no precedent when recall succeeded but matched nothing', () => {
    const reply = draftAgentReply(incident('INC-1044'), MOCK_TRACES['INC-1044'])
    expect(reply).toMatch(/No relevant historical incident was found/)
  })

  it('summarises the proposal when one exists', () => {
    const reply = draftAgentReply(incident('INC-1038'), MOCK_TRACES['INC-1038'])
    expect(reply).toMatch(/My proposed diagnosis is/)
    expect(reply).toMatch(/awaiting your confirmation/)
  })

  it('never claims a recall happened when the trace list is empty', () => {
    const reply = draftAgentReply(incident('INC-1051'), [])
    expect(reply).not.toMatch(/recalled/i)
    expect(reply).toMatch(/still gathering evidence/)
  })

  it('says a proposal with no citations is not grounded', () => {
    const ungrounded = structuredClone(incident('INC-1038'))
    ungrounded.proposed_diagnosis = { ...ungrounded.proposed_diagnosis!, cited_memory_ids: [] }
    const reply = draftAgentReply(ungrounded, MOCK_TRACES['INC-1038'])
    expect(reply).toMatch(/not grounded/)
  })
})
