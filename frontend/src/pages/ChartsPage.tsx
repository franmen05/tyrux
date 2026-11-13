import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Play, Square, Activity, AlertTriangle, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import type { MetricPoint, MonitorStatus } from '../api/types'
import { useConnId, useApp } from '../lib/app'
import { fmtBytes, fmtDate, fmtMs, fmtNumber, fmtPct, fmtSeconds, fmtTime, parseTs, truncate } from '../lib/format'
import { Button, Card, EmptyState, ErrorBanner, PageHeader, Skeleton, cn, inputCls } from '../components/ui'

const PRESETS = [
  { s: 1, label: '1 s' }, { s: 5, label: '5 s' }, { s: 10, label: '10 s' },
  { s: 30, label: '30 s' }, { s: 60, label: '1 min' }, { s: 300, label: '5 min' },
]
const WINDOWS = [
  { m: 5, label: '5 min' }, { m: 15, label: '15 min' }, { m: 60, label: '1 h' }, { m: 360, label: '6 h' }, { m: 1440, label: '24 h' },
]
const COLORS = { a: '#3b82f6', b: '#f59e0b', c: '#ef4444', d: '#10b981', e: '#8b5cf6' }

function Kpi({ id, label, value, sub, loading }: { id: string; label: string; value: ReactNode; sub?: ReactNode; loading?: boolean }) {
  return (
    <Card data-testid={`kpi-${id}`} className="p-4">
      <div className="text-xs font-medium text-muted">{label}</div>
      {loading ? <Skeleton className="mt-2 h-7 w-24" /> : <div className="mt-1 text-2xl font-semibold tabular-nums" data-testid={`kpi-${id}-value`}>{value}</div>}
      {sub && !loading && <div className="mt-0.5 truncate text-xs text-muted">{sub}</div>}
    </Card>
  )
}

function ChartCard({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <Card data-testid={id} className="p-4">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      <div className="h-56">{children}</div>
    </Card>
  )
}

const axisProps = { stroke: 'var(--c-muted)', fontSize: 11, tickLine: false } as const
const tooltipStyle = { background: 'var(--c-surface)', border: '1px solid var(--c-line)', borderRadius: 8, fontSize: 12, color: 'var(--c-fg)' }

type Row = MetricPoint & { t: number }

function TimeChartBase({
  data, yFormat, children, kind = 'line', unit,
}: { data: Row[]; yFormat?: (v: number) => string; children: ReactNode; kind?: 'line' | 'area'; unit?: string }) {
  const common = (
    <>
      <CartesianGrid stroke="var(--c-line)" strokeDasharray="3 3" />
      <XAxis dataKey="t" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(v: number) => fmtTime(v)} {...axisProps} minTickGap={40} />
      <YAxis {...axisProps} width={48} tickFormatter={yFormat} />
      <Tooltip contentStyle={tooltipStyle} labelFormatter={(v: number) => fmtTime(v)} formatter={(v: number) => (yFormat ? yFormat(v) : fmtNumber(v, 2)) + (unit ?? '')} />
      <Legend wrapperStyle={{ fontSize: 12 }} />
    </>
  )
  return (
    <ResponsiveContainer width="100%" height="100%">
      {kind === 'area' ? (
        <AreaChart data={data}>{common}{children}</AreaChart>
      ) : (
        <LineChart data={data}>{common}{children}</LineChart>
      )}
    </ResponsiveContainer>
  )
}

