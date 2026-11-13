import { NavLink, Navigate, Route, Routes, Link } from 'react-router-dom'
import { Activity, Database, LineChart, Wrench, Sparkles, type LucideIcon } from 'lucide-react'
import { useApp } from './lib/app'
import { Badge, Button, EmptyState, ErrorBanner, cn, inputCls } from './components/ui'
import ConnectionsPage from './pages/ConnectionsPage'
import ChartsPage from './pages/ChartsPage'
import TechnicalPage from './pages/TechnicalPage'
import OptimizePage from './pages/OptimizePage'
import type { ReactNode } from 'react'

const NAV: { to: string; label: string; icon: LucideIcon; id: string }[] = [
  { to: '/connections', label: 'Conexiones', icon: Database, id: 'nav-connections' },
  { to: '/charts', label: 'Gráficos', icon: LineChart, id: 'nav-charts' },
  { to: '/technical', label: 'Técnico', icon: Wrench, id: 'nav-technical' },
  { to: '/optimize', label: 'Optimización', icon: Sparkles, id: 'nav-optimize' },
]

export const ENGINE_LABEL = { postgres: 'PostgreSQL', oracle: 'Oracle' } as const

function Sidebar() {
  const { connections, selectedId, selected, select } = useApp()
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2 px-4 py-4">
        <Activity className="shrink-0 text-brand" size={22} />
        <div className="leading-tight">
          <div data-testid="app-title" className="text-base font-semibold">Tyrux</div>
          <div data-testid="app-subtitle" className="text-xs text-muted">DB Monitor</div>
        </div>
      </div>
      <div className="space-y-2 border-y border-line px-4 py-3">
        <div className="text-xs font-medium text-muted">Conexión activa</div>
        <select
          data-testid="connection-select"
          className={inputCls}
          value={selectedId ?? ''}
          onChange={(e) => select(e.target.value ? Number(e.target.value) : null)}
        >
          {connections.length === 0 && <option value="">Sin conexiones</option>}
          {connections.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        {selected && (
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge data-testid="engine-badge" className={selected.engine === 'postgres' ? 'text-sky-600 dark:text-sky-400' : 'text-red-600 dark:text-red-400'}>
              {ENGINE_LABEL[selected.engine]}
            </Badge>
            {selected.monitoring && (
              <Badge data-testid="monitoring-indicator" className="text-emerald-600 dark:text-emerald-400">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" /> Monitoreando
              </Badge>
            )}
          </div>
        )}
      </div>
      <nav className="flex flex-1 flex-col gap-1 p-3">
        {NAV.map(({ to, label, icon: Icon, id }) => (
          <NavLink
            key={to}
            to={to}
            data-testid={id}
            className={({ isActive }) =>
              cn('flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition', isActive ? 'bg-brand/10 text-brand' : 'text-muted hover:bg-surface2 hover:text-fg')
            }
          >
            <Icon size={16} /> {label}
          </NavLink>
        ))}
      </nav>
      <div className="px-4 py-3 text-xs text-muted">Solo lectura: no ejecuta cambios.</div>
    </aside>
  )
}

/** Pages that need a selected connection get it guaranteed, else a prompt to Conexiones. */
function RequireConnection({ children }: { children: ReactNode }) {
  const { selectedId, isLoading, error, refetch } = useApp()
  if (isLoading) return <div className="p-8 text-muted" data-testid="loading">Cargando…</div>
  if (error) return <ErrorBanner error={error} onRetry={refetch} />
  if (selectedId === null)
    return (
      <EmptyState
        title="Selecciona una conexión"
        description="Para ver esta sección primero necesitas configurar y seleccionar una base de datos."
        action={<Link to="/connections"><Button variant="primary" data-testid="goto-connections">Ir a Conexiones</Button></Link>}
      />
    )
  return <>{children}</>
}

export default function App() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-[1400px]">
          <Routes>
            <Route path="/" element={<Navigate to="/charts" replace />} />
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="/charts" element={<RequireConnection><ChartsPage /></RequireConnection>} />
            <Route path="/technical" element={<RequireConnection><TechnicalPage /></RequireConnection>} />
            <Route path="/optimize" element={<RequireConnection><OptimizePage /></RequireConnection>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
