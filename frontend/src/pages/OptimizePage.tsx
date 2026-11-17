import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronLeft, ChevronRight, Download, FileCode, RefreshCw, ShieldCheck } from 'lucide-react'
import { api } from '../api/client'
import type { RecCategory, RecFilters, Recommendation, ScriptRequest, Severity } from '../api/types'
import { useApp, useConnId } from '../lib/app'
import { downloadText, fmtDate } from '../lib/format'
import { Badge, Button, Card, Code, CopyButton, EmptyState, ErrorBanner, PageHeader, SEVERITY, SeverityBadge, TableSkeleton, inputCls } from '../components/ui'

const CATEGORY: Record<RecCategory, string> = {
  missing_index: 'Índice faltante', unused_index: 'Índice sin uso', duplicate_index: 'Índice duplicado',
  stale_stats: 'Estadísticas desactualizadas', bloat: 'Bloat / espacio muerto', config: 'Configuración',
  extension: 'Extensión', query: 'Consulta',
}
const ORDER: Severity[] = ['high', 'medium', 'low', 'info']
const GROUP_LABEL: Record<Severity, string> = { high: 'Severidad alta', medium: 'Severidad media', low: 'Severidad baja', info: 'Informativas' }
const PAGE_SIZES = [25, 50, 100, 200]
const SCRIPT_PREVIEW_CHARS = 200_000
const filterCls = inputCls.replace('w-full', '')

