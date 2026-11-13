import { useState, type ReactNode, type ButtonHTMLAttributes } from 'react'
import { AlertTriangle, Copy, Inbox, Loader2, RefreshCw, X, ChevronUp, ChevronDown, Search } from 'lucide-react'
import type { Severity } from '../api/types'
import { useApp } from '../lib/app'

export function cn(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(' ')
}

type BtnVariant = 'primary' | 'secondary' | 'danger' | 'ghost'
export function Button({
  variant = 'secondary', loading, className, children, disabled, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; loading?: boolean }) {
  const styles: Record<BtnVariant, string> = {
    primary: 'bg-brand text-brand-fg hover:opacity-90',
    secondary: 'border border-line bg-surface hover:bg-surface2',
    danger: 'bg-red-600 text-white hover:bg-red-700',
    ghost: 'hover:bg-surface2',
  }
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50',
        styles[variant], className,
      )}
    >
      {loading && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  )
}

export function Card({ children, className, ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return <div {...rest} className={cn('rounded-xl border border-line bg-surface', className)}>{children}</div>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 p-8 text-muted" data-testid="loading">
      <Loader2 className="animate-spin" size={18} /> {label ?? 'Cargando…'}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton h-4', className)} />
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4" data-testid="loading">
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-8" />)}
    </div>
  )
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    <div
      role="alert"
      data-testid="error-banner"
      className="flex items-start gap-3 rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm"
    >
      <AlertTriangle size={18} className="mt-0.5 shrink-0 text-red-500" />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-red-600 dark:text-red-400">Ocurrió un error</div>
        <div className="mt-0.5 break-words">{msg}</div>
      </div>
      {onRetry && (
        <Button onClick={onRetry} data-testid="retry-btn"><RefreshCw size={14} /> Reintentar</Button>
      )}
    </div>
  )
}

