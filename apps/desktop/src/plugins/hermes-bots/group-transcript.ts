/**
 * Room note taker: mirror every posted room entry, verbatim, to the gateway's
 * per-room transcript file (`group.transcript.append`, written under
 * `<HERMES_HOME>/room-logs/` or `room_log.dir`).
 *
 * The Desktop keeps only a bounded window of each room log and the ui_meta
 * mirror keeps less, so the file is the only durable, complete record of a
 * room. This must never affect the room itself: the call is fire-and-forget,
 * a missing host, an older gateway without the method, or a write failure all
 * degrade to "not recorded" and nothing else.
 */
import { host } from '@hermes/plugin-sdk'

import type { GroupMessage } from './types'

/** Entries whose transcript write is in flight or done this session — a
 *  duplicate append (e.g. a stale loop re-committing) is dropped client-side
 *  too, though the gateway also dedupes by entry id. */
const recorded = new Set<string>()

export function recordGroupTranscriptEntry(group: string, roomId: null | string | undefined, entry: GroupMessage): void {
  const id = entry?.id ? String(entry.id) : ''

  if (id) {
    if (recorded.has(id)) {
      return
    }

    recorded.add(id)

    // Bound the set: ids are unique per process, so a rolling window is enough.
    if (recorded.size > 4096) {
      const first = recorded.values().next().value

      if (first !== undefined) {
        recorded.delete(first)
      }
    }
  }

  const request = (host as { request?: (method: string, params: Record<string, unknown>) => Promise<unknown> })?.request

  if (typeof request !== 'function') {
    return
  }

  const payload = {
    group,
    room_id: roomId || null,
    entry: {
      id,
      at: entry.at,
      from: entry.from,
      text: entry.text,
      thread: entry.thread || 'legacy',
      // Only names — the data URLs stay in the room store.
      ...(Array.isArray(entry.images) && entry.images.length
        ? { images: entry.images.map(img => ({ name: img?.name || 'image' })) }
        : {})
    }
  }

  try {
    void Promise.resolve(request('group.transcript.append', payload)).catch(() => {
      /* not recorded — never a room error */
    })
  } catch {
    /* synchronous throw from a stub host — never a room error */
  }
}

/** Test hook: forget client-side dedupe state. */
export function resetGroupTranscriptState(): void {
  recorded.clear()
}