/** Explicit ids (kept across pages and filters), or "all matching `filters`" minus `excluded`. */
type Selection = { all: false; ids: Set<string> } | { all: true; filters: RecFilters; excluded: Set<string> }
const NONE: Selection = { all: false, ids: new Set() }

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
  const qc = useQueryClient()
  const [pageSize, setPageSize] = useState(50)
  const [pageIdx, setPageIdx] = useState(0)
  const [severity, setSeverity] = useState<Severity | ''>('')
  const [category, setCategory] = useState<RecCategory | ''>('')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [sel, setSel] = useState<Selection>(NONE)

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const filters = useMemo<RecFilters>(() => ({
    severity: severity || undefined, category: category || undefined, search: search || undefined,
  }), [severity, category, search])
  const filterKey = JSON.stringify(filters)

  // new filters: back to page 1; a "select all" made for other filters no longer applies
  useEffect(() => {
    setPageIdx(0)
    setSel((s) => (s.all && JSON.stringify(s.filters) !== filterKey ? NONE : s))
  }, [filterKey])
  // another connection: start over
  useEffect(() => { setSel(NONE); setPageIdx(0) }, [id])

  const offset = pageIdx * pageSize
  const q = useQuery({
    queryKey: ['recs', id, offset, pageSize, filterKey],
    queryFn: () => api.recommendationsPage(id, offset, pageSize, filters),
    placeholderData: keepPreviousData,
  })
  const data = q.data
  const items = useMemo(() => data?.items ?? [], [data])
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / pageSize))

  const refresh = useMutation({
    mutationFn: () => api.recommendationsPage(id, 0, pageSize, filters, true),
    onSuccess: () => {
      setSel(NONE)
      setPageIdx(0)
      void qc.invalidateQueries({ queryKey: ['recs', id] })
      toast('Recomendaciones recalculadas')
    },
  })

  const isChecked = (r: Recommendation) => (sel.all ? !!r.sql && !sel.excluded.has(r.id) : sel.ids.has(r.id))
  const toggle = (r: Recommendation) => setSel((s) => {
    if (s.all) {
      const excluded = new Set(s.excluded)
      if (excluded.has(r.id)) excluded.delete(r.id); else excluded.add(r.id)
      return { ...s, excluded }
    }
    const ids = new Set(s.ids)
    if (ids.has(r.id)) ids.delete(r.id); else ids.add(r.id)
    return { all: false, ids }
  })
  const selectedCount = sel.all ? Math.max(0, (data?.selectable_total ?? 0) - sel.excluded.size) : sel.ids.size
  const allSelected = sel.all && sel.excluded.size === 0 && (data?.selectable_total ?? 0) > 0
  const pageSelectable = items.filter((r) => r.sql)
  const pageAllChecked = pageSelectable.length > 0 && pageSelectable.every(isChecked)
  const togglePage = () => setSel((s) => {
    if (s.all) {
      const excluded = new Set(s.excluded)
      pageSelectable.forEach((r) => (pageAllChecked ? excluded.add(r.id) : excluded.delete(r.id)))
      return { ...s, excluded }
    }
    const ids = new Set(s.ids)
    pageSelectable.forEach((r) => (pageAllChecked ? ids.delete(r.id) : ids.add(r.id)))
    return { all: false, ids }
  })

  const gen = useMutation({
    mutationFn: () => {
      const body: ScriptRequest = sel.all
        ? { select_all: true, exclude_ids: [...sel.excluded], ...sel.filters }
        : { recommendation_ids: [...sel.ids] }
      return api.optimizeScript(id, body)
    },
  })
  const script = gen.data
  const preview = script && script.script.length > SCRIPT_PREVIEW_CHARS
    ? script.script.slice(0, SCRIPT_PREVIEW_CHARS) : script?.script

  const filtersActive = !!(severity || category || search)

  return (
    <div>
      <PageHeader title="Optimización" subtitle="Recomendaciones detectadas y un script SQL listo para revisar."
        actions={data && (
          <Button data-testid="rec-refresh-btn" loading={refresh.isPending} onClick={() => refresh.mutate()}>
            <RefreshCw size={14} /> Recalcular
          </Button>
        )} />
      <div data-testid="no-exec-notice" className="mb-4 flex items-center gap-2 rounded-lg border border-sky-500/40 bg-sky-500/10 px-4 py-2.5 text-sm">
        <ShieldCheck size={18} className="shrink-0 text-sky-500" /> La herramienta no ejecuta cambios; revisa el script antes de aplicarlo.
      </div>

      {refresh.error && <div className="mb-4"><ErrorBanner error={refresh.error} onRetry={() => refresh.mutate()} /></div>}
      {data && data.warnings.length > 0 && (
        <div data-testid="rec-warnings" className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-sm">
          <div className="flex items-center gap-2 font-medium"><AlertTriangle size={16} className="shrink-0 text-amber-500" /> Resultado parcial: algunas comprobaciones no se completaron</div>
          <ul className="mt-1 list-disc pl-6 text-muted">{data.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}

      {q.isLoading ? (
        <Card className="p-4">
          <p className="mb-3 text-sm text-muted" data-testid="rec-loading">Analizando la base de datos… en bases de datos grandes puede tardar.</p>
          <TableSkeleton rows={5} />
        </Card>
      ) : q.error && !data ? <ErrorBanner error={q.error} onRetry={() => void q.refetch()} /> : !data || data.total_all === 0 ? (
        <Card><EmptyState title="Sin recomendaciones" description="No se encontraron mejoras pendientes en esta base de datos." /></Card>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <select data-testid="rec-filter-severity" aria-label="Severidad" className={`${filterCls} w-auto`} value={severity} onChange={(e) => setSeverity(e.target.value as Severity | '')}>
              <option value="">Todas las severidades ({data.total_all})</option>
              {ORDER.map((s) => <option key={s} value={s}>{SEVERITY[s].label} ({data.by_severity[s] ?? 0})</option>)}
            </select>
            <select data-testid="rec-filter-category" aria-label="Categoría" className={`${filterCls} w-auto`} value={category} onChange={(e) => setCategory(e.target.value as RecCategory | '')}>
              <option value="">Todas las categorías</option>
              {(Object.keys(CATEGORY) as RecCategory[]).filter((c) => data.by_category[c]).map((c) => (
                <option key={c} value={c}>{CATEGORY[c]} ({data.by_category[c]})</option>
              ))}
            </select>
            <input data-testid="rec-search" type="search" placeholder="Buscar (tabla, índice, texto…)" className={`${filterCls} w-56`}
              value={searchInput} onChange={(e) => setSearchInput(e.target.value)} />
            <span className="ml-auto text-xs text-muted" data-testid="rec-generated-at">
              Calculado {fmtDate(data.generated_at)}{data.elapsed_ms != null ? ` en ${(data.elapsed_ms / 1000).toFixed(1)} s` : ''}
            </span>
          </div>

          <div className="sticky top-0 z-10 -mx-1 mb-4 flex flex-wrap items-center gap-3 bg-bg/90 px-1 py-2 backdrop-blur">
            <label className="flex items-center gap-2 text-sm">
              <input data-testid="select-all-recs" type="checkbox" checked={allSelected} disabled={data.selectable_total === 0}
                onChange={() => setSel(allSelected ? NONE : { all: true, filters, excluded: new Set() })} />
              Seleccionar todas{total > items.length ? ` (${data.selectable_total}, todas las páginas)` : ''}
            </label>
            {total > items.length && (
              <Button data-testid="select-page-recs" onClick={togglePage} disabled={pageSelectable.length === 0}>
                {pageAllChecked ? 'Quitar página' : 'Seleccionar página'}
              </Button>
            )}
            <span className="text-sm text-muted" data-testid="selected-count">{selectedCount} de {total} seleccionadas</span>
            <Button data-testid="generate-script-btn" variant="primary" className="ml-auto" disabled={selectedCount === 0} loading={gen.isPending}
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
              {preview !== script.script && (
                <p className="mb-2 text-xs text-muted" data-testid="script-truncated">
                  Vista previa limitada a los primeros {SCRIPT_PREVIEW_CHARS.toLocaleString('es')} caracteres; copie o descargue el script completo.
                </p>
              )}
              <Code data-testid="script-output" className="max-h-[420px] whitespace-pre">{preview}</Code>
            </Card>
          )}

          {total === 0 ? (
            <Card><EmptyState title="Sin resultados" description="Ninguna recomendación coincide con los filtros." /></Card>
          ) : (
            <div className={`space-y-6 ${q.isPlaceholderData ? 'opacity-60' : ''}`} data-testid="rec-list">
              {ORDER.map((sev) => {
                const group = items.filter((r) => r.severity === sev)
                if (!group.length) return null
                return (
                  <section key={sev} data-testid={`rec-group-${sev}`}>
                    <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                      <span className={`h-2.5 w-2.5 rounded-full ${SEVERITY[sev].dot}`} /> {GROUP_LABEL[sev]} ({group.length}{pages > 1 ? ' en esta página' : ''})
                    </h2>
                    <div className="space-y-2">{group.map((r) => <RecItem key={r.id} r={r} checked={isChecked(r)} onToggle={() => toggle(r)} />)}</div>
                  </section>
                )
              })}
            </div>
          )}

          {total > 0 && (
            <div data-testid="rec-pagination" className="mt-6 flex flex-wrap items-center gap-3 text-sm">
              <Button data-testid="rec-page-prev" disabled={pageIdx === 0} onClick={() => setPageIdx((p) => Math.max(0, p - 1))}>
                <ChevronLeft size={14} /> Anterior
              </Button>
              <span data-testid="rec-page-info">
                Página {Math.min(pageIdx + 1, pages)} de {pages} · {offset + 1}–{Math.min(offset + pageSize, total)} de {total}{filtersActive ? ` (filtradas de ${data.total_all})` : ''}
              </span>
              <Button data-testid="rec-page-next" disabled={pageIdx + 1 >= pages} onClick={() => setPageIdx((p) => p + 1)}>
                Siguiente <ChevronRight size={14} />
              </Button>
              <label className="ml-auto flex items-center gap-2">
                Por página
                <select data-testid="rec-page-size" className={`${filterCls} w-auto`} value={pageSize}
                  onChange={(e) => { setPageSize(Number(e.target.value)); setPageIdx(0) }}>
                  {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
            </div>
          )}
        </>
      )}
    </div>
  )
}
