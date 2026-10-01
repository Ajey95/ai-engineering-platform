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
Docker Desktop is optional for the Postgres, Memgraph, Redis and MinIO development
services; those services are not yet integrated with every code path.

```powershell
uv sync --extra dev
Copy-Item .env.example .env
docker compose up -d postgres
.venv\Scripts\uvicorn.exe platform_app.api:app --host 127.0.0.1 --port 8098
```

In another terminal:

```powershell
Set-Location apps\web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. The frontend proxy targets port 8098 by default.
For a no-Docker development smoke test, omit `.env` and the API uses a local
SQLite file. This mode is for synthetic data only.

The current controlled fixture packet is at
`artifacts/evaluation-v2/review-packet.json` and is intentionally ignored by
Git. The Evaluations page loads it only on loopback when development
authentication is disabled. The `platform_app.fixture_evaluation` command
recreates it from an explicit manifest, baseline, manually prepared candidate,
hidden oracle and artifact directory. Host execution is not a customer sandbox.

Run checks:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check platform_app tests
.venv\Scripts\python.exe scripts\verify_dev_evaluation_ui.py
Set-Location apps\web
npm run build
```

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
