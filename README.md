# AI Engineering Platform

This repository implements an early **local development foundation** for the
[AI Engineering Platform PRD](E:/vab-downloads/AI_Engineering_Platform_PRD.md),
version 1.0. It is **not a complete implementation or a paid pilot release**.
See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for requirement evidence
and release blockers.
The [benchmark contract](benchmarks/README.md) rejects incomplete or duplicate
case suites; no complete benchmark has run yet.

## What runs today

- FastAPI project, task, model registry, run, event, cancellation, review packet,
  usage and scoped canonical memory endpoints.
- An owner-only Operations page reports tenant-scoped persisted run, queue,
  graph, tool, media and inference budget snapshots. Missing instrumentation is
  shown as unavailable. A separate evaluator persists sustained queue warnings,
  graph lag and budget-breach alerts; start it with
  `python -m scripts.evaluate_operations --serve --poll-seconds 30`.
  [Operations runbooks](docs/operations/runbooks.md) and a machine-readable
  owner/impact catalog cover the required incident classes. A separate signed
  webhook dispatcher can deliver durable alert transitions with retries when
  `AIP_PAGER_WEBHOOK_URL` and `AIP_PAGER_WEBHOOK_SECRET` are configured; no live
  paging destination has been qualified.
- Canonical memory supports proposed, verified, rejected, superseded, expired
  and deleted states. Scoped transition history records the actor and reason;
  retrieval only serves current verified facts. New reviewer outcomes create
  decision records linked to their exact run events and scoped as human review
  decisions, not independent proof of repair correctness.
- Project scoped GitHub connection registration with opaque secret references,
  maintainer authorization and a read-only credential qualification command,
  visible on the Projects screen. No GitHub account has been connected or probed.
- A distinct draft PR approval endpoint binds a reviewed run to its repository,
  base commit, patch and passing test receipts for 24 hours. Publication remains
  unavailable in ordinary use until a repository connection and live provider
  are qualified. The controlled GitHub REST adapter and operator command can
  create a draft PR only after that approval and a process secret are present;
  they have not been exercised against a live GitHub account.

For an operator-managed GitHub connection, register a repository with
`credential_ref=secret://env/AIP_GITHUB_TOKEN`, place the token in that process
environment, and run:

```powershell
uv run python -m scripts.qualify_github_connection --connection-id CONNECTION_ID --actor OPERATOR_ID
```

The command checks the repository identity, reported push permission, default
branch ref and pull request read access without creating GitHub objects. It
marks the connection ready only after all checks pass. The draft publication
command repeats the check immediately before use. GitHub branch rules and
pull request write permission still require an actual approved publication to
qualify; a read-only probe cannot prove those rights.
- Transactional run admission with an idempotency key, a budget reservation,
  a durable event and a dispatch outbox row.
- Tenant daily/monthly inference caps and concurrent run admission limits,
  serialized on the tenant row in PostgreSQL. A crossing of 80 percent emits
  a run budget warning.
- Evidence ZIP exports have a per-tenant daily byte cap (100 MB default). Each
  successful response records its size and SHA-256 in a scoped export ledger;
  excess requests return 429 and crossing 80 percent writes an audit warning.
  The trusted `scripts.set_tenant_quotas` command accepts
  `--daily-export-cap-bytes`, `--daily-sandbox-minutes` and
  `--daily-media-minutes` for an operator change.
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
  references paired to completed tool action IDs and typed ToolResult records.
- Local FFmpeg HLS encoding with staging and immutable publication.
- A controlled private-media publisher uploads complete HLS files with SHA-256
  checksums and server-side encryption to a scoped S3 prefix, then records a
  ready publication. An authorized viewer can receive five-minute CloudFront
  signed cookies for exactly one recording path; the player refreshes them.
  Deletion revokes new grants immediately and stays pending until a separate
  worker verifies S3 cleanup and CloudFront invalidation. These paths have
  local fake-client tests only; no AWS bucket or distribution is configured.
