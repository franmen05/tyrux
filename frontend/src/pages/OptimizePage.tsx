import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Download, FileCode, ShieldCheck } from 'lucide-react'
import { api } from '../api/client'
import type { RecCategory, Recommendation, Severity } from '../api/types'
import { useApp, useConnId } from '../lib/app'
import { downloadText } from '../lib/format'
import { Badge, Button, Card, Code, CopyButton, EmptyState, ErrorBanner, PageHeader, SEVERITY, SeverityBadge, TableSkeleton } from '../components/ui'

const CATEGORY: Record<RecCategory, string> = {
  missing_index: 'Índice faltante', unused_index: 'Índice sin uso', duplicate_index: 'Índice duplicado',
  stale_stats: 'Estadísticas desactualizadas', bloat: 'Bloat / espacio muerto', config: 'Configuración',
  extension: 'Extensión', query: 'Consulta',
}
const ORDER: Severity[] = ['high', 'medium', 'low', 'info']
const GROUP_LABEL: Record<Severity, string> = { high: 'Severidad alta', medium: 'Severidad media', low: 'Severidad baja', info: 'Informativas' }

function RecItem({ r, checked, onToggle }: { r: Recommendation; checked: boolean; onToggle: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div data-testid="recommendation-item" data-rec-id={r.id} data-severity={r.severity} className="rounded-lg border border-line bg-surface p-4">
      <div className="flex items-start gap-3">
        <input data-testid="rec-checkbox" type="checkbox" className="mt-1" checked={checked} onChange={onToggle} aria-label={`Seleccionar ${r.title}`} disabled={!r.sql} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{r.title}</span>
            <SeverityBadge severity={r.severity} />
            <Badge>{CATEGORY[r.category] ?? r.category}</Badge>
          </div>
          <p className="mt-1 text-sm text-muted">{r.detail}</p>
          {r.object_name && <div className="mt-1 text-xs">Objeto: <span className="font-mono">{r.object_name}</span></div>}
          {r.estimated_benefit && <div className="mt-1 text-xs text-emerald-600 dark:text-emerald-400">Beneficio estimado: {r.estimated_benefit}</div>}
          {r.sql && (
            <div className="mt-2">
              <button type="button" data-testid="rec-sql-toggle" onClick={() => setOpen(!open)} className="flex items-center gap-1 text-sm text-brand hover:underline">
                {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {open ? 'Ocultar SQL' : 'Ver SQL'}
              </button>
              {open && (
                <div className="mt-2" data-testid="rec-sql">
                  <Code>{r.sql}</Code>
                  <div className="mt-1.5"><CopyButton text={r.sql} label="Copiar SQL" /></div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function OptimizePage() {
  const id = useConnId()
  const { toast } = useApp()
  const q = useQuery({ queryKey: ['recs', id], queryFn: () => api.recommendations(id) })
  const [sel, setSel] = useState<Set<string>>(new Set())

  const recs = useMemo(() => q.data ?? [], [q.data])
  const selectable = recs.filter((r) => r.sql)
  const allSelected = selectable.length > 0 && selectable.every((r) => sel.has(r.id))
  const toggle = (rid: string) => setSel((s) => { const n = new Set(s); if (n.has(rid)) n.delete(rid); else n.add(rid); return n })

  const gen = useMutation({ mutationFn: () => api.optimizeScript(id, recs.filter((r) => sel.has(r.id)).map((r) => r.id)) })
  const script = gen.data

  return (
    <div>
      <PageHeader title="Optimización" subtitle="Recomendaciones detectadas y un script SQL listo para revisar." />
      <div data-testid="no-exec-notice" className="mb-4 flex items-center gap-2 rounded-lg border border-sky-500/40 bg-sky-500/10 px-4 py-2.5 text-sm">
        <ShieldCheck size={18} className="shrink-0 text-sky-500" /> La herramienta no ejecuta cambios; revisa el script antes de aplicarlo.
      </div>

      {q.isLoading ? <Card><TableSkeleton rows={5} /></Card> : q.error ? <ErrorBanner error={q.error} onRetry={() => void q.refetch()} /> : recs.length === 0 ? (
        <Card><EmptyState title="Sin recomendaciones" description="No se encontraron mejoras pendientes en esta base de datos." /></Card>
      ) : (
        <>
          <div className="sticky top-0 z-10 -mx-1 mb-4 flex flex-wrap items-center gap-3 bg-bg/90 px-1 py-2 backdrop-blur">
            <label className="flex items-center gap-2 text-sm">
              <input data-testid="select-all-recs" type="checkbox" checked={allSelected}
                onChange={() => setSel(allSelected ? new Set() : new Set(selectable.map((r) => r.id)))} /> Seleccionar todo
            </label>
            <span className="text-sm text-muted" data-testid="selected-count">{sel.size} de {recs.length} seleccionadas</span>
            <Button data-testid="generate-script-btn" variant="primary" className="ml-auto" disabled={sel.size === 0} loading={gen.isPending}
              onClick={() => gen.mutate()}>
              <FileCode size={14} /> Generar script
            </Button>
          </div>

          {gen.error && <div className="mb-4"><ErrorBanner error={gen.error} onRetry={() => gen.mutate()} /></div>}
          {script && (
            <Card className="mb-6 p-4">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <h3 className="font-semibold">Script generado</h3>
                <Badge>{script.count} cambio(s)</Badge>
                <div className="ml-auto flex gap-2">
                  <CopyButton text={script.script} testId="copy-script-btn" />
                  <Button data-testid="download-script-btn" onClick={() => { downloadText(`optimizacion-${script.engine}.sql`, script.script); toast('Archivo .sql descargado') }}>
                    <Download size={14} /> Descargar .sql
                  </Button>
                </div>
              </div>
              <Code data-testid="script-output" className="max-h-[420px] whitespace-pre">{script.script}</Code>
            </Card>
          )}

          <div className="space-y-6">
            {ORDER.map((sev) => {
              const items = recs.filter((r) => r.severity === sev)
              if (!items.length) return null
              return (
                <section key={sev} data-testid={`rec-group-${sev}`}>
                  <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                    <span className={`h-2.5 w-2.5 rounded-full ${SEVERITY[sev].dot}`} /> {GROUP_LABEL[sev]} ({items.length})
                  </h2>
                  <div className="space-y-2">{items.map((r) => <RecItem key={r.id} r={r} checked={sel.has(r.id)} onToggle={() => toggle(r.id)} />)}</div>
                </section>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
