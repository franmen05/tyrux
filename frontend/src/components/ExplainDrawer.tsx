import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Microscope } from 'lucide-react'
import { api } from '../api/client'
import type { TopQuery } from '../api/types'
import { useConnId } from '../lib/app'
import { fmtNumber } from '../lib/format'
import { Button, Code, CopyButton, Drawer, ErrorBanner, SeverityBadge, SEVERITY, inputCls } from './ui'

/** query === null => free SQL mode (user pastes SQL). */
export default function ExplainDrawer({ query, onClose }: { query: TopQuery | null; onClose: () => void }) {
  const id = useConnId()
  const [sql, setSql] = useState('')
  const [analyze, setAnalyze] = useState(false)
  const free = query === null

  const run = useMutation({
    mutationFn: () =>
      api.explain(id, free ? { sql, analyze } : { query_id: query.query_id, sql: query.query, analyze }),
  })
  const res = run.data

  return (
    <Drawer title={free ? 'Analizar SQL' : 'Análisis de consulta'} onClose={onClose} testId="explain-drawer">
      <div className="space-y-4">
        {free ? (
          <textarea
            data-testid="explain-sql-input" value={sql} onChange={(e) => setSql(e.target.value)} rows={7}
            placeholder="Pega aquí la consulta SQL a analizar…" className={`${inputCls} font-mono text-xs`}
          />
        ) : (
          <Code data-testid="explain-query" className="max-h-48">{query.query}</Code>
        )}
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input data-testid="explain-analyze" type="checkbox" checked={analyze} onChange={(e) => setAnalyze(e.target.checked)} />
            EXPLAIN ANALYZE (solo SELECT, ejecuta la consulta)
          </label>
          <Button data-testid="explain-run-btn" variant="primary" loading={run.isPending} disabled={free && !sql.trim()} onClick={() => run.mutate()}>
            <Microscope size={14} /> Analizar
          </Button>
        </div>
        {analyze && <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">La consulta se ejecutará realmente en la base de datos (dentro de una transacción que se revierte).</div>}

        {run.error && <ErrorBanner error={run.error} onRetry={() => run.mutate()} />}

        {res && (
          <>
            {res.total_cost != null && <div className="text-sm text-muted">Costo total estimado: <b className="text-fg">{fmtNumber(res.total_cost, 2)}</b></div>}
            <section data-testid="explain-findings">
              <h3 className="mb-2 text-sm font-semibold">Hallazgos ({res.findings.length})</h3>
              {res.findings.length === 0 ? (
                <div className="text-sm text-muted" data-testid="explain-no-findings">No se detectaron problemas evidentes en el plan.</div>
              ) : (
                <div className="space-y-3">
                  {res.findings.map((f, i) => (
                    <div key={i} data-testid="explain-finding" data-severity={f.severity} className="rounded-lg border border-line p-3">
                      <div className="mb-1 flex items-center gap-2">
                        <span className={`h-2 w-2 rounded-full ${SEVERITY[f.severity].dot}`} />
                        <span className="font-medium">{f.title}</span>
                        <SeverityBadge severity={f.severity} />
                      </div>
                      <p className="text-sm text-muted">{f.detail}</p>
                      {f.sql && (
                        <div className="mt-2">
                          <Code>{f.sql}</Code>
                          <div className="mt-1.5"><CopyButton text={f.sql} label="Copiar SQL" testId="finding-copy-btn" /></div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold">Plan de ejecución</h3>
              <Code data-testid="explain-plan" className="max-h-[420px] whitespace-pre">{res.plan_text}</Code>
            </section>
          </>
        )}
      </div>
    </Drawer>
  )
}