- A per-recording delete route and Review control for closed runs. It revokes
  local media access, removes that side's raw WebM and HLS files, and retains
  the run transcript and screenshot with a durable deletion record.
- A controlled synthetic form bug evaluation: real browser before/after
  recordings, exact-tree test receipts, an independent oracle, a review packet,
  and local HLS playback on the Evaluations page. The candidate is manual.
- Authorized review downloads include a bounded evidence ZIP with the packet,
  locally verified screenshots/logs, any verified fixture patch and a checksum
  manifest. Browser recordings remain separately delivered or revocable.
- Native complete-JSON and bounded SSE OpenAI, Anthropic and Google API
  adapters with provider continuation preservation and encrypted state
  envelopes. Streamed tool calls require terminal completion. No live provider
  account has been qualified.
- Operator commands register model entries as unqualified and probe a chosen
  provider account before enabling an entry. `python -m scripts.manage_model`
  records reasoned enable, deprecate and emergency disable transitions. No
  account has been probed here.
- React workspace with project onboarding, report submission, run history,
  review and usage views. It reads real API records; no fake run evidence is shown.

No provider model is automatically qualified or enabled. The reviewed local
fixture model is a separate synthetic-only exception; the UI labels it and
cannot use it for customer repositories.
Hosted API identity supports OIDC bearer tokens and a browser authorization-code
flow with PKCE. Browser sessions are opaque, stored server-side and protected by
Secure, HttpOnly, SameSite cookies and a CSRF header. This flow has passed only
controlled issuer tests; a real issuer has not been connected. Customer run
admission remains disabled until the production sandbox is qualified. Hosted
private media code is present, but its AWS origin, edge policy and playback have
not been qualified with a live account.

The [control-plane image](infra/control-plane/README.md) packages the locked
API and trusted worker code as a non-root container. A local read-only Docker
smoke test passed; no hosted control plane has been deployed.

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
uv run python -m scripts.set_tenant_quotas --tenant-id TENANT_ID --daily-inference-cap-usd 50 --monthly-inference-cap-usd 500 --max-concurrent-runs 4 --daily-sandbox-minutes 120 --daily-media-minutes 120
```

The shown amounts are default examples, not a provider budget qualification.
Inference caps count reserved upper-bound liability until usage is settled;
daily and monthly periods use UTC. Provider usage above its reservation posts a
breach event and blocks subsequent reservations once the cap is spent. Other
PRD resource quotas remain pending.

Automatic model selection is available through `selected_model_entry: "auto"`
only after a trusted operator records an explicit tenant allowlist.
Put this JSON in a policy file, replacing IDs with registered models:

```json
{
  "enabled": true,
  "allowed_model_entry_ids": ["QUALIFIED_MODEL_ID"],
  "allowed_data_classes": ["source_code"],
  "weights": {"utility": 1, "latency": 0.02, "cost": 0.2}
}
```

```powershell
uv run python -m scripts.set_tenant_model_routing --tenant-id TENANT_ID --operator OPERATOR_ID --policy-file PATH_TO_POLICY_JSON
```

Selection also requires a current live qualification and recorded platform
benchmark evidence for at least 30 cases. No such evidence or provider account
is configured in this checkout, so `auto` currently returns
`MODEL_ROUTE_UNAVAILABLE` instead of choosing an unqualified model. A file
containing `{"enabled": false}` disables routing. Manual model selection is
still available under its existing qualification checks.

A tenant may separately authorize one cross-provider alternate per source model
and data class in the same policy file, even with automatic selection disabled:

```json
{
  "enabled": false,
  "failover_routes": [
    {
      "from_model_entry_id": "SOURCE_MODEL_ID",
      "to_model_entry_id": "ALTERNATE_MODEL_ID",
      "data_classes": ["source_code"]
    }
  ]
}
```

The route is pinned at run admission and checked again against current tenant
policy. Only a definite HTTP 429 rejection can trigger this one-time switch;
the original request receives a durable rejected receipt and its reservation is
released. The alternate must be enabled, currently qualified, from another
provider, and have at least the original context/output capacity. No pending
tool effect may exist. A timeout or transport failure stays unresolved and
cannot switch providers automatically. The fixture path rebuilds a portable
context bundle for the alternate and records a separate model step and lineage
event. No live provider failover has been qualified.

Pinned plugin manifests can be registered, checked against an exact local
artifact digest, enabled or disabled, and allowlisted for a tenant from the
trusted operator shell. The manifest schema requires tool JSON schemas,
permission scopes, destinations and limits; registration alone grants no tool
authority. The operator command is:

```powershell
uv run python -m scripts.manage_plugin --operator OPERATOR_ID register --manifest MANIFEST_JSON
uv run python -m scripts.manage_plugin --operator OPERATOR_ID validate --plugin-id PLUGIN_ID --version VERSION --artifact REVIEWED_ARTIFACT
uv run python -m scripts.manage_plugin --operator OPERATOR_ID enable --plugin-id PLUGIN_ID --version VERSION
uv run python -m scripts.manage_plugin --operator OPERATOR_ID allowlist --tenant-id TENANT_ID --entry PLUGIN_ID@VERSION
```

The resolver rejects unapproved versions, changed manifests, missing scopes
and invalid arguments. Remote MCP versions remain disabled until an isolated
transport, credential audience and egress enforcement are implemented; no
external plugin is currently executed by the worker.

```powershell
uv sync --extra dev
Copy-Item .env.example .env
.\scripts\start_wsl_docker.ps1
.\scripts\compose-wsl.ps1 up -d postgres memgraph
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

