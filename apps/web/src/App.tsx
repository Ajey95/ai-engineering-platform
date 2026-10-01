import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, BarChart3, BookOpen, CheckCircle2, ChevronDown, CircleHelp,
  ClipboardList, Code2, FolderGit2, Gauge, Layers3, Menu, Play, Plus,
  RefreshCw, Settings2, ShieldCheck, Square, XCircle,
} from 'lucide-react'
import { api, jsonBody, type CheckReceipt, type ModelEntry, type Project, type ReviewPacket, type Run, type RunEvent, type Task } from './api'
const MediaPlayer = lazy(() => import('./MediaPlayer'))

type Page = 'projects' | 'runs' | 'review' | 'memory' | 'evaluations' | 'usage' | 'settings'
type DevEvaluation = {
  case_id: string; qualification_scope: string; autonomous_repair: boolean
  candidate_origin: string; verdict: string
  baseline_tree_sha256: string; candidate_tree_sha256: string
  results: Record<'baseline' | 'candidate', {
    named_test: string; browser: string; oracle: string
    screenshot_url: string | null; media_manifest_url: string | null
  }>
}

const nav: { id: Page; label: string; icon: typeof FolderGit2 }[] = [
  { id: 'projects', label: 'Projects', icon: FolderGit2 },
  { id: 'runs', label: 'Runs', icon: Activity },
  { id: 'review', label: 'Review', icon: ClipboardList },
  { id: 'memory', label: 'Memory', icon: BookOpen },
  { id: 'evaluations', label: 'Evaluations', icon: Gauge },
  { id: 'usage', label: 'Usage', icon: BarChart3 },
  { id: 'settings', label: 'Settings', icon: Settings2 },
]

const shortId = (id: string) => id.slice(0, 8)
const date = (value: string) => new Date(value).toLocaleString()

function Status({ value }: { value: string }) {
  const style = ['FAILED', 'CANCELLED'].includes(value) ? 'bad'
    : ['INCONCLUSIVE', 'PAUSED_INPUT', 'PAUSED_BUDGET', 'FIXTURE ONLY'].includes(value) ? 'warn'
      : ['COMPLETED', 'PASSED', 'REVIEW_READY', 'QUALIFIED'].includes(value) ? 'good' : 'neutral'
  return <span className={`status ${style}`}>{value.replaceAll('_', ' ')}</span>
}

function Empty({ title, description }: { title: string; description: string }) {
  return <div className="empty"><Layers3 size={30} aria-hidden="true" /><h3>{title}</h3><p>{description}</p></div>
}

