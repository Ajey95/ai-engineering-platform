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
- Tenant daily/monthly inference caps and concurrent run admission limits,
  serialized on the tenant row in PostgreSQL. A crossing of 80 percent emits
  a run budget warning.
- Fenced lease, state transition and tool effect ledger primitives.
- A deterministic development effect policy checks tenant/project/run scope,
  reviewed action and version, target and tool budget before execution.
- OpenTelemetry spans cover the API, admission, durable dispatch, worker,
  context lookup, tool policy, model calls and media work. Export requires
  `AIP_OTLP_TRACES_ENDPOINT`.
- JSON schema tool-call assembler that discards incomplete arguments.
- Token envelope and spend preflight calculations.
- A bounded, source-backed ContextBundle for the synthetic repair call, with
  commit/file hashes, scoped evidence and explicit trust labels.
- Synthetic test and oracle output streams to artifact files with byte counts
  and digests; the repair context includes bounded, verified excerpts and
  references paired to completed tool action IDs.
- Local FFmpeg HLS encoding with staging and immutable publication.
- A per-recording delete route and Review control for closed runs. It revokes
  local media access, removes that side's raw WebM and HLS files, and retains
  the run transcript and screenshot with a durable deletion record.
- A controlled synthetic form bug evaluation: real browser before/after
  recordings, exact-tree test receipts, an independent oracle, a review packet,
  and local HLS playback on the Evaluations page. The candidate is manual.
- Native non-streaming OpenAI, Anthropic and Google API adapters with provider
  continuation preservation and encrypted state envelopes. No live provider
  account has been qualified.
- Operator commands register model entries as unqualified and probe a chosen
  provider account before enabling an entry. No account has been probed here.
- React workspace with project onboarding, report submission, run history,
  review and usage views. It reads real API records; no fake run evidence is shown.

No provider model is automatically qualified or enabled. The reviewed local
fixture model is a separate synthetic-only exception; the UI labels it and
cannot use it for customer repositories.
Hosted API identity can start with an OIDC issuer, audience, JWKS URL and
database memberships, but customer run admission remains disabled until the
production sandbox is qualified. Hosted frontend login and private media
delivery are not implemented.

## Local setup

Requirements: Python 3.12, `uv`, Node 22.12 or newer, npm, and FFmpeg for media tests.
Docker Engine is required for the isolated synthetic fixture path. This machine
uses Docker Engine in Ubuntu-24.04 WSL because Docker Desktop 4.67.0 fails at
its `dockerInference` socket during startup. Docker Desktop's data was not reset.
The WSL Engine has its own image and volume store.

For an existing local SQLite file created before paused-run or tenant quota
columns were added, back it up and apply the additive development upgrade once:

```powershell
uv run python -m scripts.upgrade_local_sqlite
```

To make the local UI runnable without provider credentials, seed the reviewed
synthetic form case and keep its development worker polling the durable outbox:

```powershell
uv run python -m scripts.seed_local_demo
uv run python -m platform_app.development_worker --serve --runtime wsl
```

The Runs form labels this as a synthetic fixture and fills the pinned local
commit. It can reproduce the bug and publish baseline evidence. With no live
qualified provider, its honest result is `INCONCLUSIVE`; it does not make an
autonomous repair or execute a customer repository. The seed and SQLite upgrade
commands are idempotent.

After PostgreSQL migrations, a trusted operator can set tenant quotas. The
command audits changes and does nothing on an identical retry:

```powershell
$env:AIP_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@HOST:PORT/DATABASE'
uv run python -m scripts.set_tenant_quotas --tenant-id TENANT_ID --daily-inference-cap-usd 50 --monthly-inference-cap-usd 500 --max-concurrent-runs 4
```

The shown amounts are default examples, not a provider budget qualification.
Inference caps count reserved upper-bound liability until usage is settled;
daily and monthly periods use UTC. Provider usage above its reservation posts a
breach event and blocks subsequent reservations once the cap is spent. Other
PRD resource quotas remain pending.

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

