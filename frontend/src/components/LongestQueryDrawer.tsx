import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Microscope } from 'lucide-react'
import { api } from '../api/client'
import { useConnId } from '../lib/app'
import { fmtDate, fmtSeconds } from '../lib/format'
import { Button, Code, CopyButton, Drawer, ErrorBanner, Skeleton } from './ui'
import ExplainDrawer from './ExplainDrawer'

/** Shows the longest running statement captured by the sample closest to `at` (ISO timestamp). */
export default function LongestQueryDrawer({ at, onClose }: { at: string; onClose: () => void }) {
  const id = useConnId()
  const [explain, setExplain] = useState(false)
  const q = useQuery({ queryKey: ['longest-query', id, at], queryFn: () => api.metricsLongestQuery(id, at) })
  const d = q.data

  if (explain && d?.query) {
    return (
      <ExplainDrawer
        query={{
          query_id: d.query_id ?? '', query: d.query, calls: 0, total_time_ms: 0, mean_time_ms: 0, max_time_ms: null,
          rows: null, disk_reads: null, buffer_gets: null, cache_hit_ratio: null, username: d.username, pct_total_time: null,
        }}
        onClose={() => setExplain(false)}
      />
    )
  }

  return (
    <Drawer title="Consulta más larga en ese momento" onClose={onClose} testId="longest-query-drawer">
      {q.error ? <ErrorBanner error={q.error} onRetry={() => void q.refetch()} /> : q.isLoading || !d ? (
        <Skeleton className="h-40" />
      ) : (
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4" data-testid="longest-query-meta">
            <div><dt className="text-xs text-muted">Momento</dt><dd data-testid="longest-query-ts">{fmtDate(d.ts)}</dd></div>
            <div><dt className="text-xs text-muted">Duración</dt><dd data-testid="longest-query-duration">{fmtSeconds(d.longest_query_seconds)}</dd></div>
            <div><dt className="text-xs text-muted">Usuario</dt><dd>{d.username ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted">Sesión</dt><dd className="font-mono">{d.session_id ?? '—'}</dd></div>
          </dl>
          {d.query ? (
            <>
              {d.query_id && <div className="text-xs text-muted">ID de consulta: <span className="font-mono">{d.query_id}</span></div>}
              <Code data-testid="longest-query-sql" className="max-h-[420px]">{d.query}</Code>
              <div className="flex flex-wrap gap-2">
                <CopyButton text={d.query} label="Copiar SQL" testId="longest-query-copy-btn" />
                <Button data-testid="longest-query-explain-btn" onClick={() => setExplain(true)}>
                  <Microscope size={14} /> Analizar plan
                </Button>
              </div>
            </>
          ) : (
            <div className="rounded-md border border-line bg-surface2 px-3 py-2 text-sm text-muted" data-testid="longest-query-empty">
              {d.longest_query_seconds == null
                ? 'No había consultas en ejecución en ese momento.'
                : 'No se registró el texto de la consulta para esta muestra (muestras tomadas antes de esta versión o sin privilegios para ver el SQL).'}
            </div>
          )}
        </div>
      )}
    </Drawer>
  )
}
