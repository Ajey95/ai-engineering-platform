# AI Engineering Platform

<div align="center">

### From a bug report to a reviewable, evidence-backed repair

**FastAPI · PostgreSQL · LangGraph · Playwright · React · FFmpeg · Memgraph**

`Pinned source` · `Durable runs` · `Bounded model calls` · `Independent verification` · `Private evidence`

</div>

The platform turns a reported application failure into a reproducible investigation. It records the exact source revision, executes checks in an isolated workspace, collects browser evidence, evaluates a bounded candidate patch, and gives a reviewer the diff and test receipts. A result can also be **inconclusive** when the evidence does not justify a repair claim.

This README describes implemented code paths and the way they have been exercised locally. The [project checkpoint](PROJECT_MEMORY.md) tracks completion and the next work session; the [requirement ledger](IMPLEMENTATION_STATUS.md) contains detailed PRD traceability.

## The use case

Consider a report that a form does not submit correctly. A developer needs more than a suggested line of code: they need the failing behavior at a known commit, the evidence behind a proposed change, and a repeatable check of the patched tree.

| Step | What the platform records | What the reviewer receives |
|---|---|---|
| Report | Project, expected and actual behavior, reproduction data, pinned Git commit | A task with a stable source identity |
| Reproduce | Named test, browser scenario, screenshot, recording, sanitized output and hidden oracle receipt | A baseline result with linked artifacts |
| Investigate | Scoped source excerpts, code symbols, verified project memory and bounded evidence | A traceable context bundle |
| Patch | Exact-tree candidate workspace, changed files and patch digest | A unified diff tied to the source commit |
| Verify | Separate named, browser and oracle checks against the candidate | Before/after results and recordings |
| Review | Run events, verdict, patch, media, evidence ZIP and decision history | One review packet that preserves the test outcome |

The included `form-submit-001` fixture exercises this flow. A normal local run reproduces the defect and reports `INCONCLUSIVE` when no model is qualified. A controlled provider response can drive the candidate path to `REVIEW_READY/PASSED`; that mode verifies the protocol with a predetermined response.

## Backend architecture

### 1. Request, execution and evidence planes

```mermaid
flowchart LR
    UI["React workspace"] -->|REST + SSE| API["FastAPI control plane"]
    API -->|atomic admission| DB[(PostgreSQL)]
    DB -->|dispatch row| WORKER["Development worker<br/>fenced lease"]
    WORKER --> GRAPH["LangGraph<br/>checkpointed phases"]
    GRAPH --> CONTEXT["Context bundle<br/>pinned code + verified memory"]
    GRAPH --> MODEL["Model adapter<br/>reservation + usage ledger"]
    GRAPH --> POLICY["Tool policy<br/>effect intent + receipt"]
    POLICY --> BOX["Isolated WSL containers<br/>tests · browser · oracle"]
    BOX --> EVIDENCE["Hashed artifacts<br/>logs · PNG · WebM"]
    EVIDENCE --> MEDIA["FFmpeg HLS encoder"]
    MEDIA --> PLAYER["Scoped playback"]
    DB -->|scoped projection| MEM[(Memgraph)]
    MEM --> CONTEXT
    DB -->|committed events| API
    API -->|SSE replay| UI
    EVIDENCE --> API
    PLAYER --> UI
```

PostgreSQL owns run state and provenance. The graph is a scoped retrieval projection. The queue or outbox delivers work; a database lease and fencing token decide which worker may change a run. Media completion is tracked separately from code verification.

### 2. Durable run lifecycle