export default function App() {
  const [page, setPage] = useState<Page>('projects')
  const [projects, setProjects] = useState<Project[]>([])
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
  const [error, setError] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [memoryRevision, setMemoryRevision] = useState('')
  const [memoryQuery, setMemoryQuery] = useState('')
  const [memoryFacts, setMemoryFacts] = useState<{ id: string; subject: string; statement: string; source_refs: string[]; verification_scope: string | null }[]>([])
  const [devEvaluation, setDevEvaluation] = useState<DevEvaluation | null>(null)

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
    if (page !== 'evaluations') return
    let active = true
    void api<DevEvaluation>('/dev/evaluation').then(value => {
      if (active) setDevEvaluation(value)
    }).catch(() => { if (active) setDevEvaluation(null) })
    return () => { active = false }
  }, [page])

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
      } catch { /* malformed event is ignored; durable history remains source of truth */ }
    }
    for (const name of ['run.admitted', 'run.state_changed', 'model.started', 'model.completed', 'tool.authorized', 'tool.completed', 'verification.completed', 'artifact.ready', 'run.closed']) stream.addEventListener(name, receive)
    return () => { active = false; stream.close() }
  }, [selectedRun, refresh])

  const project = projects.find(item => item.id === selectedProject)
  const run = runs.find(item => item.id === selectedRun)
  const task = tasks.find(item => item.id === run?.task_id)
  const scopedRuns = selectedProject ? runs.filter(item => item.project_id === selectedProject) : runs
  const scopedTasks = selectedProject ? tasks.filter(item => item.project_id === selectedProject) : tasks
  const qualifiedModels = models.filter(item => item.qualified)
  const reservedTotal = useMemo(() => usage.entries.reduce((sum, entry) => sum + entry.reserved_usd, 0), [usage])
  const actualTotal = useMemo(() => usage.entries.reduce((sum, entry) => sum + entry.actual_usd, 0), [usage])

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
      const created = await api<Run>(`/tasks/${String(form.get('task_id'))}/runs`, {
        method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: jsonBody({ base_commit: String(form.get('base_commit')),
          selected_model_entry: String(form.get('model')),
          mode: 'investigate_and_propose', reproduction: { scenario: String(form.get('scenario')) } }),
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

  async function searchMemory(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError('')
    if (!selectedProject) return
    try {
      const params = new URLSearchParams({ source_revision: memoryRevision, query: memoryQuery })
      const result = await api<{ facts: typeof memoryFacts }>(`/projects/${selectedProject}/memory?${params}`)
      setMemoryFacts(result.facts)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Memory search failed') }
  }

  function navigate(target: Page) {
    setPage(target); setMenuOpen(false); setError('')
    if (target === 'review' && !selectedRun && runs[0]) setSelectedRun(runs[0].id)
  }

  return <div className="app-shell">
    <aside className={`sidebar ${menuOpen ? 'open' : ''}`}>
      <div className="brand"><span className="brand-mark"><Code2 size={18} /></span><span>Forge</span></div>
      <nav aria-label="Main navigation">
        {nav.map(item => <button key={item.id} type="button" className={`nav-item ${page === item.id ? 'active' : ''}`} onClick={() => navigate(item.id)}>
          <item.icon size={18} aria-hidden="true" /><span>{item.label}</span>
        </button>)}
      </nav>
      <div className="sidebar-footer"><span className="avatar">LD</span><div><strong>Local development</strong><small>Fixture identity</small></div></div>
    </aside>
    <div className="app-content">
      <header className="topbar">
        <button type="button" className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMenuOpen(!menuOpen)}><Menu size={20} /></button>
        <div className="project-picker"><FolderGit2 size={17} /><select aria-label="Selected project" value={selectedProject} onChange={event => setSelectedProject(event.target.value)}>
          {projects.length === 0 && <option value="">No project</option>}
          {projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><ChevronDown size={14} /></div>
        <span className="top-separator" />
        <div className="model-summary"><ShieldCheck size={17} /><span>{qualifiedModels.length ? `${qualifiedModels.length} enabled model${qualifiedModels.length > 1 ? 's' : ''}` : 'No qualified model'}</span></div>
        <div className="top-spacer" />
        {run && <><span className="run-chip">Run #{shortId(run.id)}</span><Status value={run.state} /></>}
        <span className="budget-summary">${actualTotal.toFixed(2)} actual · ${reservedTotal.toFixed(2)} reserved</span>
        <button type="button" className="icon-button" aria-label="Refresh workspace" onClick={() => void refresh()}><RefreshCw size={17} /></button>
      </header>
      <main>
        {error && <div className="error-banner" role="alert"><XCircle size={18} />{error}<button type="button" aria-label="Dismiss error" onClick={() => setError('')}>×</button></div>}
        {page === 'projects' && <section className="page-section">
          <div className="page-heading"><div><h1>Projects</h1><p>Connect an authorized repository and a reproducible test environment.</p></div><button className="primary-button" onClick={() => setDialog('project')}><Plus size={17} /> New project</button></div>
          {projects.length === 0 ? <Empty title="No projects yet" description="Create a project to register its repository and test URL." /> : <div className="project-list">{projects.map(item => <button key={item.id} className={`project-row ${selectedProject === item.id ? 'selected' : ''}`} onClick={() => setSelectedProject(item.id)}><FolderGit2 size={20} /><span><strong>{item.name}</strong><small>{item.repository_url || 'Repository not configured'}</small></span><span className="project-meta">{date(item.created_at)}</span></button>)}</div>}
          {project && <div className="content-panel"><div className="panel-heading"><h2>{project.name}</h2><button className="secondary-button" onClick={() => setDialog('task')}><Plus size={16} /> New report</button></div><div className="field-grid"><div><label>Repository</label><p>{project.repository_url || 'Not configured'}</p></div><div><label>Test URL</label><p>{project.test_url || 'Not configured'}</p></div></div><h3>Recent reports</h3>{scopedTasks.length ? scopedTasks.slice(0, 5).map(item => <div className="list-row" key={item.id}><span>{item.report}</span><small>{date(item.created_at)}</small></div>) : <p className="muted">No reports submitted.</p>}</div>}
        </section>}
        {page === 'runs' && <section className="page-section">
          <div className="page-heading"><div><h1>Runs</h1><p>Durable execution history and review evidence.</p></div><button className="primary-button" onClick={() => setDialog('run')} disabled={!scopedTasks.length || !qualifiedModels.length} title={!qualifiedModels.length ? 'No qualified model is enabled' : undefined}><Play size={16} /> Start run</button></div>
          {!qualifiedModels.length && <div className="notice"><CircleHelp size={18} /> Registering a model does not qualify it. Validate a provider account and run the conformance suite before execution.</div>}
          <div className="workspace-grid"><div className="run-list content-panel"><h2>History</h2>{scopedRuns.length ? scopedRuns.map(item => <button key={item.id} className={`run-row ${selectedRun === item.id ? 'selected' : ''}`} onClick={() => setSelectedRun(item.id)}><span><strong>Run #{shortId(item.id)}</strong><small>{date(item.created_at)}</small></span><Status value={item.state} /></button>) : <Empty title="No runs" description="Submit a report, then start a qualified run." />}</div><div className="run-detail">{run ? <RunWorkspace run={run} task={task} packet={packet} events={events} tab={tab} setTab={setTab} cancel={cancel} /> : <Empty title="Select a run" description="Its progress and evidence will appear here." />}</div></div>
        </section>}
        {page === 'review' && <section className="page-section"><div className="page-heading"><div><h1>Review packet</h1><p>Verification claims are linked to actual tool evidence.</p></div></div>{run ? <RunWorkspace run={run} task={task} packet={packet} events={events} tab={tab} setTab={setTab} cancel={cancel} /> : <Empty title="No run selected" description="Choose a run from the Runs screen." />}</section>}
        {page === 'usage' && <section className="page-section"><div className="page-heading"><div><h1>Usage</h1><p>Reservations and actual charges from the run ledger.</p></div></div><div className="summary-strip"><div><small>Reserved</small><strong>${reservedTotal.toFixed(2)}</strong></div><div><small>Actual</small><strong>${actualTotal.toFixed(2)}</strong></div><div><small>Ledger entries</small><strong>{usage.entries.length}</strong></div></div><div className="content-panel"><h2>Ledger</h2>{usage.entries.length ? usage.entries.map((entry, index) => <div className="list-row" key={`${entry.run_id}-${index}`}><span>Run #{shortId(entry.run_id)} · {entry.status}</span><strong>${entry.reserved_usd.toFixed(2)} reserved</strong></div>) : <Empty title="No usage" description="Charges will be recorded when qualified runs execute." />}</div></section>}
        {page === 'settings' && <section className="page-section"><div className="page-heading"><div><h1>Settings</h1><p>Model registry readiness in this local workspace.</p></div></div><div className="content-panel"><h2>Models</h2>{models.length ? models.map(model => <div className="list-row" key={model.id}><span>{model.provider} · {model.model_id}</span><Status value={model.qualified ? 'QUALIFIED' : model.fixture_only ? 'FIXTURE ONLY' : model.state.toUpperCase()} /></div>) : <Empty title="No model entries" description="Use the versioned model registry API to register a model. A live conformance check is required before enabling it." />}</div></section>}
        {page === 'memory' && <section className="page-section"><div className="page-heading"><div><h1>Memory</h1><p>Verified facts at an exact repository revision. Retrieval uses the canonical fallback.</p></div></div><form className="memory-search content-panel" onSubmit={searchMemory}><label>Commit SHA<input value={memoryRevision} onChange={event => setMemoryRevision(event.target.value)} pattern="[0-9a-fA-F]{40}" required placeholder="40-character Git commit SHA" /></label><label>Search text<input value={memoryQuery} onChange={event => setMemoryQuery(event.target.value)} placeholder="File, symbol or incident" /></label><button className="secondary-button" disabled={!selectedProject}>Search memory</button></form><div className="content-panel"><h2>Source-backed facts</h2>{memoryFacts.length ? memoryFacts.map(fact => <div className="memory-fact" key={fact.id}><strong>{fact.subject}</strong><p>{fact.statement}</p><small>{fact.verification_scope || 'Scope not recorded'} · {fact.source_refs.join(', ')}</small></div>) : <p className="muted">No verified facts loaded for this revision.</p>}</div></section>}
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
      {dialog === 'run' && <form onSubmit={submitRun}><label>Report<select name="task_id" required>{scopedTasks.map(item => <option key={item.id} value={item.id}>{item.report.slice(0, 80)}</option>)}</select></label><label>Pinned base commit<input name="base_commit" pattern="[0-9a-fA-F]{40}" required placeholder="40-character Git commit SHA" /></label><label>Qualified model<select name="model" required>{qualifiedModels.map(item => <option key={item.id} value={item.id}>{item.provider} · {item.model_id}</option>)}</select></label><label>Reproduction scenario<input name="scenario" placeholder="Describe the browser action" /></label><button className="primary-button" disabled={busy}>Admit run</button></form>}
    </div></div>}
  </div>
}

function RunWorkspace({ run, task, packet, events, tab, setTab, cancel }: {
  run: Run; task?: Task; packet: ReviewPacket | null; events: RunEvent[]
  tab: 'evidence' | 'changes' | 'logs' | 'environment'
  setTab: (tab: 'evidence' | 'changes' | 'logs' | 'environment') => void
  cancel: () => void
}) {
  return <div className="review-layout">
    <div className="review-left"><div className="review-header"><div><small>Run #{shortId(run.id)} · {date(run.created_at)}</small><h2>{task?.report || 'Loading report'}</h2></div><Status value={run.state} /></div>
      <div className="content-panel bug-report"><div className="section-title"><ClipboardList size={18} /><h3>Bug report</h3></div><p>{task?.report || 'Loading…'}</p><dl><dt>Expected</dt><dd>{task?.expected_behavior || '—'}</dd><dt>Actual</dt><dd>{task?.actual_behavior || '—'}</dd></dl></div>
      <div className="content-panel progress-panel"><div className="section-title"><Activity size={18} /><h3>Run progress</h3></div>{events.length ? <ol className="timeline">{events.map(event => <li key={event.event_id}><span className="timeline-node" /><div><strong>{event.event_type.replaceAll('.', ' · ')}</strong><small>{date(event.timestamp)}</small><p>{Object.entries(event.payload).map(([key, value]) => `${key}: ${String(value)}`).join(' · ')}</p></div></li>)}</ol> : <p className="muted">No durable events recorded yet.</p>}</div>
    </div>
    <div className="review-right"><div className="tabbar" role="tablist" aria-label="Run details">{(['evidence', 'changes', 'logs', 'environment'] as const).map(item => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item === 'changes' ? 'Changed files' : item[0].toUpperCase() + item.slice(1)}</button>)}</div>
      <div className="content-panel detail-panel">{tab === 'evidence' && <EvidencePanel packet={packet} run={run} />}
        {tab === 'changes' && <><div className="section-title"><Code2 size={18} /><h3>Changed files</h3></div>{packet?.changed_files.length ? packet.changed_files.map(file => <div className="list-row" key={file}>{file}</div>) : <p className="muted">No patch has been produced.</p>}{packet?.diagnosis_hypothesis && <p className="muted">Model hypothesis: {packet.diagnosis_hypothesis}</p>}{packet?.patch_url && <PatchViewer url={packet.patch_url} />}</>}
        {tab === 'logs' && <><div className="section-title"><Activity size={18} /><h3>Activity log</h3></div>{events.map(event => <div className="list-row" key={event.event_id}><span>{event.event_type}</span><small>{date(event.timestamp)}</small></div>)}</>}
        {tab === 'environment' && <><div className="section-title"><FolderGit2 size={18} /><h3>Pinned environment</h3></div><dl><dt>Base commit</dt><dd className="mono">{run.base_commit}</dd><dt>Model entry</dt><dd>{run.model_entry_id}</dd></dl></>}
      </div>
      <div className="content-panel verdict-panel"><div className="section-title"><CheckCircle2 size={18} /><h3>Review status</h3></div><p><Status value={run.verdict} /> {packet?.limitations.join(' ') || 'The verdict covers only recorded verification evidence.'}</p>{!['COMPLETED', 'FAILED', 'CANCELLED', 'INCONCLUSIVE'].includes(run.state) && <button className="secondary-button" onClick={cancel}><Square size={14} /> Cancel run</button>}</div>
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

function EvidencePanel({ packet, run }: { packet: ReviewPacket | null; run: Run }) {
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
              <MediaPlayer manifestUrl={packet.media_manifest_urls[label]!} />
            </Suspense>
          </div>)}
      </div> : packet?.media_manifest_url ?
      <Suspense fallback={<p className="muted">Loading player…</p>}>
        <MediaPlayer manifestUrl={packet.media_manifest_url} markers={packet.evidence_timeline} />
      </Suspense> :
      <p className="muted">{baselineChecks.length || candidateChecks.length ? 'No playable recording is published for this run.' :
        'No test result, screenshot or recording is available yet.'}</p>}
  </>
}
