/**
 * The room note taker must (a) send every appended entry to the gateway
 * verbatim, (b) never duplicate an entry id, and (c) never let a missing or
 * failing gateway leak into the room append path.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type * as groupChat from './group-chat'
import { createGroupGateway, drain, runTimersInline, scriptedStorage } from './group-test-utils'
import type { ScriptedGateway } from './group-test-utils'
import type * as groupTranscript from './group-transcript'

const { host } = vi.hoisted(() => ({ host: {} as Record<string, unknown> }))

vi.mock('@hermes/plugin-sdk', async () => {
  const { pluginSdkMock } = await import('./group-test-utils')

  return pluginSdkMock(host)
})

interface Room {
  chat: typeof groupChat
  gateway: ScriptedGateway
  transcript: typeof groupTranscript
}

/** Same shape as group-chat.test.ts's loadRoom: a scripted gateway behind
 *  the host, fresh module instances, storage wired to the plugin ctx. */
async function loadRoom(): Promise<Room> {
  vi.resetModules()
  const gateway = createGroupGateway()

  for (const key of Object.keys(host)) {
    delete host[key]
  }

  Object.assign(host, gateway.host)

  const [chat, transcript, shared] = await Promise.all([
    import('./group-chat'),
    import('./group-transcript'),
    import('./shared')
  ])

  shared.setPluginCtx(scriptedStorage(gateway.storage))

  return { chat, gateway, transcript }
}

/** Unit surface: only the transcript module, a bare host we control. */
async function loadTranscriptOnly(): Promise<typeof groupTranscript> {
  vi.resetModules()

  for (const key of Object.keys(host)) {
    delete host[key]
  }

  return import('./group-transcript')
}

beforeEach(() => {
  runTimersInline()
})

describe('recordGroupTranscriptEntry', () => {
  it('sends the entry verbatim with room identity and thread; image data never rides along', async () => {
    const transcript = await loadTranscriptOnly()
    const request = vi.fn(async () => ({ appended: true }))
    host.request = request

    const entry = {
      id: 'e1',
      at: 1700000000000,
      from: { kind: 'member' as const, name: 'impl' },
      text: 'Line one.\n\n  indented\n```\ncode\n```',
      thread: 't1',
      images: [{ data: 'data:image/png;base64,AAAA', kind: 'image' as const, name: 'shot.png' }]
    }

    transcript.recordGroupTranscriptEntry('Think Tank', 'r1', entry)

    expect(request).toHaveBeenCalledTimes(1)
    const [method, params] = request.mock.calls[0] as unknown as [string, Record<string, unknown>]
    expect(method).toBe('group.transcript.append')
    expect(params.group).toBe('Think Tank')
    expect(params.room_id).toBe('r1')
    const sent = params.entry as Record<string, unknown>
    expect(sent.text).toBe(entry.text)
    expect(sent.id).toBe('e1')
    expect(sent.thread).toBe('t1')
    expect(sent.from).toEqual({ kind: 'member', name: 'impl' })
    expect(sent.images).toEqual([{ name: 'shot.png' }])
  })

  it('drops a repeated entry id client-side', async () => {
    const transcript = await loadTranscriptOnly()
    const request = vi.fn(async () => ({}))
    host.request = request
    const entry = { id: 'e2', at: 1, from: { kind: 'user' as const, name: 'You' }, text: 'hi', thread: 't1' }

    transcript.recordGroupTranscriptEntry('Room', null, entry)
    transcript.recordGroupTranscriptEntry('Room', null, entry)

    expect(request).toHaveBeenCalledTimes(1)
  })

  it('is a no-op without a host request function and swallows rejections and throws', async () => {
    const transcript = await loadTranscriptOnly()
    const entry = { id: 'e3', at: 1, from: { kind: 'user' as const, name: 'You' }, text: 'hi', thread: 't1' }

    expect(() => transcript.recordGroupTranscriptEntry('Room', null, entry)).not.toThrow()

    host.request = vi.fn(async () => {
      throw new Error('older gateway: unknown method')
    })
    expect(() => transcript.recordGroupTranscriptEntry('Room', null, { ...entry, id: 'e4' })).not.toThrow()
    await Promise.resolve()

    host.request = () => {
      throw new Error('sync stub')
    }

    expect(() => transcript.recordGroupTranscriptEntry('Room', null, { ...entry, id: 'e5' })).not.toThrow()
  })
})

describe('appendGroupChatEntry → transcript', () => {
  it('records every appended entry with the room id, user and member alike', async () => {
    const { chat, gateway } = await loadRoom()
    chat.$groupChats.set({
      Room: { log: [], roomId: 'r-room', sessions: {}, watermarks: {} }
    } as never)

    const user = chat.appendGroupChatEntry('Room', { kind: 'user', name: 'You' }, '@orchestrator resume', 't1')
    const member = chat.appendGroupChatEntry('Room', { kind: 'member', name: 'orchestrator' }, 'Released.', 't1')

    // The scripted gateway logs a call only after its awaited handler settles.
    await drain(() => false)
    const calls = gateway.rpcFor('group.transcript.append')
    expect(calls).toHaveLength(2)
    expect(calls.map(c => (c.params.entry as { id: string }).id)).toEqual([user.id, member.id])
    expect(calls[0].params.room_id).toBe('r-room')
    expect((calls[0].params.entry as { text: string }).text).toBe('@orchestrator resume')
    expect((calls[1].params.entry as { from: unknown }).from).toEqual({ kind: 'member', name: 'orchestrator' })
  })

  it('does not record the dropped byte-identical echo append', async () => {
    const { chat, gateway } = await loadRoom()
    chat.$groupChats.set({
      Room: { log: [], roomId: 'r-room', sessions: {}, watermarks: {} }
    } as never)

    chat.appendGroupChatEntry('Room', { kind: 'member', name: 'impl' }, 'Confirmed.', 't1')
    chat.appendGroupChatEntry('Room', { kind: 'member', name: 'impl' }, 'Confirmed.', 't1')

    await drain(() => false)
    expect(gateway.rpcFor('group.transcript.append')).toHaveLength(1)
  })
})
