import { useEffect, useRef, useState, useCallback } from 'react'
import { WS_URL } from '../lib/api'

/**
 * Connects to the backend WebSocket and keeps a rolling buffer of the
 * most recent events, plus convenience derived state (live counts per
 * camera, connection status). Auto-reconnects with backoff so a
 * restarted backend is picked back up without a page reload.
 */
export function useLiveSocket({ maxEvents = 50 } = {}) {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState([])
  const [lastByType, setLastByType] = useState({})
  const wsRef = useRef(null)
  const retryRef = useRef(0)
  const listenersRef = useRef(new Set())

  const subscribe = useCallback((fn) => {
    listenersRef.current.add(fn)
    return () => listenersRef.current.delete(fn)
  }, [])

  useEffect(() => {
    let cancelled = false
    let timeoutId

    function connect() {
      if (cancelled) return
      const ws = new WebSocket(WS_URL)
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
  }, [maxEvents])

  return { connected, events, lastByType, subscribe }
}
