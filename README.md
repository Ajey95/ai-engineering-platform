# AI Engineering Platform

This repository implements an early **local development foundation** for the
[AI Engineering Platform PRD](E:/vab-downloads/AI_Engineering_Platform_PRD.md),
version 1.0. It is **not a complete implementation or a paid pilot release**.
See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for requirement evidence
and release blockers.

## What runs today

- FastAPI project, task, model registry, run, event, cancellation, review packet,
  usage and scoped canonical memory endpoints.
- Transactional run admission with an idempotency key, a budget reservation,
  a durable event and a dispatch outbox row.
- Fenced lease, state transition and tool effect ledger primitives.
- JSON schema tool-call assembler that discards incomplete arguments.
- Token envelope and spend preflight calculations.
- Local FFmpeg HLS encoding with staging and immutable publication.
- A controlled synthetic form bug evaluation: real browser before/after
  recordings, exact-tree test receipts, an independent oracle, a review packet,
  and local HLS playback on the Evaluations page. The candidate is manual.
- Native non-streaming OpenAI, Anthropic and Google API adapters with provider
  continuation preservation and encrypted state envelopes. No live provider
  account has been qualified.
- React workspace with project onboarding, report submission, run history,
  review and usage views. It reads real API records; no fake run evidence is shown.

No model is automatically qualified or enabled. The UI will correctly refuse
to start a run until live provider conformance and sandbox execution exist.
Outside development, startup fails closed because OIDC roles and production
isolation are not yet implemented.

## Local setup

Requirements: Python 3.12, `uv`, Node 22.12 or newer, npm, and FFmpeg for media tests.
Docker Engine is required for the isolated synthetic fixture path. This machine
uses Docker Engine in Ubuntu-24.04 WSL because Docker Desktop 4.67.0 fails at
its `dockerInference` socket during startup. Docker Desktop's data was not reset.
The WSL Engine has its own image and volume store.

```powershell
uv sync --extra dev
Copy-Item .env.example .env
.\scripts\start_wsl_docker.ps1
wsl -d Ubuntu-24.04 -u root -- bash -lc 'cd /mnt/d/projects/aiplatform && docker compose up -d postgres'
.venv\Scripts\uvicorn.exe platform_app.api:app --host 127.0.0.1 --port 8098
```

In another terminal:

```powershell
Set-Location apps\web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The frontend proxy targets port 8098 by default.
For a SQLite development smoke test, omit `.env` and the API uses a local
SQLite file. This mode is for synthetic data only.

The current controlled fixture packet is at
`artifacts/evaluation-v2/review-packet.json` and is intentionally ignored by
Git. The Evaluations page loads it only on loopback when development
authentication is disabled. The `platform_app.fixture_evaluation` command
recreates it from an explicit manifest, baseline, manually prepared candidate,
hidden oracle and artifact directory. In WSL mode, named tests, browser actions
and the hidden oracle run in separate development containers. The oracle is
mounted only for its verifier invocation. These containers are for synthetic
fixtures; they are not the hosted customer VM boundary.

For this workspace on Windows, install and start the WSL Engine, then build the
development image:

```powershell
wsl -d Ubuntu-24.04 -u root -- bash /mnt/d/projects/aiplatform/scripts/install_wsl_docker.sh
wsl -d Ubuntu-24.04 -u root -- bash -lc 'cd /mnt/d/projects/aiplatform && docker build -f infra/dev-sandbox/Dockerfile -t aip-dev-sandbox:0.1.0 .'
```

Run the complete controlled fixture evaluation in containers:

```powershell
python -m platform_app.fixture_evaluation --manifest benchmarks/fixtures/form-submit/manifest.json --baseline benchmarks/fixtures/form-submit/base --candidate artifacts/form-submit-candidate-v2/workspace --oracle benchmarks/oracles/form-submit-001/test_hidden.py --artifacts artifacts/evaluation-v2 --candidate-origin manual --browser-runtime wsl
```

The candidate workspace is a manually prepared, ignored local artifact. The
verdict proves only this fixture's exercised behavior; no model generated the
patch. The Windows `docker` CLI still targets the broken Docker Desktop pipe;
use `wsl -d Ubuntu-24.04 -u root -- docker ...` for this project.

WSL systemd services do not keep the distribution alive after the last user
process exits. A hidden `wsl.exe ... sleep infinity` process is currently
keeping this development Engine available; it is a local process, not a
scheduled task. After a reboot, run `./scripts/start_wsl_docker.ps1` before
starting the Compose services. Closing that process lets WSL idle-stop and
gracefully stop containers.

PostgreSQL migrations require an explicit `AIP_DATABASE_URL`. For the local
Compose database:

```powershell
$env:AIP_DATABASE_URL='postgresql+psycopg://aip:local_only@127.0.0.1:54329/aip'
uv run alembic upgrade head
uv run alembic check
python -m scripts.verify_postgres_admission
python -m scripts.verify_dev_container_security
```

The admission verification creates and drops a unique test database. Its
enabled model row is only a database fixture and never invokes a provider.

Run checks:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check platform_app tests scripts
Set-Location apps\web
npm run build
```

With the API, frontend and local evaluation packet available, run
`.venv\Scripts\python.exe scripts\verify_dev_evaluation_ui.py` from the
repository root to check browser playback on desktop and mobile.

## Security boundary

The local `AIP_DEV_ACTOR` and `AIP_DEV_TENANT` are development fixtures. They are
not authentication. Never expose the current API to the Internet or supply
customer repository credentials. Hosted deployment requires OIDC membership
checks, per-run VM sandboxing, scoped repository credentials, migrations,
private artifact delivery, and all release gates in the PRD.

## Source and status

The source PRD is external to this repository and is treated as product input,
not as an instruction to weaken authorization or invent evidence. The code
does not edit that PRD. The full requirement and acceptance status is tracked
in `IMPLEMENTATION_STATUS.md`.