For the current verified local session, use `http://127.0.0.1:5176/`. Its API
is on port 8101, backed by local PostgreSQL, and the polling development worker
uses the WSL Docker Engine with code-only image `aip-dev-sandbox:0.1.1`.
The older 5173–5175 sessions may serve earlier
code or a separate SQLite database. The Review screen can
delete one closed run's baseline or candidate recording independently of the
run transcript. `DELETE /v1/runs/{run_id}/recordings/{label}` is idempotent;
`GET /v1/runs/{run_id}/review-packet` reports `deleted_recording_labels`.
On development API startup, deletion records are reapplied to local files so
restored recordings remain inaccessible and are cleaned again. Hosted
object/CDN propagation, independent backup-ledger replay and retention
automation still need implementation and qualification.

The current controlled fixture packet is at
`artifacts/evaluation-v2/review-packet.json` and is intentionally ignored by
Git. The Evaluations page loads it only on loopback when development
authentication is disabled. The `platform_app.fixture_evaluation` command
recreates it from an explicit manifest, baseline, manually prepared candidate,
hidden oracle and artifact directory. In WSL mode, named tests, browser actions
and the hidden oracle run in separate development containers. The oracle is
mounted only for its verifier invocation. These containers are for synthetic
fixtures; they are not the hosted customer VM boundary.

The repository includes `.github/workflows/quality.yml` for push/PR Python,
web, PostgreSQL and controlled sandbox gates. It has not run on GitHub because
this checkout has no remote. The sandbox verifier accepts `--runtime native`
for an unprivileged Linux runner and `--image` for a local code-only refresh.
On this Windows host, check C: space before rebuilding the full browser image.
When the base image already exists, a small development refresh can be built
without redownloading browser dependencies:

```powershell
wsl -d Ubuntu-24.04 -u root -- bash -lc 'cd /mnt/d/projects/aiplatform && docker build -f infra/dev-sandbox/Dockerfile.code-only -t aip-dev-sandbox:0.1.1 .'
uv run python -m scripts.verify_development_worker --controlled-provider --image aip-dev-sandbox:0.1.1
```

The code-only image inherits the locally installed base image; CI uses the
reproducible full Dockerfile.

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

The PostgreSQL verifier creates and drops a unique test database. It checks
duplicate admission, resume, tenant constraints, migration roundtrip and
simultaneous recording deletion with one final event. Its
enabled model row is only a database fixture and never invokes a provider.
General admission requires a model with a live qualification marker and
validation time. The disposable fixture model is accepted only for the reviewed
development case and project; this marker does not make the provider qualified.

For a hosted control plane, set `AIP_ENVIRONMENT=production`, a migrated
PostgreSQL URL, `AIP_OIDC_ISSUER`, `AIP_OIDC_AUDIENCE`, and `AIP_OIDC_JWKS_URL`.
The API accepts an RS256 bearer token with verified issuer, audience and
expiration, plus `X-Tenant-ID`. That header selects only a tenant where the
verified token subject has an active membership. A trusted operator creates
the first owner after migration:

```powershell
.venv\Scripts\python.exe -m scripts.bootstrap_tenant --tenant-id <tenant-id> --tenant-name <name> --owner-subject <verified-oidc-subject>
```

The owner can manage tenant and project memberships through `/v1/memberships`
and `/v1/projects/{id}/members`. Project reads and mutations check roles;
the last active owner cannot be disabled. The hosted API rejects run admission
with `EXECUTION_UNAVAILABLE` until a customer sandbox is implemented. The
bootstrap command is an operator action; `--owner-subject` must come from a
verified identity. This local implementation has not been tested against a
real issuer or deployed identity provider.

## Provider model qualification

An operator can register an exact provider/model/revision with
`python -m scripts.register_model --definition <model.json> --operator <identity>`.
The definition uses the `ModelRegister` fields in `platform_app/schemas.py`:
provider, model ID, registry revision, context and output limits, price revision
and input/output prices per million tokens. Declared capabilities remain
untrusted and the new entry stays `registered`.