```mermaid
sequenceDiagram
    actor User
    participant API as FastAPI
    participant DB as PostgreSQL
    participant Worker as Worker + LangGraph
    participant Box as Isolated containers
    participant Review as Review UI
    User->>API: Submit report and admit run
    API->>DB: Commit run + snapshot + budget + event + outbox
    DB-->>Worker: Dispatch eligible run
    Worker->>DB: Claim fenced lease and checkpoint phase
    Worker->>Box: Run pinned baseline checks
    Box-->>Worker: Receipt + hashed evidence
    Worker->>DB: Persist effect receipt and events
    Worker->>Box: Execute patched candidate checks
    Box-->>Worker: Independent candidate receipts
    Worker->>DB: Persist verification and review state
    DB-->>API: Ordered events and packet
    API-->>Review: Replay events and serve packet
    Review-->>User: Diff, tests, screenshots and video
```

The workflow is `PREPARING → REPRODUCING → INVESTIGATING → PATCHING → VERIFYING → REVIEW_READY`. It also records controlled pauses, cancellation, failure and inconclusive outcomes. A resumed run restores its saved stage before LangGraph continues from its durable PostgreSQL checkpoint. Completed effects are checked against their stored receipts; an uncertain effect stops replay for reconciliation.

### 3. Why a verdict is traceable

```mermaid
flowchart TD
    COMMIT["Pinned Git commit"] --> BASE["Baseline workspace"]
    COMMIT --> PATCH["Patch + exact-tree digest"]
    BASE --> BT["Baseline test and browser receipts"]
    PATCH --> CAND["Candidate workspace"]
    CAND --> CT["Candidate test and browser receipts"]
    BT --> PACKET["Review packet"]
    CT --> PACKET
    PATCH --> PACKET
    PACKET --> DECISION["Human accept or reject decision"]
    DECISION --> MEMORY["Scoped decision memory"]
```

The reviewer decision is stored separately from the independent verification verdict. Accepting a packet does not change a failed test into a pass.

## Implemented backend concepts

| Concept | Implementation |
|---|---|
| **Transactional admission** | An idempotency key binds task/run creation to a frozen commit, policy and model snapshot. The run, initial event, spend reservation and dispatch outbox are committed together. |
| **Tenant and project isolation** | Scoped database constraints, membership and project roles protect runs, evidence, usage and memory. OIDC bearer validation and browser PKCE sessions have controlled issuer tests. |
| **Durable orchestration** | LangGraph owns fixture phases; PostgreSQL stores checkpoints. Workers claim fenced leases, heartbeat, persist effect intents and receipts, and recover safe dispatches. |
| **Model gateway** | Native OpenAI, Anthropic and Google adapters handle complete JSON and bounded streaming responses. The registry pins capabilities, revisions and prices; model calls reserve liability before HTTP and settle reported usage afterward. |
| **Routing controls** | Tenant policy allowlists model/data-class combinations. An exact cross-provider alternate can be approved for a definite rate-limit rejection, with a separate lineage and reservation. |
| **Context and token management** | Pinned source excerpts, code symbols, current verified memory and tool evidence become a hashed ContextBundle. Conservative budgets, head/tail log excerpts and content-addressed compaction preserve provenance. |
| **Tool authorization** | Versioned action contracts and plugin manifests define schemas, permissions, targets and limits. The broker records denials and typed results; completed log artifacts carry byte counts and SHA-256 digests. |
| **Repository and execution boundary** | Git archives an exact commit. The development runner executes named checks, browser actions and a hidden oracle in separate bounded WSL containers with non-root, read-only and network restrictions. |
| **Candidate verification** | Patch materialization records changed files and a tree digest. Candidate checks rerun independently, with before/after screenshots, WebM, HLS and a review packet built from persisted receipts. |
| **Project memory** | PostgreSQL owns fact states and transition history. An outbox projects verified facts and revision-pinned file/symbol dependencies to Memgraph; reads recheck canonical scope and fall back to PostgreSQL. |
| **Media and deletion** | FFmpeg creates digest-checked HLS variants. Tenant-scoped local routes serve recordings; deletion revokes access, removes raw/HLS files and preserves the run's non-video evidence. Private S3 publication, signed grants and remote deletion have controlled client tests. |
| **Hosted integration modules** | The SQS dispatcher, run coordinator, per-run EC2 intent, scoped S3 guest transport and separate baseline/candidate guest phases have controlled-client integration tests. Terraform and Packer define the corresponding trusted and guest resources. |
| **Repository publication** | Project-scoped GitHub connection records, a read-only qualification command, explicit draft-PR approval, durable publication dispatch and deterministic branch reconciliation have controlled API tests. |
| **Operations and cost** | Tenant inference, concurrency, export-byte, sandbox-minute, media-minute and private-HLS-byte limits have ledgers and owner controls. Operations exposes tenant-scoped run, queue, graph, media, tool and budget signals. |
| **Audit and telemetry** | Durable run events, audit rows and OpenTelemetry spans follow admission, dispatch, tools, model calls and media work. A local OTLP receiver accepted an emitted protobuf span. |
| **Evaluation** | Forty pinned synthetic cases have hidden oracles and a strict manifest validator. A scorer distinguishes attempts, failures and false-success claims. |