For the current verified local synthetic session, use `http://127.0.0.1:5173/`.
Its API is on port 8098, backed by a fresh development SQLite database at
`artifacts/local-api-20261003.db`, and the polling development worker uses the
WSL Docker Engine with code-only image `aip-dev-sandbox:0.1.2`. A local UI run
reproduced the form bug, captured baseline tests/browser/oracle evidence and
played its seven-second HLS recording; without a qualified provider, it closed
`INCONCLUSIVE` and made no repair claim. The WSL distribution requires a live
session to keep its Docker containers running on this machine. The Review screen can
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
wsl -d Ubuntu-24.04 -u root -- bash -lc 'cd /mnt/d/projects/aiplatform && docker build -f infra/dev-sandbox/Dockerfile.code-only -t aip-dev-sandbox:0.1.2 .'
uv run python -m scripts.verify_development_worker --controlled-provider --image aip-dev-sandbox:0.1.2
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
use `.\scripts\compose-wsl.ps1 ps` or `.\scripts\compose-wsl.ps1 up -d postgres memgraph`
for Compose, and `wsl -d Ubuntu-24.04 -u root -- docker ...` for other Docker commands.

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

The latest migration reserves `aip_workflow` for LangGraph checkpoints. The
development worker initializes the checkpointer tables there and resumes a
failed phase by run ID after reclaiming the run lease. Its SQLite-only test
mode uses an in-memory checkpointer; use PostgreSQL for durable local runs.
The optional checkpoint integration gate is:

```powershell
$env:AIP_TEST_POSTGRES_URL='postgresql://aip:local_only@127.0.0.1:54329/aip'
uv run pytest -q tests/test_fixture_workflow.py
```

For local graph memory, start the Compose Memgraph service, set
`AIP_MEMGRAPH_URI=bolt://127.0.0.1:7687` for the API and projection worker,
and run the worker separately:

```powershell
wsl -d Ubuntu-24.04 -u root -- bash -lc 'cd /mnt/d/projects/aiplatform && docker compose up -d memgraph'
$env:AIP_MEMGRAPH_URI='bolt://127.0.0.1:7687'
uv run python -m scripts.project_memory_graph --serve
```

