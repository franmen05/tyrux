import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Lock, RefreshCw, Microscope } from 'lucide-react'
import { api } from '../api/client'
import type { ActiveQuery, ConfigParam, IndexStat, LockInfo, TableStat, TopOrder, TopQuery } from '../api/types'
import { useConnId } from '../lib/app'
import { fmtBytes, fmtDate, fmtMs, fmtNumber, fmtPct, fmtSeconds, truncate } from '../lib/format'
import { Badge, Button, Card, Code, DataTable, EmptyState, ErrorBanner, PageHeader, TableSkeleton, cn, inputCls, type Column } from '../components/ui'
import ExplainDrawer from '../components/ExplainDrawer'

type Tab = 'active' | 'top' | 'indexes' | 'tables' | 'locks' | 'config'
const TABS: { id: Tab; label: string }[] = [
  { id: 'active', label: 'Consultas activas' }, { id: 'top', label: 'Top consultas' }, { id: 'indexes', label: 'Índices' },
  { id: 'tables', label: 'Tablas' }, { id: 'locks', label: 'Bloqueos' }, { id: 'config', label: 'Configuración' },
]

const sql = (s: string) => <span className="font-mono text-xs" title={s}>{truncate(s, 110)}</span>

function QueryState({ q, children }: { q: { isLoading: boolean; error: unknown; refetch: () => unknown }; children: React.ReactNode }) {
  if (q.isLoading) return <TableSkeleton />
  if (q.error) return <div className="p-4"><ErrorBanner error={q.error} onRetry={() => void q.refetch()} /></div>
  return <>{children}</>
}

// ------------------------------------------------------------- Active queries
function ActiveTab() {
  const id = useConnId()
  const [auto, setAuto] = useState(true)
  const q = useQuery({ queryKey: ['active', id], queryFn: () => api.activeQueries(id), refetchInterval: auto ? 5000 : false })
  const cols: Column<ActiveQuery>[] = [
    { key: 'sid', header: 'Sesión', render: (r) => <span className="font-mono text-xs">{r.session_id}</span>, sort: (r) => r.session_id },
    { key: 'user', header: 'Usuario', render: (r) => r.username ?? '—', sort: (r) => r.username },
    { key: 'app', header: 'Aplicación', render: (r) => r.application || '—', sort: (r) => r.application },
    { key: 'state', header: 'Estado', render: (r) => r.state ?? '—', sort: (r) => r.state },
    {
      key: 'dur', header: 'Duración', align: 'right', sort: (r) => r.duration_seconds,
      render: (r) => {
        const d = r.duration_seconds ?? 0
        return <span data-testid="active-duration" className={cn('rounded px-1.5 py-0.5 font-medium', d >= 60 ? 'bg-red-500/15 text-red-600 dark:text-red-400' : d >= 10 ? 'bg-amber-500/15 text-amber-700 dark:text-amber-400' : '')}>{fmtSeconds(r.duration_seconds)}</span>
      },
    },
    { key: 'wait', header: 'Espera', render: (r) => r.wait_event ?? '—', sort: (r) => r.wait_event },
    {
      key: 'blk', header: 'Bloqueada por', sort: (r) => r.blocked_by.length,
      render: (r) => r.blocked_by.length ? (
        <span className="flex flex-wrap gap-1">{r.blocked_by.map((b) => <Badge key={b} data-testid="blocked-by-badge" className="border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"><Lock size={10} /> {b}</Badge>)}</span>
      ) : '—',
    },
    { key: 'q', header: 'Consulta', render: (r) => sql(r.query) },
  ]
  return (
    <Card>
      <div className="flex items-center justify-end gap-3 border-b border-line px-4 py-2">
        <label className="flex items-center gap-2 text-sm"><input data-testid="active-auto-refresh" type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Actualizar cada 5 s</label>
        <Button onClick={() => void q.refetch()} loading={q.isFetching && !q.isLoading} data-testid="active-refresh-btn"><RefreshCw size={14} /> Actualizar</Button>
      </div>
      <QueryState q={q}>
        <DataTable columns={cols} rows={q.data ?? []} rowKey={(r, i) => r.session_id + i} rowTestId="active-query-row"
          filterText={(r) => [r.session_id, r.username, r.application, r.state, r.query].join(' ')}
          initialSort={{ key: 'dur', dir: 'desc' }}
          empty={<EmptyState title="Sin consultas activas" description="No hay consultas en ejecución en este momento." />} />
      </QueryState>
    </Card>
  )
}

