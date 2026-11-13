import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Database, Pencil, Plug, Plus, Trash2, XCircle } from 'lucide-react'
import { api } from '../api/client'
import type { ConnectionIn, ConnectionOut, Engine, TestResult } from '../api/types'
import { useApp } from '../lib/app'
import { fmtNumber } from '../lib/format'
import {
  Badge, Button, Card, ConfirmDialog, EmptyState, ErrorBanner, Field, Modal, PageHeader, Spinner, inputCls,
} from '../components/ui'
import { ENGINE_LABEL } from '../App'

const DEFAULT_PORT: Record<Engine, number> = { postgres: 5432, oracle: 1521 }

function ConnectionForm({ initial, onClose }: { initial: ConnectionOut | null; onClose: () => void }) {
  const qc = useQueryClient()
  const { toast, select } = useApp()
  const [engine, setEngine] = useState<Engine>(initial?.engine ?? 'postgres')
  const [f, setF] = useState({
    name: initial?.name ?? '',
    host: initial?.host ?? 'localhost',
    port: String(initial?.port ?? 5432),
    database: initial?.database ?? '',
    username: initial?.username ?? '',
    password: '',
    ssl: initial?.ssl ?? false,
  })
  const [result, setResult] = useState<TestResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((p) => ({ ...p, [k]: v }))

  const body = (): ConnectionIn => ({
    name: f.name.trim(), engine, host: f.host.trim(), port: Number(f.port), database: f.database.trim(),
    username: f.username.trim(), password: f.password, ssl: f.ssl, options: initial?.options ?? {},
  })

  const pickEngine = (e: Engine) => {
    // only swap the port if the user hasn't customised it
    if (Number(f.port) === DEFAULT_PORT[engine]) set('port', String(DEFAULT_PORT[e]))
    setEngine(e)
    setResult(null)
  }

  const test = useMutation({
    mutationFn: () => api.testConnectionConfig(body()),
    onSuccess: (r) => { setResult(r); setError(null) },
    onError: (e) => { setResult(null); setError(e) },
  })
  const save = useMutation({
    mutationFn: () => (initial ? api.updateConnection(initial.id, body()) : api.createConnection(body())),
    onSuccess: async (c) => {
      await qc.invalidateQueries({ queryKey: ['connections'] })
      if (!initial) select(c.id)
      toast(initial ? 'Conexión actualizada' : 'Conexión guardada')
      onClose()
    },
    onError: (e) => setError(e),
  })

  const valid = f.name.trim() && f.host.trim() && f.database.trim() && f.username.trim() && Number(f.port) >= 1 && Number(f.port) <= 65535
  const dbLabel = engine === 'postgres' ? 'Base de datos' : 'Service name'

  return (
    <Modal title={initial ? 'Editar conexión' : 'Nueva conexión'} onClose={onClose} wide testId="connection-form">
      <form
        className="space-y-4"
        onSubmit={(e) => { e.preventDefault(); if (valid) save.mutate() }}
      >
        <div>
          <span className="mb-1 block text-xs font-medium text-muted">Motor</span>
          <div className="inline-flex overflow-hidden rounded-md border border-line">
            {(['postgres', 'oracle'] as Engine[]).map((e) => (
              <button
                key={e} type="button" data-testid={`conn-engine-${e}`} aria-pressed={engine === e}
                onClick={() => pickEngine(e)}
                className={`px-4 py-1.5 text-sm font-medium ${engine === e ? 'bg-brand text-brand-fg' : 'bg-surface hover:bg-surface2'}`}
              >
                {ENGINE_LABEL[e]}
              </button>
            ))}
          </div>
        </div>
        <Field label="Nombre">
          <input data-testid="conn-name" className={inputCls} value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="Producción" />
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-2">
            <Field label="Host">
              <input data-testid="conn-host" className={inputCls} value={f.host} onChange={(e) => set('host', e.target.value)} />
            </Field>
          </div>
          <Field label="Puerto">
            <input data-testid="conn-port" type="number" min={1} max={65535} className={inputCls} value={f.port} onChange={(e) => set('port', e.target.value)} />
          </Field>
        </div>
        <Field label={dbLabel}>
          <input data-testid="conn-database" className={inputCls} value={f.database} onChange={(e) => set('database', e.target.value)} placeholder={engine === 'postgres' ? 'postgres' : 'FREEPDB1'} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Usuario">
            <input data-testid="conn-username" autoComplete="off" className={inputCls} value={f.username} onChange={(e) => set('username', e.target.value)} />
          </Field>
          <Field label="Contraseña">
            <input
              data-testid="conn-password" type="password" autoComplete="new-password" className={inputCls}
              value={f.password} onChange={(e) => set('password', e.target.value)}
              placeholder={initial ? 'sin cambios' : ''}
            />
          </Field>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input data-testid="conn-ssl" type="checkbox" checked={f.ssl} onChange={(e) => set('ssl', e.target.checked)} /> Usar SSL
        </label>

        {result && (
          <div
            data-testid="conn-test-result"
            className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${result.ok ? 'border-emerald-500/40 bg-emerald-500/10' : 'border-red-500/40 bg-red-500/10'}`}
          >
            {result.ok ? <CheckCircle2 size={18} className="mt-0.5 text-emerald-500" /> : <XCircle size={18} className="mt-0.5 text-red-500" />}
            <div className="min-w-0">
              <div className="font-medium">{result.ok ? 'Conexión correcta' : 'Falló la conexión'}</div>
              {!result.ok && <div className="break-words">{result.message}</div>}
              {result.version && <div className="text-muted">Versión: {result.version}</div>}
              {result.latency_ms != null && <div className="text-muted">Latencia: {fmtNumber(result.latency_ms, 1)} ms</div>}
            </div>
          </div>
        )}
        {!!error && <ErrorBanner error={error} />}

        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancelar</Button>
          <Button data-testid="conn-test-btn" disabled={!valid} loading={test.isPending} onClick={() => test.mutate()}>
            <Plug size={14} /> Probar conexión
          </Button>
          <Button data-testid="conn-save-btn" variant="primary" disabled={!valid} loading={save.isPending} onClick={() => save.mutate()}>
            Guardar
          </Button>
        </div>
      </form>
    </Modal>
  )
}

export default function ConnectionsPage() {
  const { connections, isLoading, error, refetch, selectedId, select, toast } = useApp()
  const qc = useQueryClient()
  const [editing, setEditing] = useState<ConnectionOut | 'new' | null>(null)
  const [deleting, setDeleting] = useState<ConnectionOut | null>(null)
  const [tests, setTests] = useState<Record<number, TestResult | 'loading' | string>>({})

  const del = useMutation({
    mutationFn: (id: number) => api.deleteConnection(id),
    onSuccess: async () => {
      toast('Conexión eliminada')
      setDeleting(null)
      await qc.invalidateQueries({ queryKey: ['connections'] })
    },
    onError: (e) => { toast((e as Error).message, 'error'); setDeleting(null) },
  })

  const runTest = async (c: ConnectionOut) => {
    setTests((t) => ({ ...t, [c.id]: 'loading' }))
    try {
      const r = await api.testConnection(c.id)
      setTests((t) => ({ ...t, [c.id]: r }))
    } catch (e) {
      setTests((t) => ({ ...t, [c.id]: (e as Error).message }))
    }
  }

  return (
    <div>
      <PageHeader
        title="Conexiones"
        subtitle="Bases de datos que Tyrux puede diagnosticar. Las contraseñas se guardan cifradas."
        actions={
          <Button variant="primary" data-testid="add-connection-btn" onClick={() => setEditing('new')}>
            <Plus size={14} /> Agregar conexión
          </Button>
        }
      />
      {isLoading ? <Spinner /> : error ? <ErrorBanner error={error} onRetry={refetch} /> : connections.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Database size={40} />}
            title="Aún no hay conexiones"
            description="Agrega tu primera base de datos PostgreSQL u Oracle para empezar a diagnosticar consultas lentas."
            action={<Button variant="primary" onClick={() => setEditing('new')} data-testid="empty-add-btn"><Plus size={14} /> Agregar la primera conexión</Button>}
          />
        </Card>
      ) : (
        <div className="grid gap-3">
          {connections.map((c) => {
            const t = tests[c.id]
            return (
              <Card key={c.id} data-testid="connection-row" className="p-4">
                <div className="flex flex-wrap items-center gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-base font-semibold" data-testid="connection-name">{c.name}</span>
                      <Badge>{ENGINE_LABEL[c.engine]}</Badge>
                      {c.ssl && <Badge>SSL</Badge>}
                      {c.monitoring && <Badge className="text-emerald-600 dark:text-emerald-400">Monitoreando</Badge>}
                      {c.id === selectedId && <Badge className="text-brand">Seleccionada</Badge>}
                    </div>
                    <div className="mt-1 font-mono text-xs text-muted">
                      {c.host}:{c.port}/{c.database} · {c.username}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {c.id !== selectedId && <Button data-testid="conn-select-btn" onClick={() => select(c.id)}>Usar</Button>}
                    <Button data-testid="conn-edit-btn" onClick={() => setEditing(c)}><Pencil size={14} /> Editar</Button>
                    <Button data-testid="conn-row-test-btn" loading={t === 'loading'} onClick={() => runTest(c)}><Plug size={14} /> Probar</Button>
                    <Button data-testid="conn-delete-btn" onClick={() => setDeleting(c)}><Trash2 size={14} /> Eliminar</Button>
                  </div>
                </div>
                {t && t !== 'loading' && (
                  <div data-testid="conn-row-test-result" className={`mt-3 flex items-center gap-2 rounded-md px-3 py-2 text-sm ${typeof t === 'string' || !t.ok ? 'bg-red-500/10' : 'bg-emerald-500/10'}`}>
                    {typeof t === 'string' ? (<><XCircle size={16} className="text-red-500" /> {t}</>) : t.ok ? (
                      <><CheckCircle2 size={16} className="text-emerald-500" /> {t.message}{t.version ? ` · ${t.version}` : ''}{t.latency_ms != null ? ` · ${fmtNumber(t.latency_ms, 1)} ms` : ''}</>
                    ) : (<><XCircle size={16} className="text-red-500" /> {t.message}</>)}
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}
      {editing && <ConnectionForm initial={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
      {deleting && (
        <ConfirmDialog
          title="Eliminar conexión"
          message={<>¿Eliminar la conexión <b>{deleting.name}</b>? Se borrará también su historial de monitoreo.</>}
          loading={del.isPending}
          onCancel={() => setDeleting(null)}
          onConfirm={() => del.mutate(deleting.id)}
        />
      )}
    </div>
  )
}
