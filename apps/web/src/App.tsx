import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, BarChart3, BookOpen, CheckCircle2, ChevronDown, CircleHelp,
  ClipboardList, Code2, FolderGit2, Gauge, Layers3, Menu, Play, Plus,
  RefreshCw, Settings2, ShieldCheck, Square, Trash2, XCircle,
} from 'lucide-react'
import { api, jsonBody, type CheckReceipt, type ModelEntry, type Project, type RepositoryConnection, type ReviewPacket, type Run, type RunEvent, type Task } from './api'
const MediaPlayer = lazy(() => import('./MediaPlayer'))

type Page = 'projects' | 'runs' | 'review' | 'memory' | 'evaluations' | 'operations' | 'usage' | 'settings'
type DevEvaluation = {
  case_id: string; qualification_scope: string; autonomous_repair: boolean
  candidate_origin: string; verdict: string
  baseline_tree_sha256: string; candidate_tree_sha256: string
  results: Record<'baseline' | 'candidate', {
    named_test: string; browser: string; oracle: string
    screenshot_url: string | null; media_manifest_url: string | null
  }>
}
type MemoryStatus = 'proposed' | 'verified' | 'rejected' | 'superseded' | 'expired' | 'deleted'
type Membership = { subject: string; role: string; status: 'active' | 'disabled' }
type TenantQuotas = { daily_inference_cap_usd: string; monthly_inference_cap_usd: string
  max_concurrent_runs: number; daily_export_cap_bytes: number }
type PluginCatalogEntry = { plugin_id: string; version: string; state: string
  transport: string; tools: string[]; allowed: boolean; ready: boolean }
type MemoryRecord = {
  id: string; fact_type: string; repository_ref: string; source_revision: string
  subject: string; statement: string; source_refs: string[]; verification_scope: string | null
  status: MemoryStatus; created_at: string; valid_from: string | null; valid_until: string | null
  history: { status: MemoryStatus; previous_status: MemoryStatus | null; actor: string
    reason: string; evidence_ref: string | null; created_at: string }[]
}
type OperationsSummary = {
  window_start: string; observed_at: string
  components: { id: string; status: string; detail: string }[]
  runs: { by_state: Record<string, number>; closed_by_verdict: Record<string, number>
    closed_count: number; reviewed_count: number; verification_pass_rate: number | null
    inconclusive_rate: number | null; review_acceptance_rate: number | null }
  queue: { queued_count: number; oldest_age_seconds: number | null }
  graph: { pending_count: number; oldest_age_seconds: number | null }
  media_queue: { pending_count: number; oldest_age_seconds: number | null }
  sandbox: { expired_lease_count: number; cleanup_grace_seconds: number }
  tools: { by_policy_result: Record<string, number>; failed_count: number }
  media: { by_status: Record<string, number> }
  model_calls: { status: 'MEASURED' | 'TRUNCATED'; completed_count: number | null
    definite_rejection_count: number | null; uncertain_count: number | null
    unsettled_count: number | null
    completed_latency_ms_p50: number | null; completed_latency_ms_p95: number | null
    definite_rejection_rate: number | null; context_compaction_count: number | null
    estimated_input_tokens_p50: number | null
    input_estimation_error_pct_p50: number | null
    input_estimation_samples: number | null }
  inference_budget: { reserved_usd: string; actual_usd: string }
  exports: { used_bytes_today: number; daily_cap_bytes: number }
  warnings_now: string[]
  warning_details: { alert_id: string; owner: string; impact: string; runbook: string
    evaluation: string }[]
  active_alerts: { id: string; alert_id: string; state: string; generation: number
    severity: string; owner: string; impact: string; runbook: string
    first_seen_at: string; last_observed_at: string; fired_at: string | null
    evidence: Record<string, string | number> }[]
  pager_delivery: { configured: boolean; pending_count: number
    oldest_pending_age_seconds: number | null; delivered_count_24h: number }
  unavailable: string[]
}

const nav: { id: Page; label: string; icon: typeof FolderGit2 }[] = [
  { id: 'projects', label: 'Projects', icon: FolderGit2 },
  { id: 'runs', label: 'Runs', icon: Activity },
  { id: 'review', label: 'Review', icon: ClipboardList },
  { id: 'memory', label: 'Memory', icon: BookOpen },
  { id: 'evaluations', label: 'Evaluations', icon: Gauge },
  { id: 'operations', label: 'Operations', icon: ShieldCheck },
  { id: 'usage', label: 'Usage', icon: BarChart3 },
  { id: 'settings', label: 'Settings', icon: Settings2 },
]

const shortId = (id: string) => id.slice(0, 8)
const date = (value: string) => new Date(value).toLocaleString()
const contextSummaryDigest = (event: RunEvent, runId: string): string | null => {
  if (event.event_type !== 'context.compacted') return null
  const ref = event.payload.summary_ref
  if (typeof ref !== 'string' || !ref.startsWith(`${runId}/context/summary-`)) return null
  const suffix = ref.slice(`${runId}/context/summary-`.length)
  return /^[0-9a-f]{64}\.json$/.test(suffix) ? suffix.slice(0, -5) : null
}

function Status({ value }: { value: string }) {
  const style = ['FAILED', 'CANCELLED', 'DISABLED', 'UNAVAILABLE'].includes(value) ? 'bad'
    : ['INCONCLUSIVE', 'PAUSED_INPUT', 'PAUSED_BUDGET', 'PAUSED_APPROVAL', 'FIXTURE ONLY', 'DEGRADED', 'UNVERIFIED', 'LOCAL_ONLY'].includes(value) ? 'warn'
      : ['COMPLETED', 'PASSED', 'REVIEW_READY', 'QUALIFIED', 'ACTIVE', 'READY', 'ALLOWED', 'SERVING', 'AVAILABLE'].includes(value) ? 'good' : 'neutral'
  return <span className={`status ${style}`}>{value.replaceAll('_', ' ')}</span>
}

function Empty({ title, description }: { title: string; description: string }) {
  return <div className="empty"><Layers3 size={30} aria-hidden="true" /><h3>{title}</h3><p>{description}</p></div>
}