### Backend module map

| Responsibility | Main source |
|---|---|
| API, authorization and sessions | [`platform_app/api.py`](platform_app/api.py), [`auth.py`](platform_app/auth.py), [`browser_auth.py`](platform_app/browser_auth.py) |
| Admission, lifecycle and durable events | [`service.py`](platform_app/service.py), [`run_ledger.py`](platform_app/run_ledger.py), [`models.py`](platform_app/models.py) |
| Agent workflow and local execution | [`fixture_workflow.py`](platform_app/fixture_workflow.py), [`development_worker.py`](platform_app/development_worker.py), [`dev_sandbox.py`](platform_app/dev_sandbox.py) |
| Provider, context and spending | [`providers.py`](platform_app/providers.py), [`context_bundle.py`](platform_app/context_bundle.py), [`model_budget.py`](platform_app/model_budget.py) |
| Memory and code navigation | [`memory.py`](platform_app/memory.py), [`graph_memory.py`](platform_app/graph_memory.py), [`code_index.py`](platform_app/code_index.py) |
| Evidence, media and review | [`evidence_bundle.py`](platform_app/evidence_bundle.py), [`media.py`](platform_app/media.py), [`review_patch.py`](platform_app/review_patch.py) |
| Integration and infrastructure definitions | [`platform_app/`](platform_app), [`infra/`](infra), [`.github/workflows/quality.yml`](.github/workflows/quality.yml) |

## Workspace and API

The React workspace contains Projects, Reports, Runs, Review, Evaluations, Memory, Usage, Settings and Operations views. It reads real API records and uses the durable event stream to update a run. Review exposes the diff, tests, screenshots, evidence download and HLS player. Project, report and run dialogs manage keyboard focus and restore it on close.

| API area | Representative routes |
|---|---|
| Projects and reports | `GET/POST /v1/projects`, `GET/POST /v1/tasks` |
| Runs | `POST /v1/tasks/{task_id}/runs`, `GET /v1/runs/{run_id}`, `POST /v1/runs/{run_id}/cancel` |
| Recovery and review | `POST /v1/runs/{run_id}/resume`, `/resume-budget`, `/review-decision` |
| Progress | `GET /v1/runs/{run_id}/events`, `/events/history` |
| Evidence | `GET /v1/runs/{run_id}/review-packet`, `/patch`, `/evidence-bundle` |
| Memory and operations | `GET /v1/projects/{project_id}/memory`, `/code-index`, `GET /v1/operations/summary` |
| Service checks | `GET /v1/health`, `GET /v1/ready` |

FastAPI exposes the complete route schema at `/docs` in local development.

## Run the local workspace

This is the exercised Windows/WSL development path. Use Python 3.12, `uv`, Node 22.12 or newer, npm, FFmpeg, Ubuntu-24.04 WSL and Docker Engine in that distribution. Run commands from the repository root unless a step changes directory.