// ------------------------------------------------------------- Top queries
function TopTab() {
  const id = useConnId()
  const [order, setOrder] = useState<TopOrder>('total_time')
  const [explain, setExplain] = useState<TopQuery | 'free' | null>(null)
  const q = useQuery({ queryKey: ['top', id, order], queryFn: () => api.topQueries(id, order, 50) })
  const cols: Column<TopQuery>[] = [
    { key: 'query', header: 'Consulta', render: (r) => sql(r.query), sort: (r) => r.query },
    { key: 'calls', header: 'Llamadas', align: 'right', render: (r) => fmtNumber(r.calls), sort: (r) => r.calls },
    { key: 'total', header: 'Tiempo total', align: 'right', render: (r) => fmtMs(r.total_time_ms), sort: (r) => r.total_time_ms },
    { key: 'mean', header: 'Medio', align: 'right', render: (r) => fmtMs(r.mean_time_ms), sort: (r) => r.mean_time_ms },
    { key: 'max', header: 'Máx', align: 'right', render: (r) => fmtMs(r.max_time_ms), sort: (r) => r.max_time_ms },
    { key: 'rows', header: 'Filas', align: 'right', render: (r) => fmtNumber(r.rows), sort: (r) => r.rows },
    { key: 'disk', header: 'Lecturas disco', align: 'right', render: (r) => fmtNumber(r.disk_reads), sort: (r) => r.disk_reads },
    { key: 'hit', header: 'Cache hit %', align: 'right', render: (r) => fmtPct(r.cache_hit_ratio), sort: (r) => r.cache_hit_ratio },
    { key: 'pct', header: '% del total', align: 'right', render: (r) => fmtPct(r.pct_total_time), sort: (r) => r.pct_total_time },
  ]
  return (
    <Card>
      <QueryState q={q}>
        <DataTable columns={cols} rows={q.data ?? []} rowKey={(r, i) => r.query_id + i} rowTestId="top-query-row"
          onRowClick={(r) => setExplain(r)}
          filterText={(r) => r.query + ' ' + (r.username ?? '')}
          toolbar={
            <>
              <label className="flex items-center gap-2 text-sm">Ordenar por
                <select data-testid="top-order" className={cn(inputCls, 'w-auto!')} value={order} onChange={(e) => setOrder(e.target.value as TopOrder)}>
                  <option value="total_time">Tiempo total</option><option value="mean_time">Tiempo medio</option>
                  <option value="calls">Llamadas</option><option value="io">I/O (lecturas)</option>
                </select>
              </label>
              <Button data-testid="analyze-sql-btn" onClick={() => setExplain('free')}><Microscope size={14} /> Analizar SQL</Button>
            </>
          }
          empty={<EmptyState title="Sin datos de consultas" description="No hay estadísticas de consultas disponibles. En PostgreSQL requiere la extensión pg_stat_statements (mira Optimización)." />} />
      </QueryState>
      {explain && <ExplainDrawer query={explain === 'free' ? null : explain} onClose={() => setExplain(null)} />}
    </Card>
  )
}