export default function App({ identity, onSignOut }: {
  identity: { tenant_id: string; subject: string; development: boolean }
  onSignOut?: () => void
}) {
  const [page, setPage] = useState<Page>('projects')
  const [projects, setProjects] = useState<Project[]>([])
  const [repositoryConnections, setRepositoryConnections] = useState<RepositoryConnection[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [models, setModels] = useState<ModelEntry[]>([])
  const [usage, setUsage] = useState<{ entries: { run_id: string; reserved_usd: number; actual_usd: number; status: string }[] }>({ entries: [] })
  const [selectedProject, setSelectedProject] = useState<string>('')
  const [selectedRun, setSelectedRun] = useState<string>('')
  const [events, setEvents] = useState<RunEvent[]>([])
  const [packet, setPacket] = useState<ReviewPacket | null>(null)
  const [tab, setTab] = useState<'evidence' | 'changes' | 'logs' | 'environment'>('evidence')
  const [dialog, setDialog] = useState<'project' | 'task' | 'run' | null>(null)
  const [busy, setBusy] = useState(false)
  const [connectionBusy, setConnectionBusy] = useState(false)
  const [error, setError] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [memoryRevision, setMemoryRevision] = useState('')
  const [memoryQuery, setMemoryQuery] = useState('')
  const [memoryFacts, setMemoryFacts] = useState<{ id: string; subject: string; statement: string; source_refs: string[]; verification_scope: string | null }[]>([])
  const [memoryRecordStatus, setMemoryRecordStatus] = useState<MemoryStatus | 'all'>('all')
  const [memoryRecords, setMemoryRecords] = useState<MemoryRecord[]>([])
  const [memoryRecordsMore, setMemoryRecordsMore] = useState(false)
  const [devEvaluation, setDevEvaluation] = useState<DevEvaluation | null>(null)
  const [operations, setOperations] = useState<OperationsSummary | null>(null)
  const [operationsError, setOperationsError] = useState('')
  const [tenantMembers, setTenantMembers] = useState<Membership[]>([])
  const [projectMembers, setProjectMembers] = useState<Membership[]>([])
  const [quotas, setQuotas] = useState<TenantQuotas | null>(null)
  const [plugins, setPlugins] = useState<PluginCatalogEntry[]>([])
  const [settingsLoaded, setSettingsLoaded] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [devFixture, setDevFixture] = useState<{ case_id: string; base_commit: string } | null>(null)

  useEffect(() => {
    if (!menuOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [menuOpen])

  const refresh = useCallback(async () => {
    try {
      const [p, t, r, m, u] = await Promise.all([
        api<Project[]>('/projects'), api<Task[]>('/tasks'), api<Run[]>('/runs'),
        api<ModelEntry[]>('/models'), api<typeof usage>('/usage'),
      ])
      setProjects(p); setTasks(t); setRuns(r); setModels(m); setUsage(u)
      setSelectedProject(current => current || p[0]?.id || '')
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not load workspace') }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  useEffect(() => {
    if (!selectedProject) { setRepositoryConnections([]); return }
    let active = true
    setRepositoryConnections([])
    void api<RepositoryConnection[]>(`/projects/${selectedProject}/repository-connections`)
      .then(rows => { if (active) setRepositoryConnections(rows) })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Could not load repositories') })
    return () => { active = false }
  }, [selectedProject])

  useEffect(() => {
    void api<{ case_id: string; base_commit: string }>('/dev/fixture-info')
      .then(setDevFixture).catch(() => setDevFixture(null))
  }, [])

  useEffect(() => {
    if (page !== 'evaluations') return
    let active = true
    void api<DevEvaluation>('/dev/evaluation').then(value => {
      if (active) setDevEvaluation(value)
    }).catch(() => { if (active) setDevEvaluation(null) })
    return () => { active = false }
  }, [page])

  useEffect(() => {
    if (page !== 'operations') return
    let active = true
    void api<OperationsSummary>('/operations/summary').then(value => {
      if (active) { setOperations(value); setOperationsError('') }
    }).catch(cause => {
      if (active) { setOperations(null); setOperationsError(cause instanceof Error ? cause.message : 'Operations unavailable') }
    })
    return () => { active = false }
  }, [page])

  const loadMemberships = useCallback(async () => {
    const [tenantRows, projectRows, currentQuotas, catalog] = await Promise.all([
      api<Membership[]>('/memberships'),
      selectedProject ? api<Membership[]>(`/projects/${selectedProject}/members`) : Promise.resolve([]),
      api<TenantQuotas>('/tenant/quotas'),
      api<PluginCatalogEntry[]>('/tenant/plugins'),
    ])
    setTenantMembers(tenantRows)
    setProjectMembers(projectRows)
    setQuotas(currentQuotas)
    setPlugins(catalog)
    setSettingsLoaded(true)
    setSettingsError('')
  }, [selectedProject])

  useEffect(() => {
    if (page !== 'settings') return
    let active = true
    void loadMemberships().catch(cause => {
      if (active) setSettingsError(cause instanceof Error ? cause.message : 'Memberships unavailable')
    })
    return () => { active = false }
  }, [page, loadMemberships])

  const loadMemoryRecords = useCallback(async (offset = 0) => {
    if (!selectedProject) { setMemoryRecords([]); return }
    const params = new URLSearchParams({ limit: '50', offset: String(offset) })
    if (memoryRecordStatus !== 'all') params.set('status', memoryRecordStatus)
    const result = await api<{ facts: MemoryRecord[]; has_more: boolean }>(
      `/projects/${selectedProject}/memory/records?${params}`,
    )
    setMemoryRecords(current => offset ? [...current, ...result.facts] : result.facts)
    setMemoryRecordsMore(result.has_more)
  }, [selectedProject, memoryRecordStatus])

  useEffect(() => {
    if (page !== 'memory') return
    void loadMemoryRecords().catch(cause => setError(
      cause instanceof Error ? cause.message : 'Memory records could not be loaded',
    ))
  }, [page, loadMemoryRecords])

  useEffect(() => {
    if (!selectedRun) { setEvents([]); setPacket(null); return }
    let active = true
    void Promise.all([
      api<RunEvent[]>(`/runs/${selectedRun}/events/history`),
      api<ReviewPacket>(`/runs/${selectedRun}/review-packet`),
    ]).then(([history, review]) => { if (active) { setEvents(history); setPacket(review) } })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Run load failed') })
    const stream = new EventSource(`/v1/runs/${selectedRun}/events`)
    const receive = (message: MessageEvent) => {
      try {
        const event = JSON.parse(message.data) as RunEvent
        setEvents(current => current.some(item => item.event_id === event.event_id) ? current : [...current, event].sort((a, b) => a.sequence - b.sequence))
        void refresh()
        if (['verification.completed', 'artifact.ready', 'artifact.failed', 'artifact.deleted', 'review.decision', 'run.closed'].includes(event.event_type)) {
          void api<ReviewPacket>(`/runs/${selectedRun}/review-packet`)
            .then(review => { if (active) setPacket(review) })
            .catch(() => { /* durable history and the next event can retry */ })
        }
      } catch { /* malformed event is ignored; durable history remains source of truth */ }
    }
    stream.onmessage = receive
    return () => { active = false; stream.close() }
  }, [selectedRun, refresh])

  const project = projects.find(item => item.id === selectedProject)
  const run = runs.find(item => item.id === selectedRun)
  const task = tasks.find(item => item.id === run?.task_id)
  const scopedRuns = selectedProject ? runs.filter(item => item.project_id === selectedProject) : runs
  const scopedTasks = selectedProject ? tasks.filter(item => item.project_id === selectedProject) : tasks
  const runnableModels = models.filter(item => (item.qualified && item.hosted_execution_enabled) || (
    item.fixture_only && project?.fixture_case_id === 'form-submit-001'
  ))
  const reservedTotal = useMemo(() => usage.entries.reduce((sum, entry) => sum + entry.reserved_usd, 0), [usage])
  const actualTotal = useMemo(() => usage.entries.reduce((sum, entry) => sum + entry.actual_usd, 0), [usage])

  async function saveMembership(event: React.FormEvent<HTMLFormElement>, scope: 'tenant' | 'project') {
    event.preventDefault(); setBusy(true); setSettingsError('')
    const form = event.currentTarget
    const data = new FormData(form)
    const subject = String(data.get('subject') || '').trim()
    const body = jsonBody({ role: String(data.get('role')), status: String(data.get('status')) })
    try {
      const path = scope === 'tenant' ? `/memberships/${encodeURIComponent(subject)}`
        : `/projects/${selectedProject}/members/${encodeURIComponent(subject)}`
      await api(path, { method: 'PUT', body })
      form.reset()
      await loadMemberships()
    } catch (cause) {
      setSettingsError(cause instanceof Error ? cause.message : 'Membership update failed')
    } finally { setBusy(false) }
  }

  async function saveQuotas(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setSettingsError('')
    const form = event.currentTarget
    const data = new FormData(form)
    try {
      const updated = await api<TenantQuotas>('/tenant/quotas', { method: 'PUT', body: jsonBody({
        daily_inference_cap_usd: String(data.get('daily_inference_cap_usd')),
        monthly_inference_cap_usd: String(data.get('monthly_inference_cap_usd')),
        max_concurrent_runs: Number(data.get('max_concurrent_runs')),
        daily_export_cap_bytes: Number(data.get('daily_export_cap_bytes')),
        reason: String(data.get('reason') || '').trim(),
      }) })
      setQuotas(updated)
      form.reset()
    } catch (cause) {
      setSettingsError(cause instanceof Error ? cause.message : 'Quota update failed')
    } finally { setBusy(false) }
  }

  async function savePluginAccess(
    event: React.FormEvent<HTMLFormElement>, plugin: PluginCatalogEntry,
  ) {
    event.preventDefault(); setBusy(true); setSettingsError('')
    const form = event.currentTarget
    const reason = String(new FormData(form).get('reason') || '').trim()
    try {
      await api(`/tenant/plugins/${encodeURIComponent(plugin.plugin_id)}/${encodeURIComponent(plugin.version)}`, {
        method: 'PUT', body: jsonBody({ allowed: !plugin.allowed, reason }),
      })
      setPlugins(await api<PluginCatalogEntry[]>('/tenant/plugins'))
      form.reset()
    } catch (cause) {
      setSettingsError(cause instanceof Error ? cause.message : 'Plugin access update failed')
    } finally { setBusy(false) }
  }

  async function submitProject(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    const form = new FormData(event.currentTarget)
    try {
      const manifest = JSON.parse(String(form.get('manifest') || '{}'))
      const created = await api<Project>('/projects', { method: 'POST', body: jsonBody({
        name: String(form.get('name')), repository_url: String(form.get('repository_url') || '') || null,
        test_url: String(form.get('test_url') || '') || null, environment_manifest: manifest,
      }) })
      setSelectedProject(created.id); setDialog(null); await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Project creation failed') }
    finally { setBusy(false) }
  }

  async function submitRepositoryConnection(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setConnectionBusy(true); setError('')
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    try {
      await api<RepositoryConnection>(`/projects/${selectedProject}/repository-connections`, {
        method: 'POST', body: jsonBody({
          repository_url: String(form.get('repository_url')).trim(),
          credential_ref: String(form.get('credential_ref') || '').trim() || null,
        }),
      })
      setRepositoryConnections(await api<RepositoryConnection[]>(`/projects/${selectedProject}/repository-connections`))
      formElement.reset()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Repository connection failed') }
    finally { setConnectionBusy(false) }
  }

  async function disableRepositoryConnection(connectionId: string) {
    setConnectionBusy(true); setError('')
    try {
      await api(`/projects/${selectedProject}/repository-connections/${connectionId}`, { method: 'DELETE' })
      setRepositoryConnections(await api<RepositoryConnection[]>(`/projects/${selectedProject}/repository-connections`))
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not disable repository') }
    finally { setConnectionBusy(false) }
  }

  async function submitTask(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    const form = new FormData(event.currentTarget)
    try {
      await api<Task>('/tasks', { method: 'POST', body: jsonBody({
        project_id: selectedProject, report: String(form.get('report')),
        expected_behavior: String(form.get('expected_behavior')),
        actual_behavior: String(form.get('actual_behavior')),
      }) })
      setDialog(null); await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Task creation failed') }
    finally { setBusy(false) }
  }

  async function submitRun(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    const form = new FormData(event.currentTarget)
    try {
      const selectedModel = models.find(item => item.id === String(form.get('model')))
      const repairPaths = String(form.get('repair_paths') ?? '').split(/[\n,]/)
        .map(value => value.trim()).filter(Boolean)
      const reproduction = {
        scenario: String(form.get('scenario')),
        ...(selectedModel?.fixture_only && project?.fixture_case_id
          ? { fixture_case_id: project.fixture_case_id } : {}),
        ...(!selectedModel?.fixture_only
          ? { execution_profile: 'hosted_vm_v1', repair_paths: repairPaths } : {}),
      }
      const requestedSpend = String(form.get('max_spend_usd') || '').trim()
      const created = await api<Run>(`/tasks/${String(form.get('task_id'))}/runs`, {
        method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: jsonBody({ base_commit: String(form.get('base_commit')),
          selected_model_entry: String(form.get('model')),
          mode: 'investigate_and_propose', reproduction,
          ...(requestedSpend ? { max_spend_usd: requestedSpend } : {}),
        }),
      })
      setDialog(null); setSelectedRun(created.id); setPage('runs'); await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Run admission failed') }
    finally { setBusy(false) }
  }

  async function cancel() {
    if (!run) return
    try { await api(`/runs/${run.id}/cancel`, { method: 'POST' }); await refresh() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Cancellation failed') }
  }

  async function resumeInput(inputText: string, idempotencyKey: string) {
    if (!run) return
    setBusy(true); setError('')
    try {
      await api(`/runs/${run.id}/resume`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey },
        body: jsonBody({ input_text: inputText }),
      })
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Resume failed') }
    finally { setBusy(false) }
  }

  async function resumeApproval(reason: string, idempotencyKey: string) {
    if (!run) return
    setBusy(true); setError('')
    try {
      await api(`/runs/${run.id}/resume-approval`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey },
        body: jsonBody({ reason }),
      })
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Approval failed') }
    finally { setBusy(false) }
  }

  async function resumeBudget(reason: string, newLimit: string, idempotencyKey: string) {
    if (!run) return
    setBusy(true); setError('')
    try {
      await api(`/runs/${run.id}/resume-budget`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey },
        body: jsonBody({ reason, new_spend_limit_usd: newLimit }),
      })
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Budget approval failed') }
    finally { setBusy(false) }
  }

  async function decideReview(decision: 'accepted' | 'rejected', reason: string) {
    if (!run) return
    setBusy(true); setError('')
    try {
      await api(`/runs/${run.id}/review-decision`, {
        method: 'POST', body: jsonBody({ decision, reason }),
      })
      const [updated, history] = await Promise.all([
        api<ReviewPacket>(`/runs/${run.id}/review-packet`),
        api<RunEvent[]>(`/runs/${run.id}/events/history`),
      ])
      setPacket(updated); setEvents(history); await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Review decision failed') }
    finally { setBusy(false) }
  }

  async function approveDraft(connectionId: string, baseBranch: string) {
    if (!run) return
    setBusy(true); setError('')
    try {
      await api(`/runs/${run.id}/publication-approval`, {
        method: 'POST', body: jsonBody({ connection_id: connectionId, base_branch: baseBranch }),
      })
      setPacket(await api<ReviewPacket>(`/runs/${run.id}/review-packet`))
      await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Draft PR approval failed') }
    finally { setBusy(false) }
  }

  async function deleteRecording(label: 'baseline' | 'candidate') {
    if (!run || !window.confirm(`Delete the ${label} recording? The run transcript and screenshots will remain.`)) return
    setBusy(true); setError('')
    try {
      await api(`/runs/${run.id}/recordings/${label}`, { method: 'DELETE' })
      const [updated, history] = await Promise.all([
        api<ReviewPacket>(`/runs/${run.id}/review-packet`),
        api<RunEvent[]>(`/runs/${run.id}/events/history`),
      ])
      setPacket(updated); setEvents(history); await refresh()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Recording deletion failed') }
    finally { setBusy(false) }
  }

  async function searchMemory(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError('')
    if (!selectedProject) return
    try {
      const params = new URLSearchParams({ source_revision: memoryRevision, query: memoryQuery })
      const result = await api<{ facts: typeof memoryFacts }>(`/projects/${selectedProject}/memory?${params}`)
      setMemoryFacts(result.facts)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Memory search failed') }
  }

  async function transitionMemory(event: React.FormEvent<HTMLFormElement>, fact: MemoryRecord) {
    event.preventDefault(); setBusy(true); setError('')
    const form = new FormData(event.currentTarget)
    const action = String(form.get('action'))
    try {
      if (action === 'delete') {
        await api(`/projects/${selectedProject}/memory/${fact.id}`, { method: 'DELETE' })
      } else {
        await api(`/projects/${selectedProject}/memory/${fact.id}/transition`, {
          method: 'POST', body: jsonBody({ action, reason: String(form.get('reason') || ''),
            replacement_fact_id: action === 'supersede' ? String(form.get('replacement_fact_id') || '') : null }),
        })
      }
      setMemoryFacts([])
      await loadMemoryRecords()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Memory transition failed') }
    finally { setBusy(false) }
  }

  async function resolveOperationsAlert(event: React.FormEvent<HTMLFormElement>, alertId: string) {
    event.preventDefault(); setBusy(true); setOperationsError('')
    const reason = String(new FormData(event.currentTarget).get('reason') || '')
    try {
      await api(`/operations/alerts/${alertId}/resolve`, {
        method: 'POST', body: jsonBody({ reason }),
      })
      setOperations(await api<OperationsSummary>('/operations/summary'))
    } catch (cause) {
      setOperationsError(cause instanceof Error ? cause.message : 'Alert resolution failed')
    } finally { setBusy(false) }
  }

  function navigate(target: Page) {
    setPage(target); setMenuOpen(false); setError('')
    if (target === 'review' && !selectedRun && runs[0]) setSelectedRun(runs[0].id)
  }

  return <div className="app-shell">
    <aside className={`sidebar ${menuOpen ? 'open' : ''}`}>
      <div className="brand"><span className="brand-mark"><Code2 size={18} /></span><span>Forge</span></div>
      <nav id="app-navigation" aria-label="Main navigation">
        {nav.map(item => <button key={item.id} type="button" className={`nav-item ${page === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)}>
          <item.icon size={18} aria-hidden="true" /><span>{item.label}</span>
        </button>)}
      </nav>
      <div className="sidebar-footer"><span className="avatar">{identity.subject.slice(0, 2).toUpperCase()}</span><div><strong>{identity.development ? 'Local development' : identity.subject}</strong><small>{identity.tenant_id}</small></div></div>
    </aside>
    <div className="app-content">
      <header className="topbar">
        <button type="button" className="icon-button mobile-menu" aria-label={menuOpen ? 'Close navigation' : 'Open navigation'} aria-expanded={menuOpen} aria-controls="app-navigation" onClick={() => setMenuOpen(!menuOpen)}><Menu size={20} /></button>
        <div className="project-picker"><FolderGit2 size={17} /><select aria-label="Selected project" value={selectedProject} onChange={event => setSelectedProject(event.target.value)}>
          {projects.length === 0 && <option value="">No project</option>}
          {projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><ChevronDown size={14} /></div>
        <span className="top-separator" />
        <div className="model-summary"><ShieldCheck size={17} /><span>{models.some(item => item.qualified) ? `${models.filter(item => item.qualified).length} qualified model${models.filter(item => item.qualified).length > 1 ? 's' : ''}` : runnableModels.length ? 'Synthetic fixture only' : 'No qualified model'}</span></div>
        <div className="top-spacer" />
        {run && <><span className="run-chip">Run #{shortId(run.id)}</span><Status value={run.state} /></>}
        <span className="budget-summary">${actualTotal.toFixed(2)} actual · ${reservedTotal.toFixed(2)} reserved</span>
        <button type="button" className="icon-button" aria-label="Refresh workspace" onClick={() => void refresh()}><RefreshCw size={17} /></button>
        {onSignOut && <button type="button" className="icon-button" aria-label="Sign out" onClick={onSignOut}><Square size={17} /></button>}
      </header>
      <main>
        {error && <div className="error-banner" role="alert"><XCircle size={18} />{error}<button type="button" aria-label="Dismiss error" onClick={() => setError('')}>×</button></div>}
        {page === 'projects' && <section className="page-section">
          <div className="page-heading"><div><h1>Projects</h1><p>Connect an authorized repository and a reproducible test environment.</p></div><button className="primary-button" onClick={() => setDialog('project')}><Plus size={17} /> New project</button></div>
          {projects.length === 0 ? <Empty title="No projects yet" description="Create a project to register its repository and test URL." /> : <div className="project-list">{projects.map(item => <button key={item.id} className={`project-row ${selectedProject === item.id ? 'selected' : ''}`} onClick={() => setSelectedProject(item.id)}><FolderGit2 size={20} /><span><strong>{item.name}</strong><small>{item.repository_url || 'Repository not configured'}</small></span><span className="project-meta">{date(item.created_at)}</span></button>)}</div>}
          {project && <>
            <div className="content-panel"><div className="panel-heading"><h2>{project.name}</h2><button className="secondary-button" onClick={() => setDialog('task')}><Plus size={16} /> New report</button></div><div className="field-grid"><div><label>Repository</label><p>{project.repository_url || 'Not configured'}</p></div><div><label>Test URL</label><p>{project.test_url || 'Not configured'}</p></div></div><h3>Recent reports</h3>{scopedTasks.length ? scopedTasks.slice(0, 5).map(item => <div className="list-row" key={item.id}><span>{item.report}</span><small>{date(item.created_at)}</small></div>) : <p className="muted">No reports submitted.</p>}</div>
            <div className="content-panel repository-panel">
              <div className="panel-heading"><div><h2>Repository connections</h2><p className="muted">Choose the repository for future runs.</p></div></div>
              {repositoryConnections.length ? repositoryConnections.map(connection =>
                <div className="list-row repository-row" key={connection.id}>
                  <span><strong>{connection.repository_ref}</strong><small>{connection.readiness === 'credential_required' ? 'Credential needed' : connection.readiness === 'verification_required' ? 'Check required' : connection.readiness === 'ready' ? 'Ready' : 'Disabled'}</small></span>
                  {connection.status !== 'disabled' && <button type="button" className="secondary-button" disabled={connectionBusy} onClick={() => void disableRepositoryConnection(connection.id)}>Disable</button>}
                </div>
              ) : <p className="muted">No repository connected.</p>}
              <form className="repository-form" onSubmit={submitRepositoryConnection} key={project.id}>
                <label>GitHub repository URL<input name="repository_url" type="url" required placeholder="https://github.com/team/repo" defaultValue={project.repository_url?.startsWith('https://github.com/') ? project.repository_url : ''} /></label>
                <label>Secret reference <span className="optional">Optional</span><input name="credential_ref" placeholder="secret://env/AIP_GITHUB_TOKEN" autoComplete="off" /></label>
                <button className="secondary-button" type="submit" disabled={connectionBusy}>Add repository</button>
              </form>
              <p className="muted repository-note">Add a secret reference when one is available. A connection needs a successful check before publication.</p>
            </div>
          </>}
        </section>}
        {page === 'runs' && <section className="page-section">
          <div className="page-heading"><div><h1>Runs</h1><p>Durable execution history and review evidence.</p></div><button className="primary-button" onClick={() => setDialog('run')} disabled={!scopedTasks.length || !runnableModels.length} title={!runnableModels.length ? 'No executable model is available' : undefined}><Play size={16} /> Start run</button></div>
          {!runnableModels.length && <div className="notice"><CircleHelp size={18} /> Registering a model does not qualify it. Validate a provider account and run the conformance suite before execution.</div>}
          {runnableModels.some(item => item.fixture_only) && <div className="notice"><CircleHelp size={18} /> The fixture model only reproduces the reviewed synthetic form bug. It is not a live provider or customer repository run.</div>}
          <div className="workspace-grid"><div className="run-list content-panel"><h2>History</h2>{scopedRuns.length ? scopedRuns.map(item => <button key={item.id} className={`run-row ${selectedRun === item.id ? 'selected' : ''}`} onClick={() => setSelectedRun(item.id)}><span><strong>Run #{shortId(item.id)}</strong><small>{date(item.created_at)}</small></span><Status value={item.state} /></button>) : <Empty title="No runs" description="Submit a report, then start a qualified run." />}</div><div className="run-detail">{run ? <RunWorkspace run={run} task={task} packet={packet} events={events} tab={tab} setTab={setTab} cancel={cancel} resumeInput={resumeInput} resumeApproval={resumeApproval} resumeBudget={resumeBudget} decideReview={decideReview} approveDraft={approveDraft} repositoryConnections={repositoryConnections} deleteRecording={deleteRecording} busy={busy} /> : <Empty title="Select a run" description="Its progress and evidence will appear here." />}</div></div>
        </section>}
        {page === 'review' && <section className="page-section"><div className="page-heading"><div><h1>Review packet</h1><p>Verification claims are linked to actual tool evidence.</p></div></div>{run ? <RunWorkspace run={run} task={task} packet={packet} events={events} tab={tab} setTab={setTab} cancel={cancel} resumeInput={resumeInput} resumeApproval={resumeApproval} resumeBudget={resumeBudget} decideReview={decideReview} approveDraft={approveDraft} repositoryConnections={repositoryConnections} deleteRecording={deleteRecording} busy={busy} /> : <Empty title="No run selected" description="Choose a run from the Runs screen." />}</section>}
        {page === 'operations' && <section className="page-section">
          <div className="page-heading"><div><h1>Operations</h1><p>Owner-only snapshot of persisted control-plane activity.</p></div></div>
          {operationsError && <div className="notice">{operationsError}</div>}
          {operations && <>
            <p className="muted">Last 24 hours · observed {date(operations.observed_at)}. Verification rates use closed runs; reviewer acceptance uses decided reviews.</p>
            <div className="content-panel"><h2>Component status</h2><p className="muted">These statuses describe the evidence available in this snapshot. Unverified components need a live service probe.</p>{operations.components.map(component => <div className="list-row" key={component.id}><span className="component-info"><strong>{component.id.replaceAll('_', ' ')}</strong><small>{component.detail}</small></span><Status value={component.status} /></div>)}</div>
            <div className="ops-grid ops-metrics-grid">
              <div className="content-panel ops-metric"><small>Closed runs</small><strong>{operations.runs.closed_count}</strong><span>Passed verification {operations.runs.verification_pass_rate === null ? 'unavailable' : `${(operations.runs.verification_pass_rate * 100).toFixed(1)}%`} · inconclusive {operations.runs.inconclusive_rate === null ? 'unavailable' : `${(operations.runs.inconclusive_rate * 100).toFixed(1)}%`}</span><span>Reviewer acceptance {operations.runs.review_acceptance_rate === null ? 'unavailable' : `${(operations.runs.review_acceptance_rate * 100).toFixed(1)}%`} of {operations.runs.reviewed_count} decided</span></div>
              <div className="content-panel ops-metric"><small>Runnable queue</small><strong>{operations.queue.queued_count}</strong><span>Oldest {operations.queue.oldest_age_seconds === null ? 'none' : `${Math.round(operations.queue.oldest_age_seconds / 60)} min`}</span></div>
              <div className="content-panel ops-metric"><small>Graph projection backlog</small><strong>{operations.graph.pending_count}</strong><span>Oldest {operations.graph.oldest_age_seconds === null ? 'none' : `${Math.round(operations.graph.oldest_age_seconds)} sec`}</span></div>
              <div className="content-panel ops-metric"><small>Inference budget</small><strong>${Number(operations.inference_budget.actual_usd).toFixed(2)}</strong><span>${Number(operations.inference_budget.reserved_usd).toFixed(2)} outstanding reservations</span></div>
              <div className="content-panel ops-metric"><small>Model calls</small><strong>{operations.model_calls.completed_count ?? '—'}</strong><span>{operations.model_calls.status === 'TRUNCATED' ? 'Summary limit reached' : `${operations.model_calls.definite_rejection_count} rejected · ${operations.model_calls.uncertain_count} uncertain · ${operations.model_calls.unsettled_count} open`}</span><span>Completed call p95 {operations.model_calls.completed_latency_ms_p95 === null ? 'unavailable' : `${(operations.model_calls.completed_latency_ms_p95 / 1000).toFixed(1)} s`}</span><span>Context compacted {operations.model_calls.context_compaction_count ?? '—'} times · median estimated input {operations.model_calls.estimated_input_tokens_p50 ?? '—'} tokens</span><span>Median input estimate error {operations.model_calls.input_estimation_error_pct_p50 === null ? 'unavailable' : `${operations.model_calls.input_estimation_error_pct_p50.toFixed(1)}%`} ({operations.model_calls.input_estimation_samples ?? 0} completed samples)</span></div>
              <div className="content-panel ops-metric"><small>Media jobs waiting</small><strong>{operations.media_queue.pending_count}</strong><span>Oldest {operations.media_queue.oldest_age_seconds === null ? 'none' : `${Math.round(operations.media_queue.oldest_age_seconds / 60)} min`}</span></div>
              <div className="content-panel ops-metric"><small>Expired sandbox leases</small><strong>{operations.sandbox.expired_lease_count}</strong><span>After {Math.round(operations.sandbox.cleanup_grace_seconds / 60)} min cleanup grace</span></div>
            </div>
            <div className="ops-grid">
              <div className="content-panel"><h2>Run states</h2>{Object.entries(operations.runs.by_state).length ? Object.entries(operations.runs.by_state).map(([state, count]) => <div className="list-row" key={state}><span>{state.replaceAll('_', ' ')}</span><strong>{count}</strong></div>) : <p className="muted">No runs in this window.</p>}</div>
              <div className="content-panel"><h2>Tools, media and exports</h2><div className="list-row"><span>Failed tool effects</span><strong>{operations.tools.failed_count}</strong></div>{Object.entries(operations.tools.by_policy_result).map(([policy, count]) => <div className="list-row" key={policy}><span>Tool policy: {policy}</span><strong>{count}</strong></div>)}{Object.entries(operations.media.by_status).map(([status, count]) => <div className="list-row" key={status}><span>Media: {status}</span><strong>{count}</strong></div>)}<div className="list-row"><span>Evidence ZIP today</span><strong>{(operations.exports.used_bytes_today / 1_000_000).toFixed(2)} / {(operations.exports.daily_cap_bytes / 1_000_000).toFixed(0)} MB</strong></div></div>
            </div>
            <div className="content-panel"><h2>Durable alerts</h2>{operations.active_alerts.length ? operations.active_alerts.map(item => <div className="ops-warning" key={item.id}><strong>{item.alert_id.replaceAll('_', ' ')} · {item.state} · {item.severity}</strong><small>{item.impact} Owner: {item.owner}. First seen {date(item.first_seen_at)}.</small><small>Runbook: {item.runbook}</small>{item.state === 'firing' && <form onSubmit={event => void resolveOperationsAlert(event, item.id)}><label>Resolution reason<input name="reason" minLength={8} maxLength={2000} required placeholder="What was checked and fixed?" /></label><button className="secondary-button" disabled={busy}>Resolve</button></form>}</div>) : <p className="muted">No active alerts recorded.</p>}<p className="muted">Alert state updates while the operations evaluator is running. Pager destination: {operations.pager_delivery.configured ? 'configured' : 'not configured'} · {operations.pager_delivery.pending_count} pending · {operations.pager_delivery.delivered_count_24h} delivered in 24 hours.</p></div>
            <div className="content-panel"><h2>Current threshold checks</h2>{operations.warning_details.length ? operations.warning_details.map(item => <p className="ops-warning" key={item.alert_id}>{item.alert_id.replaceAll('_', ' ')} · {item.owner}<small>{item.impact}</small></p>) : <p className="muted">No current queue, graph, media or sandbox threshold is exceeded.</p>}<p className="muted">These instantaneous checks may differ from sustained alert state.</p></div>
            <div className="content-panel"><h2>Metrics awaiting instrumentation</h2><p className="muted">{operations.unavailable.map(item => item.replaceAll('_', ' ')).join(' · ')}</p></div>
          </>}
        </section>}
        {page === 'usage' && <section className="page-section"><div className="page-heading"><div><h1>Usage</h1><p>Reservations and actual charges from the run ledger.</p></div></div><div className="summary-strip"><div><small>Reserved</small><strong>${reservedTotal.toFixed(2)}</strong></div><div><small>Actual</small><strong>${actualTotal.toFixed(2)}</strong></div><div><small>Ledger entries</small><strong>{usage.entries.length}</strong></div></div><div className="content-panel"><h2>Ledger</h2>{usage.entries.length ? usage.entries.map((entry, index) => <div className="list-row" key={`${entry.run_id}-${index}`}><span>Run #{shortId(entry.run_id)} · {entry.status}</span><strong>${entry.reserved_usd.toFixed(2)} reserved</strong></div>) : <Empty title="No usage" description="Charges will be recorded when qualified runs execute." />}</div></section>}
        {page === 'settings' && <section className="page-section">
          <div className="page-heading"><div><h1>Settings</h1><p>Model readiness, workspace budgets and owner-managed access.</p></div></div>
          {settingsError && <div className="notice" role="alert">{settingsError}</div>}
          <div className="content-panel"><h2>Models</h2>{models.length ? models.map(model => <div className="list-row" key={model.id}><span>{model.provider} · {model.model_id}</span><Status value={model.qualified ? 'QUALIFIED' : model.fixture_only ? 'FIXTURE ONLY' : model.state.toUpperCase()} /></div>) : <Empty title="No model entries" description="Register a model through the versioned registry. Live conformance is required before enabling it." />}</div>
          {settingsLoaded && <div className="content-panel plugin-access-panel"><h2>Plugin access</h2><p className="muted">A trusted operator validates plugin versions first. Workspace owners can allow an enabled version or revoke access. Allowing a version does not qualify its execution adapter.</p>{plugins.length ? plugins.map(plugin => <div className="plugin-row" key={`${plugin.plugin_id}@${plugin.version}`}><strong>{plugin.plugin_id} · {plugin.version}</strong><small>{plugin.transport} · {plugin.tools.join(', ') || 'No reviewed tools'} · {plugin.state}</small><Status value={plugin.ready ? plugin.allowed ? 'ALLOWED' : 'READY' : 'UNAVAILABLE'} /><form onSubmit={event => void savePluginAccess(event, plugin)}><label>Reason for {plugin.allowed ? 'revocation' : 'access'}<input name="reason" minLength={8} maxLength={2000} required placeholder="Reviewed plugin access decision" /></label><button className="secondary-button" disabled={busy || (!plugin.ready && !plugin.allowed)}>{plugin.allowed ? 'Revoke access' : 'Allow plugin'}</button></form></div>) : <p className="muted">No plugin versions are registered. An operator must validate one before it can be allowed.</p>}</div>}
          {settingsLoaded && <div className="ops-grid settings-grid">
            {quotas && <div className="content-panel"><h2>Workspace quotas</h2><p className="muted">Changes apply to new reservations and admissions. Existing settled charges remain in the ledger.</p><form key={JSON.stringify(quotas)} onSubmit={event => void saveQuotas(event)}><label>Daily inference cap (USD)<input name="daily_inference_cap_usd" type="number" min="0.000001" max="999999.999999" step="0.000001" defaultValue={quotas.daily_inference_cap_usd} required /></label><label>Monthly inference cap (USD)<input name="monthly_inference_cap_usd" type="number" min="0.000001" max="999999.999999" step="0.000001" defaultValue={quotas.monthly_inference_cap_usd} required /></label><label>Maximum concurrent runs<input name="max_concurrent_runs" type="number" min="1" max="1000" step="1" defaultValue={quotas.max_concurrent_runs} required /></label><label>Daily evidence export cap (bytes)<input name="daily_export_cap_bytes" type="number" min="1" max="2000000000" step="1" defaultValue={quotas.daily_export_cap_bytes} required /></label><label>Reason for change<input name="reason" minLength={8} maxLength={2000} required placeholder="Approved spending and capacity policy" /></label><button className="secondary-button" disabled={busy}>Save quotas</button></form></div>}
            <div className="content-panel"><h2>Workspace members</h2><p className="muted">Only owners can change tenant access. The last active owner cannot be disabled.</p>{tenantMembers.map(member => <div className="list-row" key={member.subject}><span>{member.subject} · {member.role}</span><Status value={member.status.toUpperCase()} /></div>)}{!tenantMembers.length && <p className="muted">No members recorded.</p>}<form onSubmit={event => void saveMembership(event, 'tenant')}><label>Identity subject<input name="subject" required maxLength={200} placeholder="OIDC subject" /></label><label>Workspace role<select name="role"><option value="member">Member</option><option value="owner">Owner</option></select></label><label>Status<select name="status"><option value="active">Active</option><option value="disabled">Disabled</option></select></label><button className="secondary-button" disabled={busy}>Save workspace access</button></form></div>
            <div className="content-panel"><h2>Project members</h2><p className="muted">{project ? `Access to ${project.name}. Add a workspace member first.` : 'Select a project to manage access.'}</p>{projectMembers.map(member => <div className="list-row" key={member.subject}><span>{member.subject} · {member.role}</span><Status value={member.status.toUpperCase()} /></div>)}{!projectMembers.length && <p className="muted">No project members recorded.</p>}{selectedProject && <form onSubmit={event => void saveMembership(event, 'project')}><label>Identity subject<input name="subject" required maxLength={200} placeholder="Existing workspace member" /></label><label>Project role<select name="role"><option value="viewer">Viewer</option><option value="reviewer">Reviewer</option><option value="contributor">Contributor</option><option value="maintainer">Maintainer</option></select></label><label>Status<select name="status"><option value="active">Active</option><option value="disabled">Disabled</option></select></label><button className="secondary-button" disabled={busy}>Save project access</button></form>}</div>
          </div>}
        </section>}
        {page === 'memory' && <section className="page-section">
          <div className="page-heading"><div><h1>Memory</h1><p>Inspect source-backed facts and their decisions.</p></div></div>
          <form className="memory-search content-panel" onSubmit={searchMemory}>
            <label>Commit SHA<input value={memoryRevision} onChange={event => setMemoryRevision(event.target.value)} pattern="[0-9a-fA-F]{40}" required placeholder="40-character Git commit SHA" /></label>
            <label>Search text<input value={memoryQuery} onChange={event => setMemoryQuery(event.target.value)} placeholder="File, symbol or incident" /></label>
            <button className="secondary-button" disabled={!selectedProject}>Search current facts</button>
          </form>
          <div className="content-panel"><h2>Current facts at this revision</h2>
            {memoryFacts.length ? memoryFacts.map(fact => <div className="memory-fact" key={fact.id}>
              <strong>{fact.subject}</strong><p>{fact.statement}</p>
              <small>{fact.verification_scope || 'Scope not recorded'} · {fact.source_refs.join(', ')}</small>
            </div>) : <p className="muted">Search an exact commit to see current verified facts.</p>}
          </div>
          <div className="content-panel"><div className="panel-heading"><h2>Lifecycle and provenance</h2>
            <label className="memory-filter">Status <select value={memoryRecordStatus} onChange={event => setMemoryRecordStatus(event.target.value as MemoryStatus | 'all')}>
              {(['all', 'proposed', 'verified', 'rejected', 'superseded', 'expired', 'deleted'] as const).map(value => <option key={value} value={value}>{value}</option>)}
            </select></label></div>
            {memoryRecords.length ? memoryRecords.map(fact => <article className="memory-record" key={fact.id}>
              <div className="panel-heading"><strong>{fact.subject}</strong><Status value={fact.status.toUpperCase()} /></div>
              <p>{fact.statement}</p>
              <small>{fact.fact_type} · commit {shortId(fact.source_revision)} · {fact.verification_scope || 'Not verified'} · {date(fact.created_at)}</small>
              {fact.valid_until && <small>Valid until {date(fact.valid_until)}</small>}
              <details><summary>Sources and history</summary>
                <p className="mono">{fact.source_refs.join(', ')}</p>
                {fact.history.map((entry, index) => <p key={`${entry.status}-${index}`} className="memory-history">
                  <strong>{entry.status}</strong> · {entry.actor} · {date(entry.created_at)} · {entry.reason}
                  {entry.evidence_ref && <> · {entry.evidence_ref}</>}
                </p>)}
              </details>
              {(fact.status === 'proposed' || fact.status === 'verified') && <details><summary>Change status</summary>
                <form className="memory-action" onSubmit={event => transitionMemory(event, fact)}>
                  <label>Action<select name="action" defaultValue="reject">
                    <option value="reject">Reject</option>
                    {fact.status === 'verified' && <><option value="expire">Expire</option><option value="supersede">Supersede</option></>}
                    <option value="delete">Delete</option>
                  </select></label>
                  <label>Reason<input name="reason" minLength={5} placeholder="Evidence or policy reason" /></label>
                  <label>Replacement fact ID<input name="replacement_fact_id" placeholder="Required for supersession" /></label>
                  <button className="secondary-button" disabled={busy}>Apply</button>
                </form>
              </details>}
            </article>) : <p className="muted">No memory records match this project and status.</p>}
            {memoryRecordsMore && <button className="secondary-button" onClick={() => void loadMemoryRecords(memoryRecords.length)} disabled={busy}>Load more</button>}
          </div>
        </section>}
        {page === 'evaluations' && <section className="page-section"><div className="page-heading"><div><h1>Evaluations</h1><p>Recorded checks for a controlled local fixture.</p></div></div>{devEvaluation ? <>
          <div className="notice"><ShieldCheck size={19} /><span>This is a {devEvaluation.qualification_scope.replaceAll('_', ' ')} with a manually supplied candidate. It does not qualify an autonomous repair or hosted sandbox.</span></div>
          <div className="content-panel"><div className="panel-heading"><h2>{devEvaluation.case_id}</h2><Status value={devEvaluation.verdict} /></div><p className="muted">Scope: {devEvaluation.qualification_scope.replaceAll('_', ' ')} · Candidate: {devEvaluation.candidate_origin}</p></div>
          <div className="evaluation-grid">{(['baseline', 'candidate'] as const).map(label => {
            const result = devEvaluation.results[label]
            return <div className="content-panel" key={label}><h2>{label === 'baseline' ? 'Before' : 'After'}</h2>
              <div className="verdict-line"><span>Named test</span><Status value={result.named_test} /></div>
              <div className="verdict-line"><span>Browser scenario</span><Status value={result.browser} /></div>
              <div className="verdict-line"><span>Hidden oracle</span><Status value={result.oracle} /></div>
              {result.media_manifest_url && <Suspense fallback={<p className="muted">Loading player…</p>}><MediaPlayer manifestUrl={result.media_manifest_url} /></Suspense>}
              {result.screenshot_url && <div className="screenshot-view"><h4>Final screenshot</h4><img src={result.screenshot_url} alt={`${label} browser result`} /></div>}
              <small className="mono">Tree: {label === 'baseline' ? devEvaluation.baseline_tree_sha256 : devEvaluation.candidate_tree_sha256}</small>
            </div>
          })}</div>
        </> : <Empty title="No local evaluation loaded" description="Run the controlled fixture evaluator to produce recorded evidence." />}</section>}
      </main>
    </div>
    {dialog && <div className="dialog-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setDialog(null) }}><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div className="panel-heading"><h2 id="dialog-title">{dialog === 'project' ? 'New project' : dialog === 'task' ? 'New bug report' : 'Start run'}</h2><button className="icon-button" aria-label="Close" onClick={() => setDialog(null)}><XCircle size={19} /></button></div>
      {dialog === 'project' && <form onSubmit={submitProject}><label>Name<input name="name" minLength={2} required placeholder="Web application" /></label><label>Repository URL<input name="repository_url" type="url" placeholder="https://github.com/organization/repository" /></label><label>Test URL<input name="test_url" type="url" placeholder="https://staging.example.com" /></label><label>Environment manifest (JSON)<textarea name="manifest" rows={5} defaultValue={'{"named_tests": {}}'} /></label><button className="primary-button" disabled={busy}>Create project</button></form>}
      {dialog === 'task' && <form onSubmit={submitTask}><label>Bug report<textarea name="report" rows={4} minLength={10} required placeholder="Describe the failure and reproduction steps" /></label><label>Expected behavior<textarea name="expected_behavior" rows={2} required /></label><label>Actual behavior<textarea name="actual_behavior" rows={2} required /></label><button className="primary-button" disabled={busy}>Submit report</button></form>}
      {dialog === 'run' && <form onSubmit={submitRun}><label>Report<select name="task_id" required>{scopedTasks.map(item => <option key={item.id} value={item.id}>{item.report.slice(0, 80)}</option>)}</select></label><label>Pinned base commit<input name="base_commit" pattern="[0-9a-fA-F]{40}" required defaultValue={project?.fixture_case_id === devFixture?.case_id ? devFixture?.base_commit : ''} placeholder="40-character Git commit SHA" /></label><label>Available model<select name="model" required>{runnableModels.map(item => <option key={item.id} value={item.id}>{item.fixture_only ? 'Synthetic fixture' : item.provider} · {item.model_id}</option>)}</select></label><label>Run budget in USD<input name="max_spend_usd" type="number" min="0.000001" max="999999.999999" step="0.000001" placeholder="Operator default" /><small>Optional lower cap for this run. An owner may approve an increase up to the operator limit after a budget pause.</small></label><label>Reproduction scenario<input name="scenario" placeholder="Describe the browser action" /></label><label>Repair scope for hosted runs<input name="repair_paths" placeholder="src/app.py, src/routes.py" /><small>List up to four existing source files, separated by commas. The agent can edit only these files.</small></label><button className="primary-button" disabled={busy}>Admit run</button></form>}
    </div></div>}
  </div>
}

function RunWorkspace({ run, task, packet, events, tab, setTab, cancel, resumeInput, resumeApproval, resumeBudget, decideReview, approveDraft, repositoryConnections, deleteRecording, busy }: {
  run: Run; task?: Task; packet: ReviewPacket | null; events: RunEvent[]
  tab: 'evidence' | 'changes' | 'logs' | 'environment'
  setTab: (tab: 'evidence' | 'changes' | 'logs' | 'environment') => void
  cancel: () => void
  resumeInput: (inputText: string, idempotencyKey: string) => Promise<void>
  resumeApproval: (reason: string, idempotencyKey: string) => Promise<void>
  resumeBudget: (reason: string, newLimit: string, idempotencyKey: string) => Promise<void>
  decideReview: (decision: 'accepted' | 'rejected', reason: string) => Promise<void>
  approveDraft: (connectionId: string, baseBranch: string) => Promise<void>
  repositoryConnections: RepositoryConnection[]
  deleteRecording: (label: 'baseline' | 'candidate') => Promise<void>
  busy: boolean
}) {
  const [rejectionReason, setRejectionReason] = useState('')
  const [answer, setAnswer] = useState('')
  const [approvalReason, setApprovalReason] = useState('')
  const [budgetReason, setBudgetReason] = useState('')
  const [newBudget, setNewBudget] = useState('')
  const readyConnections = repositoryConnections.filter(connection => connection.status === 'ready')
  const [resumeKey, setResumeKey] = useState('')
  useEffect(() => { setAnswer(''); setApprovalReason(''); setBudgetReason(''); setNewBudget(''); setResumeKey('') }, [run.id, run.state])
  return <div className="review-layout">
    <div className="review-left"><div className="review-header"><div><small>Run #{shortId(run.id)} · {date(run.created_at)}</small><h2>{task?.report || 'Loading report'}</h2></div><Status value={run.state} /></div>
      <div className="content-panel bug-report"><div className="section-title"><ClipboardList size={18} /><h3>Bug report</h3></div><p>{task?.report || 'Loading…'}</p><dl><dt>Expected</dt><dd>{task?.expected_behavior || '—'}</dd><dt>Actual</dt><dd>{task?.actual_behavior || '—'}</dd></dl></div>
      <div className="content-panel progress-panel"><div className="section-title"><Activity size={18} /><h3>Run progress</h3></div>{events.length ? <ol className="timeline">{events.map(event => <li key={event.event_id}><span className="timeline-node" /><div><strong>{event.event_type.replaceAll('.', ' · ')}</strong><small>{date(event.timestamp)}</small><p>{Object.entries(event.payload).map(([key, value]) => `${key}: ${String(value)}`).join(' · ')}</p></div></li>)}</ol> : <p className="muted">No durable events recorded yet.</p>}</div>
    </div>
    <div className="review-right"><div className="tabbar" role="tablist" aria-label="Run details">{(['evidence', 'changes', 'logs', 'environment'] as const).map(item => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item === 'changes' ? 'Changed files' : item[0].toUpperCase() + item.slice(1)}</button>)}</div>
      <div className="content-panel detail-panel">{tab === 'evidence' && <EvidencePanel packet={packet} run={run} deleteRecording={deleteRecording} busy={busy} />}
        {tab === 'changes' && <><div className="section-title"><Code2 size={18} /><h3>Changed files</h3></div>{packet?.changed_files.length ? packet.changed_files.map(file => <div className="list-row" key={file}>{file}</div>) : <p className="muted">No patch has been produced.</p>}{packet?.diagnosis_hypothesis && <p className="muted">Model hypothesis: {packet.diagnosis_hypothesis}</p>}{packet?.patch_url && <PatchViewer url={packet.patch_url} />}</>}
        {tab === 'logs' && <><div className="section-title"><Activity size={18} /><h3>Activity log</h3></div>{packet?.diagnosis_evidence_refs?.map(ref => <a className="list-row" href={ref} download key={ref}>Download {ref.split('/').at(-2)?.replaceAll('_', ' ')} verification log</a>)}{events.map(event => {
          const digest = contextSummaryDigest(event, run.id)
          return <div className="list-row" key={event.event_id}><span>{event.event_type}{digest && <> · <a href={`/v1/runs/${run.id}/context/${digest}`} download>Summary</a> · <a href={`/v1/runs/${run.id}/context/${digest}?include_source=true`} download>Original context</a></>}</span><small>{date(event.timestamp)}</small></div>
        })}</>}
        {tab === 'environment' && <><div className="section-title"><FolderGit2 size={18} /><h3>Pinned environment</h3></div><dl><dt>Base commit</dt><dd className="mono">{run.base_commit}</dd><dt>Model entry</dt><dd>{run.model_entry_id}</dd></dl></>}
      </div>
      <div className="content-panel verdict-panel"><div className="section-title"><CheckCircle2 size={18} /><h3>Review status</h3></div><p><Status value={run.verdict} /> {packet?.limitations.join(' ') || 'The verdict covers only recorded verification evidence.'}</p>
        {packet && <a href={`/v1/runs/${run.id}/review-packet/download`} download={`aip-review-${run.id}.json`}>Download review packet</a>}
        {packet && <p><a href={`/v1/runs/${run.id}/evidence-bundle`} download={`aip-evidence-${run.id}.zip`}>Download evidence bundle</a></p>}
        {packet?.publication_status && <p>Draft PR: <Status value={packet.publication_status} /> {packet.publication_url && <a href={packet.publication_url} target="_blank" rel="noopener noreferrer">Open draft PR</a>}</p>}
        {packet?.review_decision && <p>Reviewer decision: <strong>{packet.review_decision}</strong>{packet.review_reason ? ` — ${packet.review_reason}` : ''}</p>}
        {run.state === 'PAUSED_INPUT' && <form className="review-actions" onSubmit={event => {
          event.preventDefault()
          const key = resumeKey || crypto.randomUUID()
          if (!resumeKey) setResumeKey(key)
          void resumeInput(answer.trim(), key)
        }}>
          <p className="muted">This run is waiting for input. Your answer is recorded with the run before it is requeued.</p>
          <label>Answer to continue<textarea value={answer} onChange={event => { setAnswer(event.target.value); setResumeKey('') }} minLength={5} maxLength={4000} rows={3} required /></label>
          <button className="primary-button" type="submit" disabled={busy || answer.trim().length < 5}>Resume run</button>
        </form>}
        {run.state === 'PAUSED_APPROVAL' && <form className="review-actions" onSubmit={event => {
          event.preventDefault()
          const key = resumeKey || crypto.randomUUID()
          if (!resumeKey) setResumeKey(key)
          void resumeApproval(approvalReason.trim(), key)
        }}>
          <p className="muted">A workspace owner may resume this run within 24 hours after the pinned model is requalified. Unresolved tool effects or changed policy still block it.</p>
          <label>Approval reason<textarea value={approvalReason} onChange={event => { setApprovalReason(event.target.value); setResumeKey('') }} minLength={8} maxLength={2000} rows={2} required /></label>
          <button className="secondary-button" type="submit" disabled={busy || approvalReason.trim().length < 8}>Approve resume</button>
        </form>}
        {run.state === 'PAUSED_BUDGET' && <form className="review-actions" onSubmit={event => {
          event.preventDefault()
          const key = resumeKey || crypto.randomUUID()
          if (!resumeKey) setResumeKey(key)
          void resumeBudget(budgetReason.trim(), newBudget, key)
        }}>
          <p className="muted">This run reached its ${run.spend_limit_usd} limit. A workspace owner can approve a higher cap within 24 hours, subject to the operator and tenant limits.</p>
          <label>New run budget in USD<input type="number" min="0.000001" max="999999.999999" step="0.000001" value={newBudget} onChange={event => { setNewBudget(event.target.value); setResumeKey('') }} required /></label>
          <label>Approval reason<textarea value={budgetReason} onChange={event => { setBudgetReason(event.target.value); setResumeKey('') }} minLength={8} maxLength={2000} rows={2} required /></label>
          <button className="secondary-button" type="submit" disabled={busy || budgetReason.trim().length < 8 || !newBudget || Number(newBudget) <= Number(run.spend_limit_usd)}>Approve budget and resume</button>
        </form>}
        {run.state === 'REVIEW_READY' && <div className="review-actions">
          <p className="muted">Accepting this packet records a review decision. Draft PR publication requires separate approval.</p>
          <button className="primary-button" disabled={busy} onClick={() => void decideReview('accepted', '')}>Accept packet</button>
          <label>Reason to reject<textarea value={rejectionReason} onChange={event => setRejectionReason(event.target.value)} maxLength={2000} rows={2} /></label>
          <button className="secondary-button" disabled={busy || rejectionReason.trim().length < 5} onClick={() => void decideReview('rejected', rejectionReason)}>Reject packet</button>
        </div>}
        {run.state === 'COMPLETED' && packet?.review_decision === 'accepted' && packet.qualification_scope === 'declared_guest_checks' && packet.publication_status === 'DISABLED' && (readyConnections.length ?
          <form className="review-actions" onSubmit={event => {
            event.preventDefault()
            const form = new FormData(event.currentTarget)
            void approveDraft(String(form.get('connection_id')), String(form.get('base_branch')))
          }}>
            <p className="muted">Approve one draft PR for this exact patch and recorded test evidence. Approval expires after 24 hours. Merge and deployment remain separate.</p>
            <label>Repository<select name="connection_id" required>{readyConnections.map(connection => <option key={connection.id} value={connection.id}>{connection.repository_ref}</option>)}</select></label>
            <label>Base branch<input name="base_branch" defaultValue="main" required /></label>
            <button className="secondary-button" disabled={busy}>Approve draft PR</button>
          </form> : <p className="muted">A ready GitHub connection is required before draft PR approval.</p>)}
        {packet?.publication_status === 'APPROVED' && <p className="muted">Draft PR approval is recorded. A configured publication worker can now create the draft.</p>}
        {!['COMPLETED', 'FAILED', 'CANCELLED', 'INCONCLUSIVE', 'REVIEW_READY'].includes(run.state) && <button className="secondary-button" onClick={cancel}><Square size={14} /> Cancel run</button>}
      </div>
    </div>
  </div>
}

function PatchViewer({ url }: { url: string }) {
  const [diff, setDiff] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    fetch(url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(`Patch unavailable (${response.status})`)
      setDiff(await response.text())
      setError('')
    }).catch(reason => {
      if (!controller.signal.aborted) setError(String(reason))
    })
    return () => controller.abort()
  }, [url])
  return <div className="patch-viewer">
    <a href={url} download>Download verified patch</a>
    {error ? <p role="alert">{error}</p> : diff ? <pre>{diff}</pre> : <p className="muted">Loading patch…</p>}
  </div>
}

function ReceiptRow({ label, receipt }: { label: string; receipt: CheckReceipt }) {
  return <div className="receipt-row">
    <div className="verdict-line"><span>{label}</span><Status value={receipt.status} /></div>
    {receipt.command && <small className="mono">{receipt.command.join(' ')}</small>}
    {(receipt.exit_code !== undefined || receipt.duration_ms !== undefined) &&
      <small>Exit: {receipt.exit_code ?? 'none'} · Duration: {receipt.duration_ms ?? 'unknown'} ms</small>}
    {receipt.tested_tree_sha256 && <small className="mono">Tree SHA-256: {receipt.tested_tree_sha256}</small>}
  </div>
}

function EvidencePanel({ packet, run, deleteRecording, busy }: {
  packet: ReviewPacket | null; run: Run
  deleteRecording: (label: 'baseline' | 'candidate') => Promise<void>; busy: boolean
}) {
  const baselineChecks: { label: string; receipt: CheckReceipt }[] = [
    ...(packet?.baseline_tests.map((receipt, index) => ({ label: `Named test ${index + 1}`, receipt })) ?? []),
    ...(packet?.baseline_browser ? [{ label: 'Browser scenario', receipt: packet.baseline_browser }] : []),
    ...(packet?.baseline_oracle ? [{ label: 'Independent oracle', receipt: packet.baseline_oracle }] : []),
  ]
  const candidateChecks: { label: string; receipt: CheckReceipt }[] = [
    ...(packet?.patched_tests.map((receipt, index) => ({ label: `Named test ${index + 1}`, receipt })) ?? []),
    ...(packet?.candidate_browser ? [{ label: 'Browser scenario', receipt: packet.candidate_browser }] : []),
    ...(packet?.candidate_oracle ? [{ label: 'Independent oracle', receipt: packet.candidate_oracle }] : []),
  ]
  return <>
    <div className="section-title"><ShieldCheck size={18} /><h3>Verification</h3></div>
    <div className="verdict-line"><span>Reproduction</span><Status value={packet?.reproduction_status || 'NOT_RUN'} /></div>
    <div className="verdict-line"><span>Code verification</span><Status value={packet?.verification_status || run.verdict} /></div>
    <div className="verdict-line"><span>Media</span><Status value={packet?.media_status || run.media_status} /></div>
    {baselineChecks.length > 0 && <div className="receipt-list">
      <h4>Baseline receipts</h4>
      {baselineChecks.map(check => <ReceiptRow key={check.label} {...check} />)}
      <p className="muted">Scope: {packet?.qualification_scope?.replaceAll('_', ' ') || 'recorded checks only'}.
        {packet?.autonomous_repair === false ? ' No autonomous repair is verified.' : ''}</p>
    </div>}
    {candidateChecks.length > 0 && <div className="receipt-list">
      <h4>Candidate receipts</h4>
      {candidateChecks.map(check => <ReceiptRow key={check.label} {...check} />)}
      {packet?.patch_hash && <small className="mono">Patch SHA-256: {packet.patch_hash}</small>}
      {packet?.actual_model_spend_usd && <small>Reported model spend: ${packet.actual_model_spend_usd}</small>}
    </div>}
    {packet?.media_manifest_urls && Object.keys(packet.media_manifest_urls).length > 0 ?
      <div className="receipt-list">
        <h4>Browser recordings</h4>
        {(['baseline', 'candidate'] as const).map(label => packet.media_manifest_urls?.[label] &&
          <div key={label}><p>{label === 'baseline' ? 'Before patch' : 'After patch'}</p>
            <Suspense fallback={<p className="muted">Loading player…</p>}>
              <MediaPlayer manifestUrl={packet.media_manifest_urls[label]!}
                grantPath={packet.media_manifest_urls[label]!.startsWith('/private-media/') ?
                  `/runs/${run.id}/recordings/${label}/grant` : undefined} />
            </Suspense>
            {['COMPLETED', 'INCONCLUSIVE', 'FAILED', 'CANCELLED', 'REVIEW_READY'].includes(run.state) &&
              <button type="button" className="secondary-button" disabled={busy} onClick={() => void deleteRecording(label)}>
                <Trash2 size={14} /> Delete recording
              </button>}
          </div>)}
      </div> : packet?.media_manifest_url ?
      <Suspense fallback={<p className="muted">Loading player…</p>}>
        <MediaPlayer manifestUrl={packet.media_manifest_url} markers={packet.evidence_timeline} />
      </Suspense> :
      <p className="muted">{baselineChecks.length || candidateChecks.length ? 'No playable recording is published for this run.' :
        'No test result, screenshot or recording is available yet.'}</p>}
    {packet?.deleted_recording_labels?.map(label =>
      <p className="muted" key={label}>{label === 'baseline' ? 'Before' : 'After'} recording deleted; run transcript retained.</p>)}
    {packet?.screenshot_urls && Object.keys(packet.screenshot_urls).length > 0 &&
      <div className="screenshot-grid">
        {(['baseline', 'candidate'] as const).map(label => packet.screenshot_urls?.[label] &&
          <div key={label}><h4>{label === 'baseline' ? 'Before patch screenshot' : 'After patch screenshot'}</h4>
            <a href={packet.screenshot_urls[label]} target="_blank" rel="noreferrer">
              <img src={packet.screenshot_urls[label]} alt={`${label} browser result`} loading="lazy" />
            </a>
          </div>)}
      </div>}
  </>
}
