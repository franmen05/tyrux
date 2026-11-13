import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ConnectionOut } from '../api/types'
import { safeStorageGet, safeStorageSet } from './format'
import { CheckCircle2, AlertCircle } from 'lucide-react'

const KEY = 'dbmonitor.connectionId'

interface Toast { id: number; text: string; kind: 'ok' | 'error' }

interface AppCtx {
  connections: ConnectionOut[]
  isLoading: boolean
  error: Error | null
  refetch: () => void
  selectedId: number | null
  selected: ConnectionOut | null
  select: (id: number | null) => void
  toast: (text: string, kind?: 'ok' | 'error') => void
}

const Ctx = createContext<AppCtx | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const q = useQuery({ queryKey: ['connections'], queryFn: api.listConnections, refetchInterval: 15000 })
  const [rawId, setRawId] = useState<number | null>(() => {
    const v = safeStorageGet(KEY)
    return v ? Number(v) || null : null
  })
  const [toasts, setToasts] = useState<Toast[]>([])
  const connections = useMemo(() => q.data ?? [], [q.data])

  // Resolve a valid selection once the list is known.
  const selectedId = useMemo(() => {
    if (!q.data) return rawId
    if (rawId !== null && q.data.some((c) => c.id === rawId)) return rawId
    return q.data.length ? q.data[0].id : null
  }, [q.data, rawId])

  useEffect(() => {
    if (q.data && selectedId !== rawId) {
      setRawId(selectedId)
      safeStorageSet(KEY, selectedId === null ? null : String(selectedId))
    }
  }, [q.data, selectedId, rawId])

  const select = useCallback((id: number | null) => {
    setRawId(id)
    safeStorageSet(KEY, id === null ? null : String(id))
  }, [])

  const toast = useCallback((text: string, kind: 'ok' | 'error' = 'ok') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3500)
  }, [])

  const value: AppCtx = {
    connections,
    isLoading: q.isLoading,
    error: q.error as Error | null,
    refetch: () => void q.refetch(),
    selectedId,
    selected: connections.find((c) => c.id === selectedId) ?? null,
    select,
    toast,
  }

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2" data-testid="toast-container">
        {toasts.map((t) => (
          <div
            key={t.id}
            data-testid="toast"
            className="flex items-center gap-2 rounded-lg border border-line bg-surface px-4 py-2.5 text-sm shadow-lg"
          >
            {t.kind === 'ok' ? (
              <CheckCircle2 size={16} className="text-emerald-500" />
            ) : (
              <AlertCircle size={16} className="text-red-500" />
            )}
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}

export function useApp(): AppCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('AppProvider missing')
  return v
}

/** For pages that require a selected connection. */
export function useConnId(): number {
  const { selectedId } = useApp()
  return selectedId as number
}