// ------------------------------------------------------------- Indexes
function IndexesTab() {
  const id = useConnId()
  const [filter, setFilter] = useState<'all' | 'unused' | 'duplicate'>('all')
  const q = useQuery({ queryKey: ['indexes', id], queryFn: () => api.indexes(id) })
  const rows = (q.data ?? []).filter((r) => filter === 'all' || (filter === 'unused' ? r.unused : !!r.duplicate_of))
  const cols: Column<IndexStat>[] = [
    { key: 'schema', header: 'Schema', render: (r) => r.schema_name, sort: (r) => r.schema_name },
    { key: 'table', header: 'Tabla', render: (r) => r.table, sort: (r) => r.table },
    { key: 'index', header: 'Índice', render: (r) => <span className="font-mono text-xs">{r.index}</span>, sort: (r) => r.index },
    { key: 'cols', header: 'Columnas', render: (r) => <span className="font-mono text-xs">{r.columns.join(', ')}</span> },
    { key: 'size', header: 'Tamaño', align: 'right', render: (r) => fmtBytes(r.size_bytes), sort: (r) => r.size_bytes },
    { key: 'scans', header: 'Scans', align: 'right', render: (r) => fmtNumber(r.scans), sort: (r) => r.scans },
    {
      key: 'flags', header: 'Flags', render: (r) => (
        <span className="flex flex-wrap gap-1">
          {r.is_primary && <Badge>PK</Badge>}
          {r.is_unique && !r.is_primary && <Badge>único</Badge>}
          {r.unused && <Badge className="border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400">sin uso</Badge>}
          {r.duplicate_of && <Badge className="border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400" title={r.duplicate_of}>duplicado de {r.duplicate_of}</Badge>}
        </span>
      ),
    },
  ]
  const filters = [['all', 'Todos'], ['unused', 'Sin uso'], ['duplicate', 'Duplicados']] as const
  return (
    <Card>
      <QueryState q={q}>
        <DataTable columns={cols} rows={rows} rowKey={(r) => `${r.schema_name}.${r.index}`} rowTestId="index-row"
          filterText={(r) => `${r.schema_name} ${r.table} ${r.index} ${r.columns.join(' ')}`}
          toolbar={
            <div className="flex gap-1">
              {filters.map(([k, l]) => (
                <button key={k} type="button" data-testid={`index-filter-${k}`} aria-pressed={filter === k} onClick={() => setFilter(k)}
                  className={cn('rounded-md border px-2.5 py-1 text-sm', filter === k ? 'border-brand bg-brand text-brand-fg' : 'border-line bg-surface hover:bg-surface2')}>{l}</button>
              ))}
            </div>
          }
          empty={<EmptyState title="Sin índices" description="No hay índices que coincidan con el filtro." />} />
      </QueryState>
    </Card>
  )
}

// ------------------------------------------------------------- Tables
function TablesTab() {
  const id = useConnId()
  const q = useQuery({ queryKey: ['tables', id], queryFn: () => api.tables(id) })
  const cols: Column<TableStat>[] = [
    { key: 'schema', header: 'Schema', render: (r) => r.schema_name, sort: (r) => r.schema_name },
    { key: 'table', header: 'Tabla', render: (r) => <span className="font-mono text-xs">{r.table}</span>, sort: (r) => r.table },
    { key: 'rows', header: 'Filas', align: 'right', render: (r) => fmtNumber(r.rows_estimate), sort: (r) => r.rows_estimate },
    { key: 'size', header: 'Tamaño', align: 'right', render: (r) => fmtBytes(r.size_bytes), sort: (r) => r.size_bytes },
    { key: 'total', header: 'Tamaño total', align: 'right', render: (r) => fmtBytes(r.total_size_bytes), sort: (r) => r.total_size_bytes },
    { key: 'seq', header: 'Seq scans', align: 'right', render: (r) => fmtNumber(r.seq_scans), sort: (r) => r.seq_scans },
    { key: 'idx', header: 'Idx scans', align: 'right', render: (r) => fmtNumber(r.index_scans), sort: (r) => r.index_scans },
    { key: 'dead', header: 'Filas muertas', align: 'right', render: (r) => fmtNumber(r.dead_rows), sort: (r) => r.dead_rows },
    { key: 'mods', header: 'Mods. desde analyze', align: 'right', render: (r) => fmtNumber(r.modifications_since_analyze), sort: (r) => r.modifications_since_analyze },
    { key: 'an', header: 'Último analyze', render: (r) => <span className="whitespace-nowrap text-xs">{fmtDate(r.last_analyzed)}</span>, sort: (r) => r.last_analyzed },
    { key: 'vac', header: 'Último vacuum', render: (r) => <span className="whitespace-nowrap text-xs">{fmtDate(r.last_vacuum)}</span>, sort: (r) => r.last_vacuum },
  ]
  return (
    <Card>
      <QueryState q={q}>
        <DataTable columns={cols} rows={q.data ?? []} rowKey={(r) => `${r.schema_name}.${r.table}`} rowTestId="table-row"
          filterText={(r) => `${r.schema_name} ${r.table}`} initialSort={{ key: 'total', dir: 'desc' }}
          empty={<EmptyState title="Sin tablas" />} />
      </QueryState>
    </Card>
  )
}

