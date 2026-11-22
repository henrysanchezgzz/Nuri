import { useEffect, useRef, useState } from 'react'
import { wsUrl } from '../lib/api'

/**
 * Conexión WebSocket a /ws/live con reconexión exponencial y ping de mantenimiento.
 * Devuelve el último snapshot del radar, el estado de conexión y la hora del último tick.
 */
export function useLiveSocket() {
  const [snapshot, setSnapshot] = useState(null)
  const [connected, setConnected] = useState(false)
  const [lastTick, setLastTick] = useState(null)
  const wsRef = useRef(null)
  const retryRef = useRef(0)
  const timerRef = useRef(null)
  const pingRef = useRef(null)
  const versionRef = useRef(null)

  useEffect(() => {
    let alive = true

    const connect = () => {
      if (!alive) return
      const ws = new WebSocket(wsUrl())
      wsRef.current = ws

      ws.onopen = () => {
        retryRef.current = 0
        setConnected(true)
        clearInterval(pingRef.current)
        pingRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send('ping')
        }, 20000)
      }

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'snapshot') {
            // Si NURI se actualizó, recargamos para usar la interfaz nueva.
            if (msg.version) {
              if (versionRef.current && versionRef.current !== msg.version) {
                window.location.reload()
                return
              }
              versionRef.current = msg.version
            }
            setSnapshot(msg)
            setLastTick(Date.now())
          }
        } catch {
          /* mensaje inválido: se ignora */
        }
      }

      ws.onclose = () => {
        setConnected(false)
        clearInterval(pingRef.current)
        if (!alive) return
        const delay = Math.min(1000 * 2 ** retryRef.current, 15000)
        retryRef.current += 1
        timerRef.current = setTimeout(connect, delay)
      }

      ws.onerror = () => ws.close()
    }

    connect()
    return () => {
      alive = false
      clearTimeout(timerRef.current)
      clearInterval(pingRef.current)
      wsRef.current?.close()
    }
  }, [])

  return { snapshot, connected, lastTick }
}

/** Recuerda el valor anterior de una cuota para animar el cambio. */
export function usePrevious(value) {
  const ref = useRef(value)
  const prev = ref.current
  useEffect(() => {
    ref.current = value
  }, [value])
  return prev
}