function MonitorBar({ id, status, onChange }: { id: number; status: MonitorStatus | undefined; onChange: () => void }) {
  const { toast } = useApp()
  const [interval, setIntervalS] = useState(5)
  const [custom, setCustom] = useState('')
  const [duration, setDuration] = useState('')
  const [err, setErr] = useState<unknown>(null)

  const effective = custom !== '' ? Number(custom) : interval
  const validInterval = effective >= 1 && effective <= 3600
  const validDuration = duration === '' || Number(duration) > 0

  const start = useMutation({
    mutationFn: () => api.monitorStart(id, { interval_seconds: effective, duration_minutes: duration === '' ? null : Number(duration) }),
    onSuccess: () => { setErr(null); toast('Monitoreo iniciado'); onChange() },
    onError: setErr,
  })
  const stop = useMutation({
    mutationFn: () => api.monitorStop(id),
    onSuccess: () => { setErr(null); toast('Monitoreo detenido'); onChange() },
    onError: setErr,
  })
  const running = !!status?.running

  return (
    <Card className="mb-4 p-4" data-testid="monitor-bar">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div>
          <div className="mb-1 text-xs font-medium text-muted">Intervalo de muestreo</div>
          <div className="flex flex-wrap items-center gap-1.5">
            {PRESETS.map((p) => (
              <button
                key={p.s} type="button" data-testid={`monitor-interval-${p.s}`} disabled={running}
                aria-pressed={custom === '' && interval === p.s}
                onClick={() => { setIntervalS(p.s); setCustom('') }}
                className={cn('rounded-md border px-2.5 py-1 text-sm disabled:opacity-60', custom === '' && interval === p.s ? 'border-brand bg-brand text-brand-fg' : 'border-line bg-surface hover:bg-surface2')}
              >{p.label}</button>
            ))}
            <input
              data-testid="monitor-interval-custom" type="number" min={1} max={3600} placeholder="otro (s)" disabled={running}
              className={cn(inputCls, 'w-24!')} value={custom} onChange={(e) => setCustom(e.target.value)}
            />
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-muted">Duración (min, opcional)</div>
          <input
            data-testid="monitor-duration" type="number" min={0} step="any" placeholder="sin límite" disabled={running}
            className={cn(inputCls, 'w-32!')} value={duration} onChange={(e) => setDuration(e.target.value)}
          />
        </div>
        <div className="flex gap-2">
          <Button data-testid="monitor-start-btn" variant="primary" disabled={running || !validInterval || !validDuration} loading={start.isPending} onClick={() => start.mutate()}>
            <Play size={14} /> Iniciar
          </Button>
          <Button data-testid="monitor-stop-btn" variant="danger" disabled={!running} loading={stop.isPending} onClick={() => stop.mutate()}>
            <Square size={14} /> Detener
          </Button>
        </div>
        <div className="ml-auto text-sm" data-testid="monitor-status" data-running={running ? 'true' : 'false'}>
          <div className="flex items-center gap-2">
            <span className={cn('h-2 w-2 rounded-full', running ? 'animate-pulse bg-emerald-500' : 'bg-slate-400')} />
            <span className="font-medium">{running ? `Monitoreando cada ${fmtSeconds(status?.interval_seconds ?? effective)}` : 'Detenido'}</span>
          </div>
          <div className="text-xs text-muted">
            {fmtNumber(status?.samples ?? 0)} muestras · última: {status?.last_sample_at ? fmtDate(status.last_sample_at) : '—'}
            {running && status?.ends_at && <> · termina {fmtDate(status.ends_at)}</>}
          </div>
        </div>
      </div>
      {!validInterval && custom !== '' && <div className="mt-2 text-xs text-red-500">El intervalo debe estar entre 1 y 3600 segundos.</div>}
      {status?.last_error && (
        <div data-testid="monitor-last-error" className="mt-3 flex items-start gap-2 rounded-md bg-amber-500/10 px-3 py-2 text-sm">
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" /> Último error del monitor: {status.last_error}
        </div>
      )}
      {!!err && <div className="mt-3"><ErrorBanner error={err} /></div>}
    </Card>
  )
}