PostgreSQL is authoritative. The memory API reports `canonical_degraded`
while the graph is unavailable, behind its outbox, or mismatched with current
canonical facts. After restoring a graph, stop its projection worker and run
`scripts.project_memory_graph --rebuild-tenant TENANT_ID --rebuild-project PROJECT_ID`
for each project before restarting it. The opt-in live graph gate uses
`AIP_TEST_MEMGRAPH_URI=bolt://127.0.0.1:7687` with
`uv run pytest -q tests/test_graph_memory.py`. This is a local projection
path; graph high availability and hosted restore have not been qualified.
The projection worker also expires verified `environment_observation` facts
after 24 hours and writes a canonical transition/outbox event before removing
them from graph retrieval. Project members can inspect paginated fact history
at `GET /v1/projects/{project_id}/memory/records`; reviewer and maintainer
transitions remain role gated. The Memory page shows these records and their
sources. This does not yet validate independent corroboration or later repair
regressions automatically.

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

For browser sign-in, also set `AIP_PUBLIC_BASE_URL` to the public HTTPS origin,
`AIP_OIDC_CLIENT_ID`, `AIP_OIDC_CLIENT_SECRET`,
`AIP_OIDC_AUTHORIZATION_ENDPOINT`, `AIP_OIDC_TOKEN_ENDPOINT`, and a random
`AIP_BROWSER_SESSION_SECRET` of at least 32 bytes. Register the exact
`https://<public-origin>/v1/auth/callback` redirect URI with the issuer. The
browser app asks for a workspace ID, redirects to the issuer, and verifies an
ID token before creating an eight-hour maximum server-side session. The OIDC
subject must already have an active membership in that workspace. The browser
uses `/v1/auth/session` to obtain a per-session CSRF value and `/v1/auth/logout`
to revoke the current session. The OIDC configuration is intentionally separate
from the API bearer audience; the browser client ID is the ID token audience.

The owner can manage tenant and project memberships through `/v1/memberships`
and `/v1/projects/{id}/members`. Project reads and mutations check roles;
the last active owner cannot be disabled. The hosted API rejects run admission
with `EXECUTION_UNAVAILABLE` until a customer sandbox is implemented. The
bootstrap command is an operator action; `--owner-subject` must come from a
verified identity. This local implementation has not been tested against a
real issuer or deployed identity provider.

Private hosted media requires the API and private recording behavior on the
same HTTPS hostname, a private S3 bucket with CloudFront origin access control,
and a trusted key group on the `private-media/*` behavior. Configure
`AIP_PRIVATE_MEDIA_BUCKET`, `AIP_CLOUDFRONT_DISTRIBUTION_ID`,
`AIP_CLOUDFRONT_KEY_PAIR_ID`, and a base64-encoded RSA private key in
`AIP_CLOUDFRONT_PRIVATE_KEY_B64`. The current edge Terraform IAM policy
supports SSE-S3; using `AIP_PRIVATE_MEDIA_KMS_KEY_ID` requires a reviewed KMS
key policy and workload permissions.
The signing key stays in the API secret manager, while the publisher/deletion
worker uses an IAM role for S3 and CloudFront. The API does not serve hosted
recording bytes from its local filesystem. Once a completed recording exists,
the operator paths are:

```powershell
.venv\Scripts\python.exe -m scripts.publish_private_media --run-id RUN_ID --label baseline
.venv\Scripts\python.exe -m scripts.dispatch_private_media_deletions --serve
```

The [edge Terraform module](infra/terraform/edge/README.md) defines the
distribution, bucket policies, cache behaviors and scoped workload policies.
It has only passed local provider-schema validation. DNS, TLS, IAM role
attachments, a selected AWS account/region and live upload/playback/deletion
checks remain. This code path does not enable customer run admission by itself.
The static web deployment command validates the compiled assets and publishes
`index.html` last:

```powershell
.venv\Scripts\python.exe -m scripts.publish_web_assets --bucket WEB_BUCKET --dist apps/web/dist
```

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
