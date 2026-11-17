import type {
  ActiveQuery, ConfigParam, ConnectionIn, ConnectionOut, ExplainRequest, ExplainResult,
  IndexCompareResult, IndexStat, LockInfo, LongestQueryAt, MetricSeries, MonitorStart, MonitorStatus, Overview, Recommendation,
  RecFilters, RecommendationPage, ScriptRequest, ScriptResult, TableStat, TestResult, TopOrder, TopQuery,
} from './types'

const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function detailToString(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg}]
    return detail
      .map((d) => {
        if (d && typeof d === 'object' && 'msg' in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? (d as { loc: unknown[] }).loc.filter((x) => x !== 'body').join('.')
            : ''
          return loc ? `${loc}: ${(d as { msg: string }).msg}` : (d as { msg: string }).msg
        }
        return JSON.stringify(d)
      })
      .join('; ')
  }
  return null
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError('No se pudo contactar con el servidor de Tyrux.', 0)
  }
  if (!res.ok) {
    let msg = `Error ${res.status}`
    try {
      const data = await res.json()
      msg = detailToString(data?.detail) ?? msg
    } catch {
      /* keep default */
    }
    throw new ApiError(msg, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const c = (id: number) => `/connections/${id}`

export const api = {
  listConnections: () => request<ConnectionOut[]>('GET', '/connections'),
  createConnection: (b: ConnectionIn) => request<ConnectionOut>('POST', '/connections', b),
  updateConnection: (id: number, b: ConnectionIn) => request<ConnectionOut>('PUT', c(id), b),
  deleteConnection: (id: number) => request<void>('DELETE', c(id)),
  testConnectionConfig: (b: ConnectionIn) => request<TestResult>('POST', '/connections/test', b),
  testConnection: (id: number) => request<TestResult>('POST', `${c(id)}/test`),

  overview: (id: number) => request<Overview>('GET', `${c(id)}/overview`),
  activeQueries: (id: number) => request<ActiveQuery[]>('GET', `${c(id)}/active-queries`),
  topQueries: (id: number, orderBy: TopOrder, limit = 50) =>
    request<TopQuery[]>('GET', `${c(id)}/top-queries?order_by=${orderBy}&limit=${limit}`),
  explain: (id: number, b: ExplainRequest) => request<ExplainResult>('POST', `${c(id)}/explain`, b),
  tables: (id: number) => request<TableStat[]>('GET', `${c(id)}/tables`),
  indexes: (id: number) => request<IndexStat[]>('GET', `${c(id)}/indexes`),
  locks: (id: number) => request<LockInfo[]>('GET', `${c(id)}/locks`),
  config: (id: number) => request<ConfigParam[]>('GET', `${c(id)}/config`),
  recommendations: (id: number) => request<Recommendation[]>('GET', `${c(id)}/recommendations`),
  recommendationsPage: (id: number, offset: number, limit: number, f: RecFilters = {}, refresh = false) => {
    const p = new URLSearchParams({ offset: String(offset), limit: String(limit) })
    if (f.category) p.set('category', f.category)
    if (f.severity) p.set('severity', f.severity)
    if (f.search?.trim()) p.set('search', f.search.trim())
    if (refresh) p.set('refresh', 'true')
    return request<RecommendationPage>('GET', `${c(id)}/recommendations/page?${p}`)
  },
  optimizeScript: (id: number, b: ScriptRequest | string[]) =>
    request<ScriptResult>('POST', `${c(id)}/optimize/script`, Array.isArray(b) ? { recommendation_ids: b } : b),

  monitorStatus: (id: number) => request<MonitorStatus>('GET', `${c(id)}/monitor`),
  monitorStart: (id: number, b: MonitorStart) => request<MonitorStatus>('POST', `${c(id)}/monitor`, b),
  monitorStop: (id: number) => request<MonitorStatus>('DELETE', `${c(id)}/monitor`),
  metrics: (id: number, minutes: number) =>
    request<MetricSeries>('GET', `${c(id)}/metrics?minutes=${minutes}`),
  metricsLongestQuery: (id: number, at: string) =>
    request<LongestQueryAt>('GET', `${c(id)}/metrics/longest-query?at=${encodeURIComponent(at)}`),
  metricsTopQueries: (id: number, minutes: number) =>
    request<TopQuery[]>('GET', `${c(id)}/metrics/top-queries?minutes=${minutes}`),
  clearMetrics: (id: number) => request<void>('DELETE', `${c(id)}/metrics`),

  compareIndexes: (source_id: number, target_id: number) =>
    request<IndexCompareResult>('POST', '/compare/indexes', { source_id, target_id }),
  compareIndexesScript: (source_id: number, target_id: number, ids: string[]) =>
    request<ScriptResult>('POST', '/compare/indexes/script', { source_id, target_id, ids }),
}