export default function ChartsPage() {
  const id = useConnId()
  const qc = useQueryClient()
  const { toast } = useApp()
  const [minutes, setMinutes] = useState(15)

  const overview = useQuery({ queryKey: ['overview', id], queryFn: () => api.overview(id), refetchInterval: 10000 })
  const status = useQuery({
    queryKey: ['monitor', id], queryFn: () => api.monitorStatus(id),
    refetchInterval: (q) => (q.state.data?.running ? Math.max(q.state.data.interval_seconds ?? 2, 2) * 1000 : 5000),
  })
  const running = !!status.data?.running
  const pollMs = Math.max(status.data?.interval_seconds ?? 2, 2) * 1000

  const metrics = useQuery({
    queryKey: ['metrics', id, minutes], queryFn: () => api.metrics(id, minutes),
    refetchInterval: running ? pollMs : false,
  })
  const topQ = useQuery({
    queryKey: ['metrics-top', id, minutes], queryFn: () => api.metricsTopQueries(id, minutes),
    refetchInterval: running ? Math.max(pollMs, 5000) : false,
  })

  const refreshAll = () => {
    void qc.invalidateQueries({ queryKey: ['monitor', id] })
    void qc.invalidateQueries({ queryKey: ['metrics', id] })
    void qc.invalidateQueries({ queryKey: ['metrics-top', id] })
    void qc.invalidateQueries({ queryKey: ['connections'] })
  }
  const clear = useMutation({
    mutationFn: () => api.clearMetrics(id),
    onSuccess: () => { toast('Historial borrado'); refreshAll() },
    onError: (e) => toast((e as Error).message, 'error'),
  })

  const data: Row[] = useMemo(
    () => (metrics.data?.points ?? []).map((p) => ({ ...p, t: parseTs(p.ts).getTime() })),
    [metrics.data],
  )
  const topBars = useMemo(
    () => [...(topQ.data ?? [])].sort((a, b) => b.total_time_ms - a.total_time_ms).slice(0, 10)
      .map((q) => ({ ...q, label: truncate(q.query, 40) })),
    [topQ.data],
  )
  const o = overview.data
  const noPoints = !metrics.isLoading && !metrics.error && data.length === 0

  const startCta = () => document.querySelector<HTMLElement>('[data-testid="monitor-start-btn"]')?.focus()

  return (
    <div>
      <PageHeader title="Gráficos" subtitle="Estado actual y evolución en el tiempo de la base de datos seleccionada." />

      {o && o.warnings.length > 0 && (
        <div data-testid="overview-warnings" className="mb-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <div className="mb-1 flex items-center gap-2 font-medium"><AlertTriangle size={16} className="text-amber-500" /> Advertencias</div>
          <ul className="list-disc space-y-0.5 pl-6">{o.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          <Link to="/optimize" className="mt-1 inline-block text-brand hover:underline">Ver recomendaciones</Link>
        </div>
      )}
      {overview.error && <div className="mb-4"><ErrorBanner error={overview.error} onRetry={() => void overview.refetch()} /></div>}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <Kpi id="sessions" label="Sesiones activas / total" loading={overview.isLoading}
          value={o ? `${fmtNumber(o.sessions_active)} / ${fmtNumber(o.sessions_total)}` : '—'}
          sub={o ? `${fmtNumber(o.sessions_blocked)} bloqueadas` : undefined} />
        <Kpi id="cache" label="Cache hit" loading={overview.isLoading} value={fmtPct(o?.cache_hit_ratio)} />
        <Kpi id="size" label="Tamaño de la BD" loading={overview.isLoading} value={fmtBytes(o?.size_bytes)} />
        <Kpi id="longest" label="Consulta más larga" loading={overview.isLoading} value={fmtSeconds(o?.longest_query_seconds)} />
        <Kpi id="maxconn" label="Máx. conexiones" loading={overview.isLoading} value={fmtNumber(o?.max_connections)} />
        <Kpi id="version" label="Versión" loading={overview.isLoading}
          value={<span className="text-base" title={o?.version}>{o ? truncate(o.version, 28) : '—'}</span>}
          sub={o?.uptime_seconds != null ? `activa hace ${fmtSeconds(o.uptime_seconds)}` : undefined} />
      </div>

      <MonitorBar id={id} status={status.data} onChange={refreshAll} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted">Ventana de tiempo</span>
        {WINDOWS.map((w) => (
          <button
            key={w.m} type="button" data-testid={`window-${w.m}`} aria-pressed={minutes === w.m}
            onClick={() => setMinutes(w.m)}
            className={cn('rounded-md border px-2.5 py-1 text-sm', minutes === w.m ? 'border-brand bg-brand text-brand-fg' : 'border-line bg-surface hover:bg-surface2')}
          >{w.label}</button>
        ))}
        <Button variant="ghost" className="ml-auto" data-testid="clear-metrics-btn" loading={clear.isPending}
          onClick={() => { if (window.confirm('¿Borrar el historial de métricas de esta conexión?')) clear.mutate() }}>
          <Trash2 size={14} /> Borrar historial
        </Button>
      </div>

      {metrics.error ? (
        <ErrorBanner error={metrics.error} onRetry={() => void metrics.refetch()} />
      ) : metrics.isLoading ? (
        <div className="grid gap-4 lg:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-64" />)}</div>
      ) : noPoints ? (
        <Card>
          <EmptyState
            icon={<Activity size={40} />}
            title="Inicia el monitoreo para ver la evolución"
            description="Aún no hay muestras en esta ventana de tiempo. El monitoreo toma datos periódicos de la base de datos y los grafica aquí."
            action={
              running ? <span className="text-muted">Esperando la primera muestra…</span> : (
                <Button variant="primary" data-testid="empty-start-btn" onClick={startCta}><Play size={14} /> Iniciar monitoreo</Button>
              )
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard id="chart-sessions" title="Sesiones">
            <TimeChartBase data={data} kind="area" yFormat={(v) => fmtNumber(v)}>
              <Area type="monotone" dataKey="sessions_active" name="Activas" stackId="s" stroke={COLORS.a} fill={COLORS.a} fillOpacity={0.5} isAnimationActive={false} />
              <Area type="monotone" dataKey="sessions_idle" name="Inactivas" stackId="s" stroke={COLORS.d} fill={COLORS.d} fillOpacity={0.35} isAnimationActive={false} />
              <Area type="monotone" dataKey="sessions_blocked" name="Bloqueadas" stackId="s" stroke={COLORS.c} fill={COLORS.c} fillOpacity={0.6} isAnimationActive={false} />
            </TimeChartBase>
          </ChartCard>
          <ChartCard id="chart-tps" title="Transacciones por segundo">
            <TimeChartBase data={data} yFormat={(v) => fmtNumber(v, 1)}>
              <Line type="monotone" dataKey="commits_per_sec" name="Commits/s" stroke={COLORS.a} dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line type="monotone" dataKey="rollbacks_per_sec" name="Rollbacks/s" stroke={COLORS.c} dot={false} strokeWidth={2} isAnimationActive={false} />
            </TimeChartBase>
          </ChartCard>
          <ChartCard id="chart-reads" title="Lecturas por segundo">
            <TimeChartBase data={data} yFormat={(v) => fmtNumber(v, 0)}>
              <Line type="monotone" dataKey="logical_reads_per_sec" name="Lógicas" stroke={COLORS.a} dot={false} strokeWidth={2} isAnimationActive={false} />
              <Line type="monotone" dataKey="physical_reads_per_sec" name="Físicas" stroke={COLORS.b} dot={false} strokeWidth={2} isAnimationActive={false} />
            </TimeChartBase>
          </ChartCard>
          <ChartCard id="chart-cache" title="Cache hit (%)">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data}>
                <CartesianGrid stroke="var(--c-line)" strokeDasharray="3 3" />
                <XAxis dataKey="t" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(v: number) => fmtTime(v)} {...axisProps} minTickGap={40} />
                <YAxis {...axisProps} width={48} domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} />
                <Tooltip contentStyle={tooltipStyle} labelFormatter={(v: number) => fmtTime(v)} formatter={(v: number) => fmtPct(v, 2)} />
                <Line type="monotone" dataKey="cache_hit_ratio" name="Cache hit" stroke={COLORS.d} dot={false} strokeWidth={2} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </ChartCard>
          <div className="lg:col-span-2">
            <ChartCard id="chart-longest" title="Consulta más larga (s)">
              <TimeChartBase data={data} yFormat={(v) => fmtNumber(v, 1)} unit=" s">
                <Line type="monotone" dataKey="longest_query_seconds" name="Más larga (s)" stroke={COLORS.e} dot={false} strokeWidth={2} isAnimationActive={false} />
              </TimeChartBase>
            </ChartCard>
          </div>
        </div>
      )}

      <Card className="mt-4 p-4" data-testid="chart-top-queries">
        <h3 className="mb-3 text-sm font-semibold">Top 10 consultas por tiempo total en la ventana</h3>
        {topQ.error ? <ErrorBanner error={topQ.error} onRetry={() => void topQ.refetch()} /> : topQ.isLoading ? <Skeleton className="h-64" /> : topBars.length === 0 ? (
          <div className="py-8 text-center text-muted" data-testid="top-queries-empty">
            Sin datos de consultas en esta ventana. Se necesita monitoreo activo y pg_stat_statements (PostgreSQL) o V$SQL (Oracle).
          </div>
        ) : (
          <div style={{ height: Math.max(220, topBars.length * 34 + 40) }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={topBars} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid stroke="var(--c-line)" strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" {...axisProps} tickFormatter={(v: number) => fmtMs(v)} />
                <YAxis type="category" dataKey="label" width={320} {...axisProps} fontFamily="ui-monospace, monospace" interval={0} />
                <Tooltip
                  cursor={{ fill: 'var(--c-surface2)' }}
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null
                    const q = payload[0].payload as (typeof topBars)[number]
                    return (
                      <div style={tooltipStyle} className="max-w-lg p-3 shadow-lg">
                        <pre className="mb-2 max-h-40 overflow-hidden whitespace-pre-wrap break-words font-mono text-[11px]">{q.query}</pre>
                        <div>Tiempo total: <b>{fmtMs(q.total_time_ms)}</b> · Llamadas: <b>{fmtNumber(q.calls)}</b> · Medio: <b>{fmtMs(q.mean_time_ms)}</b></div>
                      </div>
                    )
                  }}
                />
                <Bar dataKey="total_time_ms" name="Tiempo total" fill={COLORS.a} radius={[0, 4, 4, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>
    </div>
  )
}