export function EmptyState({
  title, description, action, icon,
}: { title: string; description?: string; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div data-testid="empty-state" className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <div className="text-muted">{icon ?? <Inbox size={36} />}</div>
      <div className="text-base font-medium">{title}</div>
      {description && <div className="max-w-md text-muted">{description}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-0.5 text-muted">{subtitle}</p>}
      </div>
      {actions}
    </div>
  )
}

export const SEVERITY: Record<Severity, { label: string; cls: string; dot: string }> = {
  high: { label: 'Alta', cls: 'bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/40', dot: 'bg-red-500' },
  medium: { label: 'Media', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/40', dot: 'bg-amber-500' },
  low: { label: 'Baja', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/40', dot: 'bg-sky-500' },
  info: { label: 'Info', cls: 'bg-slate-500/15 text-slate-600 dark:text-slate-300 border-slate-500/40', dot: 'bg-slate-400' },
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  const s = SEVERITY[severity]
  return <span className={cn('inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium', s.cls)}>{s.label}</span>
}

export function Badge({ children, className, ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span {...rest} className={cn('inline-flex items-center gap-1 rounded-md border border-line bg-surface2 px-1.5 py-0.5 text-xs', className)}>
      {children}
    </span>
  )
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}

export function CopyButton({ text, label = 'Copiar', testId }: { text: string; label?: string; testId?: string }) {
  const { toast } = useApp()
  return (
    <Button
      data-testid={testId}
      onClick={async () => toast((await copyToClipboard(text)) ? 'Copiado al portapapeles' : 'No se pudo copiar', 'ok')}
    >
      <Copy size={14} /> {label}
    </Button>
  )
}

export function Code({ children, className, ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLPreElement>) {
  return (
    <pre {...rest} className={cn('overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-surface2 p-3 font-mono text-xs leading-relaxed', className)}>
      {children}
    </pre>
  )
}

export function Modal({
  title, onClose, children, wide, testId,
}: { title: string; onClose: () => void; children: ReactNode; wide?: boolean; testId?: string }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-12" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div data-testid={testId} role="dialog" aria-label={title} className={cn('w-full rounded-xl border border-line bg-surface shadow-2xl', wide ? 'max-w-2xl' : 'max-w-md')}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button aria-label="Cerrar" onClick={onClose} className="rounded p-1 hover:bg-surface2"><X size={18} /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

export function ConfirmDialog({
  title, message, confirmLabel = 'Eliminar', onConfirm, onCancel, loading,
}: { title: string; message: ReactNode; confirmLabel?: string; onConfirm: () => void; onCancel: () => void; loading?: boolean }) {
  return (
    <Modal title={title} onClose={onCancel} testId="confirm-dialog">
      <div className="mb-5">{message}</div>
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel} data-testid="confirm-cancel">Cancelar</Button>
        <Button variant="danger" loading={loading} onClick={onConfirm} data-testid="confirm-accept">{confirmLabel}</Button>
      </div>
    </Modal>
  )
}

export function Drawer({
  title, onClose, children, testId,
}: { title: string; onClose: () => void; children: ReactNode; testId?: string }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside data-testid={testId} className="flex h-full w-full max-w-3xl flex-col border-l border-line bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button aria-label="Cerrar" data-testid="drawer-close" onClick={onClose} className="rounded p-1 hover:bg-surface2"><X size={18} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </aside>
    </div>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  )
}

export const inputCls =
  'w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand'

// ------------------------------------------------------------------ DataTable
export interface Column<T> {
  key: string
  header: string
  render: (row: T) => ReactNode
  sort?: (row: T) => string | number | null | undefined
  align?: 'left' | 'right'
  className?: string
}

export function DataTable<T>({
  columns, rows, rowKey, filterText, rowTestId, onRowClick, initialSort, placeholder = 'Filtrar…', empty, rowClass, toolbar,
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (r: T, i: number) => string
  filterText?: (r: T) => string
  rowTestId: string
  onRowClick?: (r: T) => void
  initialSort?: { key: string; dir: 'asc' | 'desc' }
  placeholder?: string
  empty?: ReactNode
  rowClass?: (r: T) => string
  toolbar?: ReactNode
}) {
  const [sort, setSort] = useState(initialSort ?? null)
  const [filter, setFilter] = useState('')

  let view = rows
  if (filter.trim() && filterText) {
    const f = filter.trim().toLowerCase()
    view = view.filter((r) => filterText(r).toLowerCase().includes(f))
  }
  if (sort) {
    const col = columns.find((c) => c.key === sort.key)
    if (col?.sort) {
      const get = col.sort
      const dir = sort.dir === 'asc' ? 1 : -1
      view = [...view].sort((a, b) => {
        const x = get(a), y = get(b)
        if (x == null && y == null) return 0
        if (x == null) return 1
        if (y == null) return -1
        if (typeof x === 'number' && typeof y === 'number') return (x - y) * dir
        return String(x).localeCompare(String(y), 'es') * dir
      })
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2.5">
        {filterText && (
          <div className="relative w-64 max-w-full">
            <Search size={14} className="absolute left-2.5 top-2.5 text-muted" />
            <input
              data-testid="table-filter"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={placeholder}
              className={cn(inputCls, 'pl-8')}
            />
          </div>
        )}
        {toolbar}
        <span className="ml-auto text-xs text-muted">{view.length} de {rows.length}</span>
      </div>
      {view.length === 0 ? (
        empty ?? <EmptyState title="Sin resultados" description="Ningún elemento coincide con el filtro." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs uppercase tracking-wide text-muted">
                {columns.map((c) => {
                  const active = sort?.key === c.key
                  return (
                    <th
                      key={c.key}
                      data-testid={`th-${c.key}`}
                      className={cn('whitespace-nowrap px-3 py-2 font-medium', c.align === 'right' && 'text-right', c.sort && 'cursor-pointer select-none hover:text-fg')}
                      onClick={() => c.sort && setSort(active ? { key: c.key, dir: sort!.dir === 'asc' ? 'desc' : 'asc' } : { key: c.key, dir: 'desc' })}
                    >
                      <span className="inline-flex items-center gap-1">
                        {c.header}
                        {active && (sort!.dir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />)}
                      </span>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {view.map((r, i) => (
                <tr
                  key={rowKey(r, i)}
                  data-testid={rowTestId}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  className={cn('border-b border-line/60 align-top last:border-0', onRowClick && 'cursor-pointer hover:bg-surface2', rowClass?.(r))}
                >
                  {columns.map((c) => (
                    <td key={c.key} className={cn('px-3 py-2', c.align === 'right' && 'whitespace-nowrap text-right tabular-nums', c.className)}>
                      {c.render(r)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
