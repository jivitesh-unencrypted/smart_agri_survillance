import { useEffect, useRef, useState, useCallback } from 'react'
import { WS_URL, getToken } from '../lib/api'
import { useUserScope } from '../lib/userScope'

/**
 * Connects to the backend WebSocket and keeps a rolling buffer of the
 * most recent events, plus convenience derived state (live counts per
 * camera, connection status). Auto-reconnects with backoff so a
 * restarted backend is picked back up without a page reload.
 *
 * The socket is AUTHENTICATED and OWNER-SCOPED. A browser cannot set headers
 * on a WebSocket handshake, so the access token is passed as `?token=` and the
 * server registers the connection under that user. The server then delivers
 * only that user's events, so a signed-in second account never sees the first
 * account's live detections, alerts or camera telemetry.
 *
 * `userScope.epoch` is a dependency on purpose: when the signed-in identity
 * changes, the old socket is still bound to the previous user's token, so the
 * effect must tear it down and reconnect. Without that, switching accounts in
 * the same tab would leave the dashboard streaming the previous user's live
 * events even though the REST queries were already correctly re-scoped.
 */
export function useLiveSocket({ maxEvents = 50 } = {}) {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState([])
  const [lastByType, setLastByType] = useState({})
  const wsRef = useRef(null)
  const retryRef = useRef(0)
  const listenersRef = useRef(new Set())
  // `useUserScope()` returns the epoch value itself (a number), not an object.
  const epoch = useUserScope()

  const subscribe = useCallback((fn) => {
    listenersRef.current.add(fn)
    return () => listenersRef.current.delete(fn)
  }, [])

  useEffect(() => {
    let cancelled = false
    let timeoutId

    // Any buffered events belonged to the previous identity.
    setEvents([])
    setLastByType({})
    setConnected(false)

    function connect() {
      if (cancelled) return

      const token = getToken()
      if (!token) {
        // Not signed in (or still restoring the session). Retry on the usual
        // backoff rather than spinning - the epoch change re-runs this effect
        // as soon as a real session exists.
        setConnected(false)
        const delay = Math.min(1000 * 2 ** retryRef.current, 15000)
        retryRef.current += 1
        timeoutId = setTimeout(connect, delay)
        return
      }

      const sep = WS_URL.includes('?') ? '&' : '?'
      const ws = new WebSocket(`${WS_URL}${sep}token=${encodeURIComponent(token)}`)
      wsRef.current = ws

      ws.onopen = () => {
        retryRef.current = 0
        setConnected(true)
      }
      ws.onclose = () => {
        setConnected(false)
        if (cancelled) return
        const delay = Math.min(1000 * 2 ** retryRef.current, 15000)
        retryRef.current += 1
        timeoutId = setTimeout(connect, delay)
      }
      ws.onerror = () => {
        ws.close()
      }
      ws.onmessage = (msg) => {
        let data
        try {
          data = JSON.parse(msg.data)
        } catch {
          return
        }
        setEvents((prev) => [data, ...prev].slice(0, maxEvents))
        setLastByType((prev) => ({ ...prev, [data.type]: data }))
        listenersRef.current.forEach((fn) => fn(data))
      }
    }

    connect()
    return () => {
      cancelled = true
      clearTimeout(timeoutId)
      wsRef.current?.close()
    }
  }, [maxEvents, epoch])

  return { connected, events, lastByType, subscribe }
}