### 1. Prepare dependencies and data

```powershell
uv sync --extra dev
Copy-Item .env.example .env
.\scripts\start_wsl_docker.ps1
.\scripts\compose-wsl.ps1 up -d postgres memgraph
.venv\Scripts\python.exe -m alembic upgrade head
wsl.exe -d Ubuntu-24.04 -u root -- docker build -f infra/dev-sandbox/Dockerfile -t aip-dev-sandbox:0.1.0 .
wsl.exe -d Ubuntu-24.04 -u root -- docker build -f infra/dev-sandbox/Dockerfile.code-only -t aip-dev-sandbox:0.1.3 .
.venv\Scripts\python.exe -m scripts.seed_local_demo
```

`Dockerfile.code-only` refreshes the platform code over the full local browser image. Once the base exists, later code refreshes can run that second build alone.

### 2. Start the three processes

Open separate PowerShell terminals at the repository root:

```powershell
.venv\Scripts\uvicorn.exe platform_app.api:app --host 127.0.0.1 --port 8098
```

```powershell
.venv\Scripts\python.exe -m platform_app.development_worker --serve --runtime wsl --image aip-dev-sandbox:0.1.3
```

```powershell
Set-Location apps\web
npm ci
npm run dev
```

Open **http://127.0.0.1:5173/**. The Vite proxy connects the browser to the API on port 8098. The seeded project offers the pinned synthetic form case; it cannot be mistaken for a customer repository.

## Reproduce the backend checks

### Controlled PostgreSQL workflow

Each command creates, migrates and drops a uniquely named local PostgreSQL database. It runs the real isolated fixture checks with a predetermined provider response and writes a review packet under `artifacts/worker-verification/`.

```powershell
.\scripts\verify_local_postgres_worker.ps1
.\scripts\verify_local_postgres_worker.ps1 -ResumeProbe
.\scripts\verify_local_postgres_worker.ps1 -BudgetPauseProbe
```

The budget probe confirms that the first attempt pauses before provider HTTP, approval requeues the run, and the resumed attempt makes exactly one controlled provider request. The input probe confirms the paused run resumes with its stored receipts. Both reach `REVIEW_READY/PASSED` for the synthetic fixture.

### Regression and frontend checks

```powershell
$env:AIP_TEST_POSTGRES_URL='postgresql://aip:local_only@127.0.0.1:54329/aip'
$env:AIP_TEST_MEMGRAPH_URI='bolt://127.0.0.1:7687'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check platform_app tests scripts
.venv\Scripts\python.exe -m scripts.validate_benchmark --manifest benchmarks/suite-v1.json
Set-Location apps\web
npm run build
```

The latest PostgreSQL/Memgraph-enabled local Python run passed **295 tests** with **2 Windows symlink skips**. Ruff passed. The 40 pinned benchmark baseline/reference pairs and the frontend production build passed locally. [GitHub quality run 37113492325](https://github.com/Ajey95/ai-engineering-platform/actions/runs/37113492325) passed Python (293 tests, 4 Linux skips), PostgreSQL, web and the controlled sandbox repair/media jobs.

## Repository guide

```text
apps/web/                  React and TypeScript workspace
platform_app/              API, workflow, policy, model, memory and media modules
migrations/                PostgreSQL schema migrations
benchmarks/                Pinned synthetic cases, manifests and hidden oracles
infra/dev-sandbox/         Isolated local fixture image
infra/control-plane/       Trusted API and worker image definition
infra/media-worker/        Separate media image definition
infra/terraform/           Network, queue, data, control and edge definitions
scripts/                   Operators, dispatchers and verification probes
tests/                     Unit, integration and controlled external-client checks
```

The [project checkpoint](PROJECT_MEMORY.md) gives the verified state and exact continuation order. The [implementation ledger](IMPLEMENTATION_STATUS.md) maps code and evidence to PRD requirements.
