export interface Project {
  id: string
  name: string
  repository_url: string | null
  test_url: string | null
  created_at: string
}

export interface Task {
  id: string
  project_id: string
  report: string
  expected_behavior: string
  actual_behavior: string
  created_at: string
}

export interface Run {
  id: string
  task_id: string
  project_id: string
  state: string
  verdict: string
  media_status: string
  base_commit: string
  model_entry_id: string
  cancel_requested: boolean
  created_at: string
  updated_at: string
}

export interface ModelEntry {
  id: string
  provider: string
  model_id: string
  state: string
  qualified: boolean
  fixture_only: boolean
  context_limit: number | null
  output_limit: number | null
}

export interface RunEvent {
  schema_version: string
  event_id: string
  run_id: string
  sequence: number
  event_type: string
  timestamp: string
  trace_id: string
  payload: Record<string, unknown>
}

export interface ReviewPacket {
  run_id: string
  report: string
  expected_behavior: string
  actual_behavior: string
  base_commit: string
  verification_status: string
  reproduction_status: string
  qualification_scope?: string
  autonomous_repair?: boolean
  media_status: string
  limitations: string[]
  changed_files: string[]
  baseline_tests: CheckReceipt[]
  baseline_browser?: CheckReceipt | null
  baseline_oracle?: CheckReceipt | null
  patched_tests: CheckReceipt[]
  browser_evidence_refs: string[]
  media_manifest_url?: string | null
  evidence_timeline?: { at_seconds: number; label: string; screenshot_url?: string }[]
}

export interface CheckReceipt {
  status: string
  command?: string[]
  exit_code?: number | null
  duration_ms?: number
  tested_tree_sha256?: string
  oracle_sha256?: string
}

export class ApiError extends Error {
  constructor(public code: string, message: string) { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/v1${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(body.code ?? 'REQUEST_FAILED', body.message ?? `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const jsonBody = (value: unknown) => JSON.stringify(value)