Create a separate JSON attestation with matching `registry_revision`,
`context_limit`, `output_limit`, `price_revision`, `price_per_m_input` and
`price_per_m_output`, plus `limits_source_url`, `pricing_source_url` and an
ISO `effective_date`. A trusted operator must check those sources and values;
the command records their URLs but does not independently verify their content.
Set the matching `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY` in the
operator environment or secret manager, then run:

```powershell
python -m scripts.qualify_model --model-entry-id <entry-id> --attestation <metadata.json> --operator <identity> --enable
```

The command makes three small native API requests to verify text, a schema
checked tool call, continuation and usage. It records a resolved model,
revision, adapter digest and model registry lifecycle events. A failed probe
leaves the entry unavailable; changed model metadata or adapter code invalidates
qualification. These commands have only been tested with controlled responses.
No provider account or model has been qualified live on this machine.

To export traces, set `AIP_OTLP_TRACES_ENDPOINT` to the OTLP HTTP traces URL
of a collector. Hosted endpoints must use HTTPS; local development may use
HTTP on loopback. The API and worker propagate W3C trace context through the
durable dispatch outbox. Trace attributes contain identifiers and outcomes,
not prompts, repository content or credentials. No collector or hosted trace
backend has been qualified here.

To exercise the admitted-run worker against the trusted synthetic baseline:

```powershell
.venv\Scripts\python.exe -m scripts.verify_development_worker
```

This creates a disposable SQLite run database under `artifacts/worker-verification`,
consumes its `run.dispatch` outbox row, claims a fenced lease, extracts the
fixture and hidden oracle from the pinned Git commit, and executes named,
browser and oracle checks in separate WSL Docker containers. The script prints
the run ID and artifact directory. Its review packet comes from persisted tool
receipts and reports `REPRODUCED` for the baseline and `INCONCLUSIVE` for the
run. The database-only model entry is a fixture; no provider call or autonomous
patch occurs. This development worker accepts only `form-submit-001` and
publishes a local HLS recording of the browser baseline.

To exercise the budgeted patch and candidate verification protocol, run
`.venv\Scripts\python.exe -m scripts.verify_development_worker --controlled-provider`.
This supplies a predetermined HTTP response to the native OpenAI adapter in a
disposable database. It verifies a model-call reservation/usage receipt, a
bounded patch, separate candidate named/browser/hidden-oracle containers, and
before/after HLS publication. It does not qualify an OpenAI account or prove
autonomous repair. The script prints a review packet and run ID.
For a run in the API's configured artifact directory, the Changes tab loads a
tenant-scoped unified diff and offers a patch download. The server reconstructs
the pinned fixture base and rejects a candidate whose tree or patch hash no
longer matches its receipt.
The Evidence tab also shows before/after PNG screenshots from admitted browser
runs. Their SHA-256 digests are checked against persisted browser receipts
each time the local tenant-scoped route serves them.
For a local `PAUSED_INPUT` run, the Runs screen accepts a bounded answer and
posts it to `/v1/runs/{id}/resume` with an `Idempotency-Key`. The API checks
current project membership, unchanged policy revision and unresolved effects
before it records the answer and requeues one dispatch. The fixture worker
replays completed effects against their receipts. Approval and budget pauses
do not have a resume path yet; hosted resume remains disabled with the hosted
sandbox gate.
For a `REVIEW_READY` fixture run, the reviewer can accept or reject the packet
in the Runs or Review screen. Rejection requires a reason. Both decisions are
audited, close the run, and leave the test verdict intact. Accepting a packet
does not authorize or create a draft PR.
On a later invocation, its local outbox recovery requeues expired dispatches
whose completed effects have intact receipts. An uncertain `INTENDED` effect
stops as inconclusive and requires reconciliation; it is never rerun blindly.

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
customer repository credentials. Hosted deployment still requires a qualified
OIDC provider, per-run VM sandboxing, scoped repository credentials, private
artifact delivery, and all release gates in the PRD.

## Source and status

The source PRD is external to this repository and is treated as product input,
not as an instruction to weaken authorization or invent evidence. The code
does not edit that PRD. The full requirement and acceptance status is tracked
in `IMPLEMENTATION_STATUS.md`.