// ------------------------------------------------------------- Locks
function LocksTab() {
  const id = useConnId()
  const q = useQuery({ queryKey: ['locks', id], queryFn: () => api.locks(id), refetchInterval: 5000 })
  const rows: LockInfo[] = q.data ?? []
  return (
    <Card>
      <QueryState q={q}>
        {rows.length === 0 ? (
          <EmptyState icon={<Lock size={36} />} title="Sin bloqueos" description="Ninguna sesión está esperando a otra en este momento." />
        ) : (
          <div className="divide-y divide-line">
            {rows.map((l, i) => (
              <div key={i} data-testid="lock-row" className="grid gap-4 p-4 md:grid-cols-2">
                <div>
                  <div className="mb-1 text-xs font-medium text-red-500">Bloqueada · sesión {l.blocked_session}{l.blocked_user ? ` (${l.blocked_user})` : ''}</div>
                  <Code className="max-h-32">{l.blocked_query || '—'}</Code>
                </div>
                <div>
                  <div className="mb-1 text-xs font-medium text-amber-500">Bloqueador · sesión {l.blocking_session}{l.blocking_user ? ` (${l.blocking_user})` : ''}</div>
                  <Code className="max-h-32">{l.blocking_query || '—'}</Code>
                </div>
                <div className="text-xs text-muted md:col-span-2">
                  {l.lock_type && <>Tipo: {l.lock_type} · </>}{l.object_name && <>Objeto: {l.object_name} · </>}Espera: {fmtSeconds(l.wait_seconds)}
                </div>
              </div>
            ))}
          </div>
        )}
      </QueryState>
    </Card>
  )
}

// ------------------------------------------------------------- Config
function ConfigTab() {
  const id = useConnId()
  const q = useQuery({ queryKey: ['config', id], queryFn: () => api.config(id) })
  const cols: Column<ConfigParam>[] = [
    { key: 'name', header: 'Parámetro', render: (r) => <span className="font-mono text-xs">{r.name}</span>, sort: (r) => r.name },
    { key: 'value', header: 'Valor', render: (r) => <span className="font-mono text-xs">{r.value}{r.unit ? ` ${r.unit}` : ''}</span> },
    {
      key: 'rec', header: 'Recomendado', sort: (r) => r.recommended,
      render: (r) => r.recommended ? <Badge className="border-brand/40 text-brand">{r.recommended}</Badge> : '—',
    },
    { key: 'cat', header: 'Categoría', render: (r) => r.category ?? '—', sort: (r) => r.category },
    { key: 'desc', header: 'Descripción', render: (r) => <span className="text-muted">{r.description ?? '—'}</span>, className: 'max-w-md' },
  ]
  return (
    <Card>
      <QueryState q={q}>
        <DataTable columns={cols} rows={q.data ?? []} rowKey={(r) => r.name} rowTestId="config-row"
          filterText={(r) => `${r.name} ${r.value} ${r.description ?? ''} ${r.category ?? ''}`} initialSort={{ key: 'name', dir: 'asc' }}
          empty={<EmptyState title="Sin parámetros" />} />
      </QueryState>
    </Card>
  )
}

export default function TechnicalPage() {
  const [tab, setTab] = useState<Tab>('active')
  return (
    <div>
      <PageHeader title="Técnico" subtitle="Diagnóstico en vivo: consultas, planes, índices, tablas, bloqueos y configuración." />
      <div className="mb-4 flex flex-wrap gap-1 border-b border-line" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" type="button" aria-selected={tab === t.id} data-testid={`tab-${t.id}`} onClick={() => setTab(t.id)}
            className={cn('-mb-px border-b-2 px-4 py-2 text-sm font-medium', tab === t.id ? 'border-brand text-brand' : 'border-transparent text-muted hover:text-fg')}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'active' && <ActiveTab />}
      {tab === 'top' && <TopTab />}
      {tab === 'indexes' && <IndexesTab />}
      {tab === 'tables' && <TablesTab />}
      {tab === 'locks' && <LocksTab />}
      {tab === 'config' && <ConfigTab />}
    </div>
  )
}
