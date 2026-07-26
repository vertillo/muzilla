import { useEffect, useRef, useState } from 'react'
import type { JobEvent } from '@/lib/types'

export interface JobEventsState {
  events: JobEvent[]
  latestProgress: { current: number; total: number | null; message: string | null } | null
  isComplete: boolean
  terminalState: string | null
  error: string | null
}

const INITIAL_STATE: JobEventsState = {
  events: [],
  latestProgress: null,
  isComplete: false,
  terminalState: null,
  error: null,
}

/** Subscribes to GET /api/jobs/{id}/events (docs/PLAN.md §9: SSE, not
 * WebSockets). Not a React Query hook — SSE is push, not
 * request/response — a plain EventSource accumulating events into
 * local state, closed on unmount or once the job reaches a terminal
 * state. */
export function useJobEvents(jobId: number | null): JobEventsState {
  const [state, setState] = useState<JobEventsState>(INITIAL_STATE)
  const lastSeqRef = useRef(0)

  useEffect(() => {
    if (jobId === null) {
      setState(INITIAL_STATE)
      return
    }
    setState(INITIAL_STATE)
    lastSeqRef.current = 0

    const source = new EventSource(`/api/jobs/${jobId}/events`)

    source.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as JobEvent
      lastSeqRef.current = event.seq
      setState((prev) => {
        const next: JobEventsState = { ...prev, events: [...prev.events, event] }
        if (event.kind === 'progress') {
          next.latestProgress = {
            current: Number(event.payload.current ?? 0),
            total: event.payload.total === null ? null : Number(event.payload.total),
            message: (event.payload.message as string | null) ?? null,
          }
        }
        return next
      })
    }

    source.addEventListener('done', (msg) => {
      const payload = JSON.parse((msg as MessageEvent).data) as { state: string }
      setState((prev) => ({ ...prev, isComplete: true, terminalState: payload.state }))
      source.close()
    })

    source.addEventListener('error', (msg) => {
      // A plain EventSource network error (msg.data undefined) is
      // distinct from our server-sent "event: error" (job not found);
      // only the latter carries a JSON body.
      const raw = (msg as MessageEvent).data
      if (raw) {
        const payload = JSON.parse(raw) as { detail: string }
        setState((prev) => ({ ...prev, error: payload.detail, isComplete: true }))
      }
      source.close()
    })

    return () => source.close()
  }, [jobId])

  return state
}
