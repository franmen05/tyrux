// Mirrors backend/app/schemas.py. Datetimes are ISO strings.
export type Engine = 'postgres' | 'oracle'
export type Severity = 'high' | 'medium' | 'low' | 'info'
export type RecCategory =
  | 'missing_index' | 'unused_index' | 'duplicate_index' | 'stale_stats'
  | 'bloat' | 'config' | 'extension' | 'query'

export interface ConnectionIn {
  name: string
  engine: Engine
  host: string
  port: number
  database: string
  username: string
  password: string
  ssl: boolean
  options: Record<string, unknown>
}

export interface ConnectionOut {
  id: number
  name: string
  engine: Engine
  host: string
  port: number
  database: string
  username: string
  ssl: boolean
  options: Record<string, unknown>
  created_at: string
  monitoring: boolean
}

export interface TestResult {
  ok: boolean
  message: string
  version: string | null
  latency_ms: number | null
}

export interface Overview {
  engine: Engine
  version: string
  database: string
  uptime_seconds: number | null
  size_bytes: number | null
  sessions_total: number
  sessions_active: number
  sessions_idle: number
  sessions_blocked: number
  max_connections: number | null
  cache_hit_ratio: number | null
  commits_total: number | null
  rollbacks_total: number | null
  deadlocks_total: number | null
  longest_query_seconds: number | null
  top_queries_available: boolean
  warnings: string[]
}

export interface ActiveQuery {
  session_id: string
  username: string | null
  application: string | null
  client: string | null
  state: string | null
  duration_seconds: number | null
  wait_event: string | null
  blocked_by: string[]
  query_id: string | null
  query: string
}

export interface TopQuery {
  query_id: string
  query: string
  calls: number
  total_time_ms: number
  mean_time_ms: number
  max_time_ms: number | null
  rows: number | null
  disk_reads: number | null
  buffer_gets: number | null
  cache_hit_ratio: number | null
  username: string | null
  pct_total_time: number | null
}

export type TopOrder = 'total_time' | 'mean_time' | 'calls' | 'io'

export interface ExplainRequest {
  query_id?: string | null
  sql?: string | null
  analyze: boolean
}

export interface Finding {
  severity: Severity
  title: string
  detail: string
  sql: string | null
}

export interface ExplainResult {
  query: string
  plan_text: string
  plan_json: unknown
  total_cost: number | null
  findings: Finding[]
}

export interface TableStat {
  schema_name: string
  table: string
  rows_estimate: number | null
  size_bytes: number | null
  total_size_bytes: number | null
  seq_scans: number | null
  seq_rows_read: number | null
  index_scans: number | null
  dead_rows: number | null
  modifications_since_analyze: number | null
  last_analyzed: string | null
  last_vacuum: string | null
}

export interface IndexStat {
  schema_name: string
  table: string
  index: string
  columns: string[]
  definition: string | null
  size_bytes: number | null
  scans: number | null
  is_unique: boolean
  is_primary: boolean
  unused: boolean
  duplicate_of: string | null
  last_used: string | null
}

export interface LockInfo {
  blocked_session: string
  blocked_user: string | null
  blocked_query: string | null
  blocking_session: string
  blocking_user: string | null
  blocking_query: string | null
  lock_type: string | null
  object_name: string | null
  wait_seconds: number | null
}

export interface ConfigParam {
  name: string
  value: string
  unit: string | null
  description: string | null
  category: string | null
  recommended: string | null
}

export interface Recommendation {
  id: string
  category: RecCategory
  severity: Severity
  title: string
  detail: string
  object_name: string | null
  sql: string
  estimated_benefit: string | null
}

export interface ScriptResult {
  engine: Engine
  script: string
  count: number
}

export interface MetricPoint {
  ts: string
  sessions_active: number
  sessions_idle: number
  sessions_total: number
  sessions_blocked: number
  cache_hit_ratio: number | null
  size_bytes: number | null
  longest_query_seconds: number | null
  commits_per_sec: number | null
  rollbacks_per_sec: number | null
  physical_reads_per_sec: number | null
  logical_reads_per_sec: number | null
  deadlocks_per_sec: number | null
}

export interface MetricSeries {
  connection_id: number
  interval_seconds: number | null
  points: MetricPoint[]
}

export interface MonitorStart {
  interval_seconds: number
  duration_minutes?: number | null
}

export interface MonitorStatus {
  connection_id: number
  running: boolean
  interval_seconds: number | null
  started_at: string | null
  ends_at: string | null
  samples: number
  last_sample_at: string | null
  last_error: string | null
}
