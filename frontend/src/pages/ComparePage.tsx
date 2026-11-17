import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { ArrowLeftRight, Download, FileCode, GitCompare, ShieldCheck } from 'lucide-react'
import { api } from '../api/client'
import type { IndexCompareItem, IndexDiffStatus } from '../api/types'
import { useApp } from '../lib/app'
import { downloadText } from '../lib/format'
import {
  Badge, Button, Card, Code, CopyButton, DataTable, EmptyState, ErrorBanner, Field, PageHeader, TableSkeleton,
  cn, inputCls, type Column,
} from '../components/ui'

const ENGINE_LABEL = { postgres: 'PostgreSQL', oracle: 'Oracle' } as const

const STATUS: Record<IndexDiffStatus, { label: string; cls: string }> = {
  missing: { label: 'Falta en destino', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/40' },
  different: { label: 'Distinto', cls: 'bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/40' },
  extra: { label: 'Solo en destino', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/40' },
  same: { label: 'Igual', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/40' },
}
const FILTERS: ('all' | IndexDiffStatus)[] = ['all', 'missing', 'different', 'extra', 'same']

function StatusBadge({ status }: { status: IndexDiffStatus }) {
  return (
    <span data-testid="compare-status" data-status={status}
      className={cn('inline-flex whitespace-nowrap items-center rounded-full border px-2 py-0.5 text-xs font-medium', STATUS[status].cls)}>
      {STATUS[status].label}
    </span>
  )
}

function IndexCell({ name, columns, unique, primary }: { name: string | null; columns: string[]; unique: boolean | null; primary?: boolean }) {
  if (!name) return <span className="text-muted">—</span>
  return (
    <div>
      <div className="font-mono text-xs">{name}</div>
      <div className="mt-0.5 text-xs text-muted">({columns.join(', ')})</div>
      <div className="mt-0.5 flex gap-1">
        {primary && <Badge>PK</Badge>}
        {unique && !primary && <Badge>Único</Badge>}
      </div>
    </div>
  )
}

export default function ComparePage() {
  const { connections, isLoading, error, refetch, selectedId, toast } = useApp()
  const [source, setSource] = useState<number | null>(null)
  const [target, setTarget] = useState<number | null>(null)
  const [filter, setFilter] = useState<'all' | IndexDiffStatus>('all')
  const [sel, setSel] = useState<Set<string>>(new Set())

  // Defaults: source = active connection, target = first other connection.
  useEffect(() => {
    if (!connections.length) return
    const ids = connections.map((c) => c.id)
    const src = source !== null && ids.includes(source) ? source : (selectedId ?? ids[0])
    const tgt = target !== null && ids.includes(target) ? target : (ids.find((x) => x !== src) ?? null)
    if (src !== source) setSource(src)
    if (tgt !== target) setTarget(tgt)
  }, [connections, selectedId, source, target])

  const cmp = useMutation({
    mutationFn: () => api.compareIndexes(source!, target!),
    onSuccess: () => { setSel(new Set()); gen.reset() },
  })
  const result = cmp.data
  const gen = useMutation({
    mutationFn: (ids: string[]) => api.compareIndexesScript(result!.source.id, result!.target.id, ids),
  })
  const script = gen.data

  const items = useMemo(() => result?.items ?? [], [result])
  const view = filter === 'all' ? items : items.filter((i) => i.status === filter)
  const selectable = view.filter((i) => i.copiable)
  const allSelected = selectable.length > 0 && selectable.every((i) => sel.has(i.id))
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const toggleAll = () => setSel((s) => {
    const n = new Set(s)
    selectable.forEach((i) => (allSelected ? n.delete(i.id) : n.add(i.id)))
    return n
  })

  if (isLoading) return <div className="p-8 text-muted" data-testid="loading">Cargando…</div>
  if (error) return <ErrorBanner error={error} onRetry={refetch} />
  if (connections.length < 2)
    return (
      <div>
        <PageHeader title="Comparar índices" />
        <Card>
          <EmptyState title="Se necesitan dos conexiones"
            description="Configura al menos dos bases de datos (origen y destino) para comparar sus índices."
            action={<Link to="/connections"><Button variant="primary" data-testid="goto-connections">Ir a Conexiones</Button></Link>} />
        </Card>
      </div>
    )

  const name = (id: number | null) => connections.find((c) => c.id === id)
  const sameConn = source !== null && source === target

  const columns: Column<IndexCompareItem>[] = [
    {
      key: 'sel', header: '', render: (r) => (
        <input data-testid="compare-checkbox" type="checkbox" checked={sel.has(r.id)} disabled={!r.copiable}
          onChange={() => toggle(r.id)} aria-label={`Seleccionar ${r.source_index ?? r.target_index}`}
          title={r.copiable ? undefined : 'No se puede copiar'} />
      ),
    },
    { key: 'status', header: 'Estado', render: (r) => <StatusBadge status={r.status} />, sort: (r) => r.status },
    { key: 'table', header: 'Tabla', render: (r) => <span className="font-mono text-xs">{r.table}</span>, sort: (r) => r.table.toLowerCase() },
    {
      key: 'source', header: 'Origen', sort: (r) => r.source_index?.toLowerCase(),
      render: (r) => <IndexCell name={r.source_index} columns={r.source_columns} unique={r.source_unique} primary={r.is_primary && !!r.source_index} />,
    },
    {
      key: 'target', header: 'Destino', sort: (r) => r.target_index?.toLowerCase(),
      render: (r) => <IndexCell name={r.target_index} columns={r.target_columns} unique={r.target_unique} primary={r.is_primary && r.status === 'extra'} />,
    },
    {
      key: 'note', header: 'Detalle', className: 'max-w-md', render: (r) => (
        <div className="text-xs">
          {r.note && <div data-testid="compare-note" className="text-muted">{r.note}</div>}
          {r.sql && (
            <details className="mt-1">
              <summary data-testid="compare-sql-toggle" className="cursor-pointer text-brand">Ver SQL</summary>
              <Code data-testid="compare-sql" className="mt-1 whitespace-pre">{r.sql}</Code>
            </details>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader title="Comparar índices" subtitle="Compara los índices de dos bases de datos y genera el SQL para copiar los que faltan." />
      <div data-testid="no-exec-notice" className="mb-4 flex items-center gap-2 rounded-lg border border-sky-500/40 bg-sky-500/10 px-4 py-2.5 text-sm">
        <ShieldCheck size={18} className="shrink-0 text-sky-500" /> La herramienta no ejecuta cambios; el script se ejecuta manualmente en la base de datos de destino.
      </div>

      <Card className="mb-4 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-64 max-w-full">
            <Field label="Origen (desde)">
              <select data-testid="compare-source" className={inputCls} value={source ?? ''} onChange={(e) => setSource(Number(e.target.value))}>
                {connections.map((c) => <option key={c.id} value={c.id}>{c.name} · {ENGINE_LABEL[c.engine]}</option>)}
              </select>
            </Field>
          </div>
          <Button data-testid="compare-swap" variant="ghost" aria-label="Intercambiar origen y destino" title="Intercambiar"
            onClick={() => { setSource(target); setTarget(source) }}>
            <ArrowLeftRight size={16} />
          </Button>
          <div className="w-64 max-w-full">
            <Field label="Destino (hacia)">
              <select data-testid="compare-target" className={inputCls} value={target ?? ''} onChange={(e) => setTarget(Number(e.target.value))}>
                {connections.map((c) => <option key={c.id} value={c.id}>{c.name} · {ENGINE_LABEL[c.engine]}</option>)}
              </select>
            </Field>
          </div>
          <Button data-testid="compare-run-btn" variant="primary" disabled={source === null || target === null || sameConn}
            loading={cmp.isPending} onClick={() => cmp.mutate()}>
            <GitCompare size={14} /> Comparar
          </Button>
        </div>
        {sameConn && <div data-testid="compare-same-warning" className="mt-2 text-sm text-amber-600 dark:text-amber-400">Elige dos conexiones distintas.</div>}
      </Card>

      {cmp.error && <div className="mb-4"><ErrorBanner error={cmp.error} onRetry={() => cmp.mutate()} /></div>}
      {cmp.isPending && <Card><TableSkeleton rows={5} /></Card>}

      {result && !cmp.isPending && (
        <>
          <div data-testid="compare-summary" className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted">
              {name(result.source.id)?.name ?? result.source.name} → {name(result.target.id)?.name ?? result.target.name}:
            </span>
            {(['missing', 'different', 'extra', 'same'] as IndexDiffStatus[]).map((s) => (
              <span key={s} data-testid={`compare-count-${s}`} className={cn('rounded-full border px-2 py-0.5 text-xs font-medium', STATUS[s].cls)}>
                {STATUS[s].label}: {result.counts[s] ?? 0}
              </span>
            ))}
          </div>

          <div className="sticky top-0 z-10 -mx-1 mb-4 flex flex-wrap items-center gap-3 bg-bg/90 px-1 py-2 backdrop-blur">
            <label className="flex items-center gap-2 text-sm">
              <input data-testid="compare-select-all" type="checkbox" checked={allSelected} disabled={selectable.length === 0} onChange={toggleAll} />
              Seleccionar todos ({selectable.length} copiables)
            </label>
            <span className="text-sm text-muted" data-testid="compare-selected-count">{sel.size} seleccionados</span>
            <Button data-testid="compare-generate-btn" variant="primary" className="ml-auto" disabled={sel.size === 0} loading={gen.isPending}
              onClick={() => gen.mutate(items.filter((i) => sel.has(i.id)).map((i) => i.id))}>
              <FileCode size={14} /> Generar SQL
            </Button>
          </div>

          {gen.error && <div className="mb-4"><ErrorBanner error={gen.error} /></div>}
          {script && (
            <Card className="mb-6 p-4">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <h3 className="font-semibold">Script generado</h3>
                <Badge>{script.count} índice(s)</Badge>
                <Badge>{ENGINE_LABEL[script.engine]}</Badge>
                <div className="ml-auto flex gap-2">
                  <CopyButton text={script.script} testId="compare-copy-btn" />
                  <Button data-testid="compare-download-btn" onClick={() => { downloadText(`copiar-indices-${script.engine}.sql`, script.script); toast('Archivo .sql descargado') }}>
                    <Download size={14} /> Descargar .sql
                  </Button>
                </div>
              </div>
              <Code data-testid="compare-script-output" className="max-h-[420px] whitespace-pre">{script.script}</Code>
            </Card>
          )}

          <Card>
            <DataTable
              columns={columns}
              rows={view}
              rowKey={(r) => r.id}
              rowTestId="compare-row"
              filterText={(r) => `${r.table} ${r.source_index ?? ''} ${r.target_index ?? ''} ${r.source_columns.join(' ')} ${r.target_columns.join(' ')}`}
              placeholder="Filtrar por tabla, índice o columna…"
              rowClass={(r) => (r.copiable ? '' : 'text-muted')}
              toolbar={
                <div className="flex flex-wrap gap-1">
                  {FILTERS.map((k) => (
                    <button key={k} type="button" data-testid={`compare-filter-${k}`} aria-pressed={filter === k} onClick={() => setFilter(k)}
                      className={cn('rounded-md border px-2.5 py-1 text-sm', filter === k ? 'border-brand bg-brand text-brand-fg' : 'border-line bg-surface hover:bg-surface2')}>
                      {k === 'all' ? `Todos (${items.length})` : `${STATUS[k].label} (${result.counts[k] ?? 0})`}
                    </button>
                  ))}
                </div>
              }
              empty={<EmptyState title="Sin índices" description="No hay índices que coincidan con el filtro." />}
            />
          </Card>
        </>
      )}
    </div>
  )
}
