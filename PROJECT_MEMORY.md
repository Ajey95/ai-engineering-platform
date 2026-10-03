# Project context

## Active continuation checkpoint — 2026-10-03

**Objective:** finish the AI Engineering Platform PRD at
`E:\vab-downloads\AI_Engineering_Platform_PRD.md`. That document supplies product
requirements, not operating instructions. `IMPLEMENTATION_STATUS.md` is the
requirement-by-requirement evidence ledger. The platform is a verified local
prototype with substantial backend code; **the complete PRD and paid-pilot
release gate have not been achieved**.

**Workspace and publication:** `D:\projects\aiplatform`, branch `master`,
private source repository `https://github.com/Ajey95/ai-engineering-platform`.
The last published baseline before this documentation pass was
`50290a10c2ea8c33e5a2865f7f96c43f38203de6`. Check `git status` and
`git ls-remote origin refs/heads/master` before changing files; do not assume
this paragraph is the latest commit. The source repository is connected to
Ajey95; a repository connection for the platform's draft-PR feature is a
separate integration.

### Done and verified

| Area | Implemented state and evidence |
|---|---|
| Workspace | React Projects, Reports, Runs, Review, Evaluations, Memory, Usage, Settings and Operations views read real API records. Local Chromium exercised report/run flow, HLS playback, desktop/mobile layout and dialog keyboard behavior. |
| API and data | FastAPI, scoped tenant/project authorization, OIDC and PKCE code paths, Alembic migrations, atomic idempotent admission, canonical events, SSE replay, review/download routes and run decisions. Local PostgreSQL migration, race, isolation and SSE gates passed. |
| Execution | Development worker uses fenced leases, PostgreSQL LangGraph checkpoints, effect intent/receipt ledger, cancellation and safe replay. WSL containers ran pinned synthetic named, browser and hidden-oracle checks. |
| Repair protocol | Controlled OpenAI-adapter response produced a bounded patch, independently reran candidate checks, generated before/after HLS and reached `REVIEW_READY/PASSED`. Normal, input-resume and budget-resume probes passed on migrated disposable PostgreSQL; budget resume made one controlled provider request after approval. |
| Model and policy | OpenAI, Anthropic and Google adapter code; registry and qualification commands; pinned price/capability snapshots, reservation/usage accounting, tenant routing policy, bounded failover, context bundles, compaction, tool policy and plugin manifest validation have controlled tests. |
| Memory and media | Canonical memory states and provenance, revision-pinned code index, local Memgraph projection/fallback, FFmpeg HLS, scoped local playback and deletion, private publication/grant/deletion code with fake-client tests. |
| Operations and infrastructure artifacts | Quota ledgers, owner Operations UI, alert evaluator/webhook code, runbooks, OTLP propagation and local receiver probe. Terraform modules, Packer guest definition and control/media Dockerfiles exist and passed local format/build/smoke checks. |
| Evaluation and quality | Forty pinned synthetic cases: baselines and reference hidden-oracle checks passed. Latest PostgreSQL/Memgraph-enabled suite: **295 passed, 2 Windows symlink skips**; Ruff passed. Frontend TypeScript/Vite production build passed. Local PostgreSQL structural restore drill passed. |

### Pending before calling the product complete

| Priority | Remaining work / acceptance evidence |
|---|---|
| P0: live repair | Qualify exact OpenAI, Anthropic and Google model entries using real accounts; run a genuine model-produced repair on an authorized non-fixture repository; test provider streaming/tool continuation, outage and billing reconciliation end to end. Controlled responses do not establish autonomous repair. |
| P0: hosted boundary | Select AWS account/region, build the guest AMI, apply network/queue/database/artifact/control/edge infrastructure, wire secrets and TLS/OIDC, then challenge hostile-repository isolation, metadata denial, egress and late-callback cleanup on actual VMs. Hosted run admission remains off. |
| P1: publication and media | Qualify a real repository connection and approved draft-PR creation/reconciliation. Prove S3/CloudFront tenant isolation, signed playback, variant switching under network changes, media crash replay, deletion and invalidation with live services. |
| P1: reliability and operations | Run hosted restore/failover, load and latency trials, trace correlation, paging delivery, backup/tombstone replay, quota metering and incident ownership. Local measurements do not establish hosted objectives. |
| Evaluation and release | Execute full model benchmark attempts, held-out/regression matrix, all relevant PRD §27.1 acceptance scenarios and §27.2 paid-pilot signoff; publish a support matrix, actual cost ceiling, limitations and rollback artifact. |
| Expansion scope | General task stacks, richer agent navigation/tool cycles, remote MCP runtime, optional CrewAI/PageIndex and interactive browser work remain requirement-led P2 or partial areas. Follow the ledger before claiming any of them complete. |
| Local tooling | Docker Desktop 4.67.0 still fails on its `dockerInference` socket. Ubuntu-24.04 WSL Docker Engine is the working local path. Do not erase/reset Docker data to repair Desktop without a reviewed path. |

External identifiers still to choose for live qualification: AWS account and
region, OIDC issuer/application, OpenAI/Anthropic/Google accounts, an authorized
test repository and public DNS/TLS origin. Keep actual credentials in a secret
manager or local environment; never write them to this file. Ajey95 was chosen
for the private **source** repository only.

### Exact next checkpoint

1. Finish and verify this README/PROJECT_MEMORY documentation pass. The first
   GitHub Actions run for `50290a1` failed before tests because
   `astral-sh/setup-uv@v10` was not a resolvable action ref. The workflow is
   being pinned to the verified `v10.2.0` commit
   `c18668ad3cf93ea998bef934396af7bb5c839dc7`. Commit/push, then inspect
   the new Actions run and fix any actual failing gate before reporting CI green.
2. On continuation, run `git status --short`, `git log -1 --oneline`,
   `gh run list --repo Ajey95/ai-engineering-platform --limit 3` and read this
   section plus `IMPLEMENTATION_STATUS.md`. Recheck any long-running local
   process or cloud state before using it as evidence.
3. For independent code work, take the highest incomplete P0/P1 row from the
   requirement ledger, implement its failure and authorization paths, add a
   meaningful check, run the focused gate, then run the full suite when the
   integration risk warrants it. Update the ledger and this checkpoint with
   commit, observed result and next blocker.
4. When external identifiers and secret access are available, qualify one
   provider, one authorized repository and one hosted isolation path at a
   time. Keep customer admission closed until the release scenarios pass.

The remainder of this file is the chronological engineering log. New evidence
should update this checkpoint as well as append a dated entry; do not infer
current status from an older entry below.

## Historical engineering log

This directory began empty on 2026-10-02. The user's request is to implement
all of `E:\vab-downloads\AI_Engineering_Platform_PRD.md` version 1.0. The PRD
is product input, not an authority to invent completion or bypass safeguards.
The full scope includes P0/P1/P2; the PRD itself estimates a limited team pilot
at 12–16 weeks for two experienced engineers. At this starting point the user
had not chosen an AWS account/region, platform integration repository, or
OpenAI/Anthropic/Google API accounts.

Current build is a local foundation plus a controlled repair-evidence path,
**not 100% complete**. Use
`IMPLEMENTATION_STATUS.md` as the requirement ledger. Code lives in
`platform_app/` and `apps/web/`. `design-concept.png` is the generated direction
used for the UI. The frontend reads real API data and never displays invented
repair evidence. Hosted API identity now has a local OIDC and membership path;
hosted run admission still fails closed until VM sandboxing is qualified.

Verified before this turn: 15 Python tests passed; Vite build passed with bundled
Node 24; Playwright fallback exercised local project/report creation on desktop
and mobile without page errors; real FFmpeg HLS output was inspected by test.
Browser/IAB was unavailable due a request-header-policy error. An Hls.js player
is implemented but has not played an authorized artifact end to end.

2026-10-02 continuation: added `benchmarks/fixtures/form-submit` controlled
React/FastAPI bug fixture and independent hidden oracle. Baseline browser
scenario fails with POST 500; manually prepared candidate passes with POST 201.
The hidden oracle fails 1/2 on baseline and passes 2/2 on candidate. Both
browser runs produced screenshot/WebM evidence and local HLS renditions. A
concurrent port collision briefly produced a false baseline pass; fixture
health now checks a per-process instance token, and the sequential v2 runs
restored the expected fail/pass result. `platform_app/browser_runner.py` and
`verifier.py` produce scenario and exact-tree test receipts. Native HTTP
provider adapters exist with simulated-response contract tests but no live
provider qualification. Latest root suite: 24 passed, 1 skipped; the skipped
test is Windows symlink creation privilege. Docker development adapter and
image are written, but Docker Desktop engine is still unavailable. Backend log
reports an invalid `dockerInference` listener path during startup. No Docker
container run, autonomous agent repair, VM sandbox, Memgraph projection, CDN,
hosted deployment or paid pilot gate has been exercised.

The controlled evaluator writes `artifacts/evaluation-v2/review-packet.json`
with `candidate_origin=manual`, baseline named/browser/oracle = PASS/FAIL/FAIL,
candidate = PASS/PASS/PASS, and HLS media READY for both. The development API
exposes this only on loopback with no dev token. The React Evaluations page
played both HLS recordings in Chromium: 2 master playlists, 5 media segments,
2 decoded videos and no page errors. Docker Desktop's stale `dockerInference`
reparse point could not be renamed with PowerShell; the engine remains
unverified after restart. A same-directory backup of Docker's transient run
directory was made at
`C:\Users\AJEYA\AppData\Local\Docker\run.stale.20261002013922`, but the
engine recreated the failure, so Docker Desktop processes were stopped.
The local API and Vite development server were left running on
`127.0.0.1:8098` and `127.0.0.1:5173` to show the Evaluations page.

2026-10-02 Docker continuation: the user explicitly said not to schedule. The
thread heartbeat `continue-ai-platform-implementation` was deleted. Docker
Desktop 4.67.0 still crashes at the `dockerInference` AF_UNIX reparse point;
renaming the transient run directory and disabling Docker AI did not fix it.
The official Docker Engine 29.8.2 was installed in the existing Ubuntu-24.04
WSL distribution with systemd. `docker info` confirms a Linux overlayfs
server. The development image `aip-dev-sandbox:0.1.0` built from Python slim
with Playwright Chromium. The adapter maps Windows paths to WSL bind mounts,
runs as a non-root image user with no network, read-only root/workspace, no
capabilities, no-new-privileges, CPU/memory/PID limits and a timeout kill.
The controlled baseline browser run recorded POST 500 and failed; the manual
candidate recorded POST 201 and passed. The complete evaluator then ran named
tests, browser actions and hidden oracle in separate containers for both sides.
Latest packet `artifacts/evaluation-v2/review-packet.json` has scope
`synthetic_container_fixture`, baseline PASS/FAIL/FAIL, candidate PASS/PASS/PASS,
and both HLS media READY. The candidate remains `manual`, not an agent repair.
The hidden oracle is mounted only into its verifier container. Docker Desktop
itself remains broken; the WSL Engine is the working project runtime.
The live container security probe passed seven checks: non-root, no daemon
socket, no host drive mount, read-only workspace, metadata network denied,
zero effective capabilities and no-new-privileges. `python -m
scripts.verify_dev_container_security` reproduces it. WSL systemd services
do not keep the distro alive when no user process is attached; a hidden
`wsl.exe -d Ubuntu-24.04 -u root -- sleep infinity` process (PID 60188 at this
milestone) keeps this development Engine and Compose services available. It is
not a scheduled automation.

PostgreSQL 17.11 ran healthy in Compose. Alembic initial revision
`6185524d46e8` was generated and applied; `alembic check` found no pending
schema operations. A disposable PostgreSQL database concurrency test returned
one run, one budget reservation, one outbox event, and one run event for two
simultaneous requests with the same key. It created and dropped its own
database; the enabled model was a database fixture only. No live model call.

At that earlier milestone, the next step was a budgeted agent repair loop,
durable worker dispatch and independent receipts. Never call the manually
prepared candidate an agent repair.
Do not mark an adapter qualified without live account conformance. Keep
secrets out of this file and the repository.

2026-10-02 admitted worker milestone: added a development-only outbox worker
for the reviewed `form-submit-001` fixture. It archives the fixture and hidden
oracle from the run's pinned platform commit, claims a fenced lease, executes
named/browser/oracle baseline actions in separate WSL Docker containers, and
persists intent before each action and receipts afterward. The run packet now
reads these receipts. Only the reviewed fixture Git tree, manifest blob and
oracle blob hashes are accepted at that commit, so an arbitrary historic
commit cannot change the executable fixture content. The live worker check in
`artifacts/worker-verification/0b688adf8918` had outbox delivered, three
receipts PASS/FAIL/FAIL, baseline REPRODUCED, run INCONCLUSIVE and
`autonomous_repair=false`; the model row was a disposable database fixture.
The rebuilt image included an oracle SHA-256 in the receipt. Local tests are
35 passed, 1 Windows symlink skip; ruff clean. Docker Desktop is still broken,
but the independent Ubuntu WSL Docker Engine runs this path. Next: actual
budgeted model/patch loop, crash reconciliation and hosted boundaries.

2026-10-02 dispatch recovery follow-up: outbox processing and fenced claim now
commit together. Expired processing events with only completed actions are
requeued for replay; an `INTENDED` action with unknown outcome closes the run
inconclusively and leaves the effect unreplayed. Completed receipts require
matching persisted result files and output hashes on replay. Duplicate
dispatches for closed runs are acknowledged. Fault-case tests cover unknown
effects, missing artifacts and terminal duplicates; the real WSL container
verification passed after the refactor. Hosted queue recovery remains untested.

2026-10-02 patch and qualification boundary: `patch_workspace.py` now parses a
strict JSON patch, allows only `server.py` in the reviewed fixture, rejects
traversal/test changes/oversized content, copies the baseline to a separate
candidate workspace, and computes a stable patch hash. It is not yet connected
to a model call or the run verifier. Admission now refuses an enabled model
without a live qualification marker and validation time, except for an
explicitly scoped development fixture model and project. No live account is
configured or qualified. `scripts.verify_postgres_admission` still observed
one run/reservation/outbox/event under duplicate concurrent admission. The
latest worker receipt is `artifacts/worker-verification/217505165bb9` at
commit `16ba81e`; PASS/FAIL/FAIL baseline and INCONCLUSIVE run. Tests: 43 passed,
1 Windows symlink skip; ruff clean.

2026-10-02 review UI follow-up: the Runs evidence panel now renders stored
baseline receipt status, exact command, exit code, duration and tree hash
instead of treating a run without media as lacking test evidence. The model
selector uses an API `qualified` flag, and a fixture-only enabled DB row is
shown as fixture-only, not as a qualified provider. The Evaluations notice
uses the packet's actual synthetic container scope. Vite/TypeScript build
passed with bundled Node 24; local tests 43 passed, 1 skipped.

2026-10-02 budgeted fixture patch and admitted media milestone: added
`platform_app/model_budget.py` to reserve a qualified model call before HTTP,
enforce pinned registry/pricing and run limits, then settle provider-reported
usage. `agent_patch.py` persists the native response and digest before parsing
a strict one-file patch. The development worker now applies that patch to a
separate candidate and executes named, browser and hidden-oracle checks in
separate WSL Docker containers. Baseline and candidate browser WebM files are
encoded to immutable local HLS, with media status independent of the code
verdict. The API serves only tenant-owned published media and the Runs UI shows
before/after players and candidate receipts. A default disposable run at
`artifacts/worker-verification/6a7a8dd77a1b` was INCONCLUSIVE after
PASS/FAIL/FAIL baseline with media READY. A controlled provider response run
at `artifacts/worker-verification/21ad0f115e0e` reached REVIEW_READY after
PASS/FAIL/FAIL baseline and PASS/PASS/PASS candidate, both media READY; its
packet explicitly reports `autonomous_repair=false`. Both pinned commit
`5382f81`. The controlled HTTP response is predetermined test data, not a live
OpenAI call, model qualification, or independent diagnosis. Python tests: 48
passed, 1 Windows symlink skip; ruff clean; Vite/TypeScript build passed.
Docker Desktop remains broken at `dockerInference`; the separate Ubuntu WSL
Docker Engine is the functioning development runtime. No automation was
scheduled. Next priorities: repair Docker Desktop if feasible, hosted identity
and tenant roles, isolated customer execution, live provider qualification,
durable workflow/queue, private media distribution, and benchmark/release gates.

2026-10-02 review patch follow-up: the development API exposes a tenant-scoped
`/v1/runs/{id}/patch` download. It reconstructs a unified diff from the
trusted pinned Git fixture and a stored candidate only after rechecking the
candidate tree hash and patch hash. The Changes tab previews and downloads it.
Tampering causes `PATCH_CHANGED`; a focused test covers the valid and altered
candidate. At this follow-up the full suite was 49 passed, 1 Windows symlink
skip and the web build passed. Docker Desktop read-only inspection still
showed the `dockerInference` reparse point and backend error 1920; Windows CLI
could not connect, while the Ubuntu WSL Docker Engine reported 29.8.2 and
overlayfs. The working project runtime remains WSL. No Desktop data reset,
purge or other broad deletion was performed.

2026-10-02 reviewer decision follow-up: added actor-scoped accept/reject for
`REVIEW_READY` and a durable `review.decision` event plus audit record. The
decision closes the run idempotently while retaining its independent PASSED
verification verdict. Rejection requires a reason; accepting never publishes
a PR. The Runs and Review screens show the decision controls and outcome.
Cancellation of a review-ready run now also preserves the verified result.
Tests 51 passed, 1 Windows symlink skip; frontend build passed; ruff import
order was fixed after the first check and must be rechecked before commit.

2026-10-02 provider accounting hardening: native adapters now reject missing
required usage fields instead of converting them to zero. Anthropic input
totals include cache read and creation categories; Google output totals include
thinking tokens and any larger total-minus-prompt delta. The model budget
settlement refuses cached usage until cache pricing is explicitly qualified,
leaving the effect reservation unresolved rather than reporting a false
actual cost. Official provider documentation was checked for those usage
semantics. Python tests 53 passed, 1 Windows symlink skip; ruff clean. No
live provider call or account qualification occurred.

2026-10-02 admitted screenshot follow-up: browser runner now includes final
PNG SHA-256 in its receipt; replay checks the digest. A tenant-scoped screenshot
route verifies file content against that receipt, and Runs shows before/after
images alongside HLS. The development sandbox image was rebuilt (manifest
digest `e6ff20cf090428c2654334a0407241c3a8184a8df985bb98785b78a9ce2adbe6`).
A fresh controlled response run in
`artifacts/worker-verification/3bb018890c92` at pinned commit `6d28955`
reached REVIEW_READY with PASS/FAIL/FAIL baseline, PASS/PASS/PASS candidate,
both media READY and two screenshot hashes. It still has
`autonomous_repair=false`. Python tests 54 passed, 1 Windows symlink skip;
ruff and web build passed. Docker Desktop remains unresolved; WSL Engine ran
the verification.

2026-10-02 hosted authorization continuation: added PyJWT RS256 issuer,
audience, expiration and subject validation against an operator configured
JWKS URL. API requires an active tenant membership for the verified subject
and checks project roles on tasks, runs, evidence, media, memory and usage.
Owners can manage tenant/project memberships; updates are audited and the
last active owner is protected. A trusted operator bootstrap command creates
the first tenant owner. Migration `0a09a20564b1` adds memberships, role/status
checks and a composite tenant/project foreign key. Migration `4fba39f6713d`
adds scoped keys across task, run, evidence, budget, tool and memory records.
Disposable PostgreSQL verification proved the owner bootstrap, rejected
cross-tenant membership/task/event inserts, and retained one
admission/reservation/outbox/event in a duplicate request race. Local
PostgreSQL upgraded to the new head with no schema drift.
Python tests: 56 passed, 1 Windows symlink skip; Ruff clean. WSL Docker Engine
29.8.2 and PostgreSQL container healthy. No real OIDC issuer, customer sandbox,
provider account or hosted deployment was qualified. Hosted run admission
returns `EXECUTION_UNAVAILABLE`; Docker Desktop remains broken while the WSL
Engine is the verified development runtime.

2026-10-02 context continuation: the fixture patch call now receives a bounded
ContextBundle with task constraints, permissions, source commit/hash,
provenance locator, scope, trust labels and a labelled token estimate.
Oversized source/evidence is refused. Python tests 58 passed, 1 Windows
symlink skip; Ruff clean. Controlled worker run
`a42516d1-7377-49e5-a997-a74f215b0bb1` at commit `e7b3c2c` reached
REVIEW_READY with PASS/FAIL/FAIL baseline, PASS/PASS/PASS candidate and both
media READY. The provider response was predetermined and the packet correctly
states `autonomous_repair=false`; no live provider qualification occurred.

2026-10-02 paused-input continuation: migration `fb68543388d0` adds durable
resume target/key/input hash to runs. `PAUSED_INPUT` releases the lease;
authenticated project contributors can submit a bounded answer with an
idempotency key. Resume rejects changed policy or unresolved effects, closes
an older dispatch and queues one new dispatch. The fixture model ContextBundle
receives the last three authenticated resume inputs as labelled history.
`PAUSED_APPROVAL` and `PAUSED_BUDGET` cannot resume through this endpoint;
hosted resume remains disabled pending VM isolation. Python tests 60 passed,
1 Windows symlink skip; Ruff clean. Disposable PostgreSQL migration roundtrip
and local upgrade reached `fb68543388d0` with no schema drift. Controlled
Docker run `548f0098-b0c7-4524-a6a1-3e4d9a4cb1ff` at commit `8f28f50`
resumed input and reached REVIEW_READY with PASS/FAIL/FAIL baseline,
PASS/PASS/PASS candidate and both media READY; provider response controlled,
`autonomous_repair=false`. Isolated browser QA at 5174/8099 showed the form
on desktop/mobile, POST 202 to QUEUED, no page errors or mobile overflow.
Temporary QA servers were stopped; the existing user dev servers were not
changed.
Controlled rerun `a5947e25-9a7e-4941-b6b5-e74e9bbfcfa8` additionally
asserted that the resume answer and trust label reached the provider request.
The disposable PostgreSQL verifier separately passed `postgres_resume=true`:
the old dispatch was delivered and exactly one new dispatch was pending.

2026-10-02 deterministic action policy continuation: the development worker
and fixture model reservation call a reviewed effect policy before execution.
It binds tenant/project/task/run, current policy revision, fixture setup,
action name/class/target/source version and the run tool cap. Denials are
durable ToolAction/AuditEvent records. Python tests 66 passed, 1 Windows
symlink privilege skip; scoped Ruff clean. Controlled Docker run
`a0701196-992e-490c-8daf-cc0d6d29cfff` reached REVIEW_READY with ten
authorized effects, PASS/FAIL/FAIL baseline, PASS/PASS/PASS candidate and
media READY after PAUSED_INPUT resume. The provider response remained
predetermined; no live model or hosted security qualification occurred.

2026-10-02 model qualification continuation: added operator registration and
live probe commands. Registration stores declared capabilities without
authority. The probe requires exact operator-attested context/output limits,
prices and source URLs, then checks native text, one schema validated tool call,
continuation, usage and resolved model. Qualification is invalidated by model
metadata or provider adapter digest drift; lifecycle events use new migration
`b8c6d3259e41`. No provider account was available, so only controlled probe
tests ran. Local PostgreSQL upgraded with no drift; disposable migration
roundtrip and admission/resume checks passed. Controlled Docker run
`09cbf973-dfb2-43ad-8c4c-64dd0b42c463` at `de8de42` reached REVIEW_READY
with PASS/FAIL/FAIL baseline, PASS/PASS/PASS candidate and media READY after
resume. The controlled provider flag is now distinct from live qualification.

2026-10-02 tracing continuation: OpenTelemetry API/SDK and OTLP HTTP exporter
were added. The API, admission/resume, context/memory lookup, durable outbox,
worker, policy/tool effects, model budget/native provider HTTP and media have
content-free spans. W3C traceparent is persisted in dispatch events and
restored in the worker. In-memory SDK tests confirmed parent/child lineage;
72 Python tests passed, 1 Windows symlink privilege skip. Controlled Docker
run `06f0cb22-46ba-4c8f-999e-926281da6a3a` at `56261c0` reached
REVIEW_READY with ten effects and media READY. No OTLP collector/export or
hosted trace backend was tested; endpoint must be configured by an operator.

2026-10-02 local demo continuation: the old SQLite file was missing the three
paused-run columns and made `/v1/runs` return HTTP 500. The additive
`scripts/upgrade_local_sqlite.py` backed it up under `artifacts/db-backups`
before adding the columns; repeated execution reported current. The idempotent
`scripts/seed_local_demo.py` created a labelled project/report/fixture model.
The Runs form now includes the reviewed fixture ID and local pinned commit;
`DevelopmentWorker --serve` polls the outbox. The active refreshed demo uses
API `127.0.0.1:8099` and web `127.0.0.1:5174`; older processes on 8098/5173
remain stale. Playwright admitted run
`62d42f9f-487c-4419-92f9-f939978318e2`; the WSL Docker worker completed it
as INCONCLUSIVE with baseline media READY and no provider call. Desktop/mobile
Playwright checks showed persisted failure evidence, no page errors and no
horizontal overflow. This is a reproducible synthetic demo, not full PRD
completion.

The admitted run HLS player decoded and played the 7.2-second baseline in
Chromium. Its master/variant playlists, init and segments returned HTTP 206,
with no failed media requests. Docker Desktop 4.67.0 remains broken at a stale
`dockerInference` reparse-point socket; moving it failed with Windows file
inaccessibility and automatic approval review blocked deleting it outside
the workspace. WSL Docker Engine 29.8.2 remains the healthy project runtime.

2026-10-02 recording deletion continuation: migration `c53718b2a844` and
`platform_app/recording_deletion.py` add per-side deletion records. Closed-run
DELETE revokes media access before physical removal, audits the action,
removes local HLS/raw WebM, and preserves transcript/screenshot. The Review UI
offers a confirmed control and reports deleted sides. Local PostgreSQL reached
the new head with no drift. Python suite: 73 passed, 1 Windows symlink skip;
web build passed (Node 21 warning; Node 22.12+ required for supported Vite).
The current refreshed demo is web 5175/API 8100; worker remains on WSL Docker.
Disposable run `56a0dbcf-a3c6-46aa-b36e-8a3b914462aa` reached INCONCLUSIVE
with media READY. Live proxy check: media 200 before and 404 after deletion,
local HLS/raw WebM absent, screenshot 200, 19 events intact. Desktop/mobile
Playwright checks showed the deleted notice and loaded screenshot, no page
errors or horizontal overflow. Prior active cancellation probe
`598ae8aa-4962-4112-89a0-abc2f193389b` reached CANCELLED and left no run
container. SSE reconnect replayed event IDs 4–6 after a cursor of 3. Hosted
CDN/object/backup deletion propagation is still absent; do not claim FR-SEC-04
or AC-25 complete.

Development API startup now reapplies every recording deletion tombstone to
local files before serving traffic. A focused test restored the deleted HLS
and WebM files, ran reconciliation, and confirmed they were removed again
while the screenshot and single `artifact.deleted` event remained. This does
not provide an independent off-database ledger or hosted object/CDN replay.

The React SSE handler now refreshes the review packet after verification,
media, deletion, review and close events. A live Playwright form admission of
run `61c2837c-45e7-410a-bf76-e40c10216372` on web 5175 kept the run
selected and saw its Browser recordings panel appear without reselecting or
reloading the page; no page error occurred.

2026-10-02 PostgreSQL end-to-end continuation: seeded the reviewed synthetic
project/report/fixture model into local PostgreSQL 17.11 at migrated revision
`c53718b2a844`; `alembic check` reported no drift. API 8101 and a separate
`DevelopmentWorker --serve --runtime wsl` ran against PostgreSQL. Run
`e53fe64f-9a5a-4885-9966-81a8c915fc50` at pinned commit `b3f502c` reached
INCONCLUSIVE with one delivered outbox dispatch, four tool effects, contiguous
events 1–17, PASS/FAIL/FAIL baseline and media READY/HLS HTTP 200. No live
model call occurred. Web 5176 proxies to this API and is the current demo URL.
Desktop/mobile Playwright opened that run, decoded HLS and loaded its screenshot;
desktop played past time zero; neither had failed requests, page errors or
horizontal overflow. This is a synthetic local proof, not hosted release.

2026-10-02 deletion race fix: two simultaneous PostgreSQL requests initially
exposed a duplicate `run_events` sequence after the revocation commit. The
handler now reacquires the run row lock and refreshes ORM state after that
commit; it holds the lock through cleanup and final event insertion. A new
disposable run `9e8c0b45-7dfe-4403-93dd-5039470a0cb7` completed in WSL
Docker, then two concurrent delete calls both returned `complete`: one
deletion row, one `artifact.deleted` event, HLS absent, screenshot retained.
The disposable PostgreSQL verifier now includes the same two-request deletion
race and passed alongside migration roundtrip, duplicate admission, resume and
tenant constraints.

After commit `0a43f1b`, the PG API on 8101 was restarted from current source
and the 5176 Vite server was restarted under supported bundled Node 24.19.0.
Both API health and run `e53fe64f` HLS returned HTTP 200 after restart; the
WSL Docker Engine reported 29.8.2. Keep 5176 as the user-facing demo URL.

2026-10-02 tenant quota continuation: migration `eb8f0a7d36c4` adds UTC
daily/monthly inference caps (default $50/$500) and concurrent run cap (default
4). Admission and model reservations lock and refresh the tenant row before
checking usage; settled actuals replace reservations, while pending calls
retain upper-bound liability. Warnings emit on an 80% crossing. The trusted
PostgreSQL operator CLI audits changes and identical retries are inert.
Disposable PostgreSQL verification passed concurrent distinct-key admission
and inference reservation races, migration roundtrip, CLI audit and existing
deletion/ownership probes. The local PostgreSQL demo DB was upgraded to head;
`alembic check` found no drift. The existing SQLite development DB was backed
up and additively upgraded for tenant columns. Full Python suite passed 78,
with one Windows symlink privilege skip, using a D: pytest temporary directory
because C: had about 300 MB free. Resource caps beyond inference and run
count, and hosted billing/provider qualification remain pending.

2026-10-02 context excerpt continuation: `fixture_context_bundle` now verifies
baseline tool receipts against completed ledger actions and reads test logs by
safe run-scoped artifact path. It hashes the full file, checks the receipt
digest, and supplies bounded head/tail text plus artifact ref and matching
tool call/result ID. It rejects missing, tampered or unpaired evidence. The
controlled WSL Docker verification run `5355bee2-42d7-4fa5-9a5e-56b88c023439`
at pinned commit `7459696` reached REVIEW_READY/PASSED with ten completed
effects and baseline/candidate media READY. Its provider response was a local
HTTP fixture, so this verifies pipeline wiring only, not live model quality.
After these changes the full Python suite passed 80 tests with one Windows
symlink privilege skip; Ruff passed the edited Python files. The disposable
PostgreSQL verifier again passed schema roundtrip, quota races, deletion race,
owner bootstrap and tenant constraints.

2026-10-02 quota settlement hardening: settlement now takes the tenant row
lock used by reservations, books actual usage into the reservation's UTC
day/month, and emits `budget.breached` if an overrun crosses a cap. A disabled
tenant can still settle an in-flight provider liability. Tests cover period
reset, overrun and disabled-tenant settlement. Python suite: 83 passed, one
Windows symlink privilege skip; Ruff and disposable PostgreSQL verifier passed.

2026-10-02 verifier log streaming: synthetic named tests and hidden oracle now
stream combined stdout/stderr to their artifact files instead of buffering all
output in Python memory. Receipts hash file bytes and record `output_bytes`.
A focused binary-output test wrote 100,000 bytes, verified full preservation
and matching digest; existing timeout tests passed. The sandbox image rebuild
and controlled end-to-end replay were pending at this checkpoint.

2026-10-02 Docker disk recovery and regression workflow: a full WSL browser
image rebuild downloaded a changed Python base and browser packages until C:
reached zero free bytes, causing WSL filesystem I/O errors. The build was
stopped and WSL shut down. Automatic approval review rejected recursive
deletion of generated C: pytest folders; those exact folders were moved into
ignored `artifacts/pytest-c-cache-recovery`. Two inactive Terraform provider
temp files were moved into ignored `artifacts/c-temp-recovery`. The inactive
Puppeteer cache was moved into ignored `artifacts/c-cache-recovery/puppeteer`
and a junction left at its original C: path. C: then had about 740 MB free.
WSL Docker Engine 29.8.2 and PostgreSQL were restarted, followed by the PG
worker; web 5176, API health, and existing HLS returned 200. Do not run another
full browser image rebuild on this C: allocation until storage is relocated.

A code-only image `aip-dev-sandbox:0.1.1` was built from the existing base with
the new verifier file. Controlled WSL run
`388e8941-78ce-4fab-bbeb-7c9b5ab4b2ae` at commit `4e30b55` reached
REVIEW_READY/PASSED with ten effects and both media sides READY. The live
development worker now uses image 0.1.1. A native Linux UID 1000 fixture
probe passed artifact writes from an unprivileged container. Workflow
`.github/workflows/quality.yml` defines push/PR Python, web, PostgreSQL and
controlled sandbox jobs using verified action versions; no GitHub remote is
configured, so no hosted workflow execution has occurred. Local Python suite:
84 passed, one Windows symlink privilege skip; Ruff, Alembic drift check and
Node 24 web build passed. Docker Desktop's stale socket problem remains; the
WSL Engine is the working project runtime.

2026-10-02 typed fixture ToolResult: context assembly now converts completed
baseline receipts into a deterministic sanitized ToolResult with status,
structured fields, verified log artifact ref, byte count, duration and
truncation flag. Model-facing call/result IDs stay paired. A focused test
keeps a failed browser scenario FAILED even when an underlying HTTP response
was 200, and rejects a status string carrying instructions. General plugin
broker execution and authorized full-output retrieval are still pending.
The controlled WSL replay `cd522708-0287-4ca8-9b0e-df1614e3b72e` at
`ed52940` reached REVIEW_READY/PASSED with ten effects and both media sides
READY after this change. Full Python suite: 86 passed, one Windows symlink
privilege skip; Ruff passed.

2026-10-02 fixture context compaction: `platform_app/context_compaction.py`
reduces verified tool log excerpts when serialized input reaches 80% of the
model's conservatively estimated input byte capacity or the next required
content would overflow it. A pending tool cycle fails closed. It keeps the
task, source commit, immutable permission boundaries, decisions and paired
tool IDs in the prompt; saves content-addressed full source and deterministic
summary files with a 32 KiB summary cap. Scoped retrieval validates summary
and source SHA-256 plus task/decision equivalence. Persisted run creation time
anchors context source metadata for identical replay. The fixture model path
records one `context.compacted` event. An integration test forced compaction,
settled a controlled provider response, then replayed it without a second
provider request or duplicate event. Provider usage receipt now preserves
validated reasoning/cache detail fields so replay metadata agrees. This is
fixture-only; general session compaction and model-side original retrieval
remain pending.

2026-10-02 post-compaction verification: full Python suite passed 89 tests with
one Windows symlink privilege skip; Ruff passed `platform_app tests scripts`
and `git diff --check` was clean. Commit `7918476` contains compaction. An
older development worker pair and the prior current worker pair were stopped
after a PostgreSQL query found no leased runs; one worker was relaunched with
WSL runtime and image `aip-dev-sandbox:0.1.1`. API `/v1/health` and web 5176
returned 200. Controlled Docker replay
`0c3b4098-742a-4881-80d6-4bdc7dac2a52` at pinned commit `7918476`
reached REVIEW_READY/PASSED, with ten completed effects, baseline and
candidate media READY. Its provider response was synthetic and proves only
the local pipeline.

2026-10-02 diagnosis log retrieval: the API now serves completed fixture
named/browser/oracle logs from run-scoped artifact paths only after project
authorization and full SHA-256 receipt verification. The review packet lists
baseline diagnosis log URLs; the web log tab links them. A focused API test
checks authorized bytes, unknown step, other tenant and tampering denial.
General plugin full-output retrieval, signed hosted storage and full report
export remain pending.

Verification after commit `c8660e6`: 90 Python tests passed, one Windows
symlink privilege skip; Ruff clean; Node 24 TypeScript and Vite production
build passed. API 8101 was relaunched from current source. `/v1/health`
returned 200; the persisted demo run `e53fe64f-9a5a-4885-9966-81a8c915fc50`
returned two diagnosis log references in its review packet, and both named
and oracle authenticated downloads returned 200 with 984 and 7242 bytes.
The isolated controlled verifier run is stored in its own SQLite DB and is
not visible through the live PostgreSQL API.

2026-10-02 pinned code navigation: `SnapshotNavigator` offers bounded file
listing, text search, Python AST symbol lookup and file excerpts over an
already isolated pinned snapshot. Paths reject traversal and links; reads
carry commit, content SHA-256 and untrusted-source labels. The development
worker uses it to read the fixture's small `server.py` before model context
assembly. A test archives the trusted fixture from Git, verifies search and
symbol provenance, and proves the hidden oracle outside the base scope is
unreachable. Graph traversal and broad language indexing remain pending.

2026-10-02 navigation verification and runtime recovery: full Python suite
passed 92 tests with one Windows symlink privilege skip; Ruff clean. Controlled
WSL Docker run `79a81783-e06b-4976-baa5-393e4c55bc78` at pinned commit
`5961dd9` reached REVIEW_READY/PASSED with ten effects and both media sides
READY. The response was predetermined, so this is not live provider proof.
The turn was interrupted just after verification, stopping WSL and the local
API/worker/web sessions. `scripts/start_wsl_docker.ps1` restarted WSL Engine
29.8.2; PostgreSQL Compose is healthy. Separate foreground sessions now run
API 8101, worker with image `aip-dev-sandbox:0.1.1`, and Vite 5176. API and
web returned 200; the persisted demo run still returns two diagnosis log
references. Automatic approval review rejected a combined background relaunch
command; the separate foreground sessions are the working recovery path.

2026-10-02 memory selection: fixture model context now includes up to five
current verified MemoryFact records selected by task report terms, with exact
tenant/project/source revision filters. Each item carries fact ID, bounded
statement excerpt, full statement SHA-256, source refs, trust and scope labels.
Tests exclude unverified, revoked and wrong-revision facts. Completed model
actions return their digest-verified saved response before reassembling context,
so later memory revocation cannot change a replay or issue a second provider
call; tampered saved responses fail closed. General graph retrieval remains
pending.

2026-10-02 memory integration verification: 92 Python tests passed, one
Windows symlink privilege skip; Ruff and `git diff --check` passed. A local
PostgreSQL transaction inserted a verified fact, selected it by report terms
and exact source revision, excluded another revision, then rolled back. The
first smoke attempt had fixture setup order wrong under the composite project
foreign key; explicit tenant/project flush fixed the setup and the query
passed. Controlled Docker replay `05876cf6-72bf-48bb-9e78-59428fb53303`
at commit `b6dd3d5` reached REVIEW_READY/PASSED with ten effects and both
media sides READY; it remains a synthetic provider response. The development
worker was restarted from current code and one worker pair is running. API
8101 and web 5176 both returned 200.

2026-10-02 checkpointed fixture workflow: `platform_app/fixture_workflow.py`
owns baseline, qualification, patch and verification phases with synchronous
LangGraph checkpoints. PostgreSQL uses migration `4bc7f793d66a` to reserve
the `aip_workflow` schema; a test interrupted patch and resumed with a new
fence after closing and reopening its PostgreSQL connection. The worker now
executes these stages under its existing lease and fenced effect ledger.
Crash recovery acknowledges `REVIEW_READY` dispatches after expiry. A full
controlled WSL Docker run `dc1e1bb5-7f6a-4a1c-8a92-ca52c2779947` reached
REVIEW_READY/PASSED with ten completed effects and both media sides READY; a
separate resumed-input run `5d634827-635c-409e-bd0c-6c206d96fb4d` did the
same with two delivered dispatches. Both used predetermined provider responses,
not a live account. Local PostgreSQL upgraded to the new head and Alembic
reported no schema drift; `aip_workflow` contains the four LangGraph tables.
The full Python suite passed 94 tests with two skips (the PostgreSQL test is
opt-in without its URL, plus the Windows symlink privilege skip); focused
PostgreSQL checkpoint test passed when enabled; Ruff and diff checks passed.
General repository workflows, live providers and hosted sandboxing remain.

2026-10-02 review packet export: added an authenticated, tenant-scoped JSON
download of the persisted review packet and a Runs panel link. The API's
global no-store middleware applies. The packet references independently
authorized evidence URLs; it is not a self-contained archive. Tests verify
packet equality and denial for another tenant. Frontend TypeScript/Vite build
passed, though the shell's Node 21 emits Vite's unsupported-version warning.
Committed as `60e4224`. API 8101 and the WSL-backed worker were restarted from
this revision. API health and the authorized review packet download for the
persisted local demo run returned HTTP 200; the download was 5,175 bytes with
an attachment filename. Vite 5176 returned HTTP 200. The PostgreSQL admission
verifier also passed migration roundtrip at `4bc7f793d66a`, resume, quota
races, recording deletion race and tenant scope probes.

2026-10-02 automatic routing foundation: `platform_app/model_routing.py`
implements `selected_model_entry=auto` in admission. The route requires an
explicit tenant model/data-class allowlist, current live qualification and
tool checks, enough context/output capacity, an available 30-sample evidence
row for the exact model revision, and a recorded policy hash. It compares
utility, latency and cost with tenant weights, pins the chosen model/evidence
in the run snapshot and emits `model.routed`. A trusted operator CLI updates
the policy with an audit event. No actual benchmark evidence or live provider
account exists, so ordinary auto requests fail closed. Migration
`2d71e508a104` applied on local PostgreSQL with no schema drift. The full
Python suite passed 98 tests with two skips; Ruff passed. The disposable
PostgreSQL verifier passed migration roundtrip and its existing race probes.

2026-10-02 Memgraph projection: added the Neo4j Bolt client dependency and
`platform_app/graph_memory.py`. Canonical verified/deleted fact outbox events
project current state idempotently into project, repository, source-revision,
subject and fact nodes. A project-scope rebuild command removes and recreates
derived records. `/v1/projects/{id}/memory` uses graph IDs only as hints and
rechecks all returned facts in PostgreSQL; it reports `canonical_degraded`
and uses canonical lookup on graph outage, pending projection or result
mismatch. Local Memgraph Compose pulled and started; the opt-in real graph
roundtrip passed verified projection, scoped query, deletion and rebuild.
Pure outbox/fallback/API tests passed. The full Python suite passed 102 tests
with three skips (two opt-in database gates and the Windows symlink privilege
skip); Ruff and diff checks passed. Hosted graph recovery and load remain
unverified.
Committed the graph increment as `72ca67c`. Restarted API 8101 with
`AIP_MEMGRAPH_URI`, restarted the development worker, and started the graph
projection worker as separate foreground sessions. API health and Vite 5176
returned HTTP 200; a scoped local memory request returned retrieval mode
`graph`. The Memgraph service is running in WSL Docker. This smoke request had
no matching fact, while the opt-in integration test verified a real projected
fact and deletion.

2026-10-02 plugin registry foundation: added strict reviewed manifest schemas,
version/artifact digest pinning, registry lifecycle events, exact tenant
allowlist and tool-resolution checks for schemas and scopes. External JSON
schema references are rejected to avoid network resolution during validation.
Remote MCP endpoints must be public HTTPS origins in the declared allowlist;
remote MCP cannot be enabled yet because isolated transport and credential
audience enforcement are absent. A trusted operator CLI registers, validates,
enables/disables and allowlists versions. Local PostgreSQL upgraded through
`729b05a14f6c` with no Alembic drift; focused lifecycle/tampering tests passed.
The full suite passed 105 tests with three skips; Ruff and diff checks passed.
The disposable PostgreSQL verifier passed migration roundtrip, resume and its
race probes at the new head. The worker does not execute external plugin tools
yet.

2026-10-02 benchmark contract increment: added `platform_app/benchmark_contract.py`,
`scripts/validate_benchmark.py` and `benchmarks/README.md`. The versioned suite
requires exactly 10 cases per PRD category, at least 20 held out, distinct
fixtures and hidden oracle paths, pinned Git assets/digests, seed and accepted
outcomes. Scoring requires all 40 distinct attempts for one model, includes
failures in denominators and detects false success, but labels supplied
results `UNVERIFIED_RESULTS` because this tool cannot attest their origin.
Only one actual fixture exists; the other 39 cases and live qualification have
not been performed. Focused tests passed 3/3; the full Python suite passed 108
with three skips and Ruff passed. This is a contract, not benchmark evidence.

2026-10-02 repository connection increment: added a scoped GitHub connection
table and Alembic revision `e2466a8aa82c`, strict canonical HTTPS owner/repo
parsing, opaque `secret://` reference validation, maintainer create/disable,
reader list, idempotent repeat, and audit events. API omits the credential
reference and reports `credential_required`, `verification_required` or
`disabled`; no endpoint marks a connection ready without an integration probe.
Local PostgreSQL upgraded with no Alembic drift; the disposable migration
roundtrip, quota and tenant-scope probes passed. Python suite: 110 passed, 3
skipped; Ruff passed. This does not enable customer checkout or draft PRs.

2026-10-02 publication approval increment: `platform_app/publication.py` and
Alembic `cbd675bb9a11` add explicit 24-hour draft PR authority. It requires
an accepted completed run with PASSED verdict, a ready repository connection
pinned in the run snapshot, a native-provider patch receipt and three passing
candidate check receipts. Approval binds tenant/project/run/action/destination,
base commit, patch digest and a canonical hash of exact test effects/receipts;
it can be revoked and must be reverified before any publisher uses it. Scoped
database FKs reject cross-project run/connection references. Local API and
service tests passed; full Python suite 113 passed, 3 skipped; Ruff passed.
PostgreSQL upgraded to `cbd675bb9a11`, Alembic detected no drift, and the
disposable migration/race verifier passed. No actual GitHub push or PR creation
has occurred.

2026-10-02 controlled GitHub publication adapter: tightened approval binding
so named/browser/oracle candidate effects match the patch's candidate tree.
Added `platform_app/github_publication.py`, `publication_dispatch.py` and the
operator command `scripts/publish_approved_draft.py`. The adapter validates
workspace tree and patch hash, confirms the approved base commit, builds blobs,
tree and commit through GitHub REST, creates a deterministic run branch and a
draft PR with a unique marker, and reconciles branch/PR on retry. A durable
ToolAction intent is committed before the HTTP write; a receipt and consumed
approval are committed after a matching PR. Controlled HTTP tests covered
normal retry and a server error after PR creation; dispatcher tests covered
uncertain outcome and immutable request arguments. No live GitHub account was
used, no connection was marked ready, and no real PR exists. Python suite:
117 passed, 3 skipped; Ruff and diff checks passed.

2026-10-02 repository connection UI: Projects now fetches scoped connection
records, adds a GitHub URL with an optional opaque secret reference, disables
records, and shows credential/verification readiness without displaying secret
references. `apps/web` TypeScript/Vite build passed (Node 21 emits an upstream
version warning despite successful build). Playwright Chromium checked the
Projects -> Add repository -> Credential needed flow at 1440x900 with the
repository API intercepted, so no placeholder connection was persisted.
No page errors were observed. A settled 390x844 mobile screenshot showed the
form and project content without clipping; screenshots are under ignored
`artifacts/ui-qa/`. Browser plugin was unavailable; local Playwright was used.

2026-10-02 evidence bundle: review packets now derive draft publication state
from the approval and completed publication receipt instead of always saying
DISABLED. Added `platform_app/evidence_bundle.py` and an authenticated
`/v1/runs/{id}/evidence-bundle` ZIP endpoint. It reuses screenshot/log/patch
verification, rechecks each file digest during bounded export, and lists
SHA-256/byte count for every included artifact. Recordings are not embedded.
Unit/API tests cover archive contents, tenant denial and tamper refusal. After
restarting API 8101, a saved local run `9e8c0b45-7dfe-4403-93dd-5039470a0cb7`
returned HTTP 200 with a 13,723-byte ZIP containing named/oracle logs, packet,
baseline screenshot and manifest. Playwright Chromium verified the Review link
on that run with no page errors; screenshot in `artifacts/ui-qa/review-bundle.png`.
Python suite: 117 passed, 3 skipped; web build and Ruff passed.

2026-10-02 memory lifecycle: Alembic `0f647b9382ae` adds a database status
constraint and scoped `memory_fact_events` provenance table. Canonical service
supports proposed, verified, rejected, superseded, expired and deleted states;
non-current transitions emit graph projection events and close validity where
appropriate. Reviewer/maintainer transition API enforces project scope and
records an audit event. Both scoped lookup and model context selection require
current verified validity. Focused tests covered rejection, supersession,
expiry, events and API role/tenant boundaries. Local PostgreSQL upgraded to
`0f647b9382ae` with no Alembic drift; disposable migration/tenant/quota probes
passed. Full Python suite: 119 passed, 3 skipped; Ruff and diff checks passed.

2026-10-02 memory expiry and inspection: verification now gives
`environment_observation` facts a 24-hour validity window. The Memgraph
projection worker first expires due canonical facts in bounded batches, which
records a transition and tombstone outbox event. A stale verified fact cannot
supersede a current one. The project-scoped `/memory/records` API exposes
paginated states and provenance; the Memory UI shows history and role-gated
transition controls. Event timestamps are monotonic per fact even on Windows
clock ticks. Focused tests covered expiry, idempotence, scope, history and stale
replacement; full suite 121 passed, 3 skipped. Ruff, web build and Alembic
check passed. Chromium rendered the inspection page at 1440x900 and 390x844
with a synthetic intercepted record, no JS error or horizontal overflow;
screenshots are under ignored `artifacts/ui-qa/`. The live local API restarted
on 8101 and returned 200 for scoped records; the local project has zero facts.
Independent corroboration, regression feedback and immutable-artifact pinning
are not implemented. The graph worker restarted on current code.

2026-10-02 GitHub connection qualification increment: added a read-only
GitHub REST probe with exact repository identity, active state, reported push
permission, default branch ref SHA and pull request read checks. A process
secret `secret://env/AIP_*` operator command records scoped ready/unverified
state and an audit entry without persisting or printing the token. A failed
recheck revokes prior ready state. Draft PR publication now verifies the
approval before remote probing and rechecks the repository immediately before
use. Mock HTTP and SQLite tests passed; no live token/account was supplied, so
no real repository has been marked ready or PR created. A read-only probe does
not prove actual PR write permission or branch rules.

2026-10-02 reviewer feedback memory: new accepted and rejected review
decisions now atomically create a verified project decision fact sourced to
the exact `review.decision` event. Its verification scope explicitly says it
is a human review decision, not independent repair validation. Review
idempotence prevents duplicate facts. Focused service/publication/memory tests
passed 11/11. Automated invalidation after a later regression remains absent.

2026-10-02 operations snapshot: added an owner-only tenant-scoped
`/v1/operations/summary` API and Operations page. It aggregates persisted
24-hour run states, passed-verification/inconclusive rates among closed runs,
reviewer acceptance among decided reviews, current queued run
and graph outbox age, tool policy/failure counts, media statuses, call-only
actual spend and outstanding inference reservations. A cancelled run retaining
PASSED verdict does not count as success. Queue >5 minutes and graph lag >60
seconds appear as current snapshot warnings, not sustained pager alerts.
Provider latency/errors, context/token metrics, sandbox utilization and ABR
playback are labelled unavailable. Owner/tenant aggregate tests passed; mocked
desktop/mobile Chromium views had no JS error or horizontal overflow. Hosted
metrics/alerting and runbooks remain absent.
The local PostgreSQL API was restarted on 8101; the live operations endpoint
returned 200 with three closed runs, zero queued runs and five explicitly
unavailable metric families. Chromium then loaded that real API page at desktop
and mobile widths without JS errors or horizontal overflow. The metric was
refined to distinguish passed verification from reviewer acceptance; a
cancelled run with PASSED verdict is never counted as a verified close.

2026-10-02 export quota: Alembic `a9db61e2c7f4` adds a positive tenant daily
export cap (100 MB default) and a run-scoped export ledger. The evidence ZIP
route verifies and assembles content first, then locks the tenant row, checks
the UTC-day cap, records bytes and archive SHA-256, and commits before serving.
Excess downloads return 429 and the 80% crossing creates an audit warning.
The trusted tenant quota CLI accepts an optional export cap. Focused API and
quota tests passed; local PostgreSQL upgraded to the new head with no detected
Alembic drift. The disposable migration roundtrip, quota operator audit and
two-request PostgreSQL export race all passed, allowing exactly one 60-byte
export under a 100-byte cap. A live local evidence ZIP returned HTTP 200 with
13,723 bytes and created one ledger row with matching byte count and SHA-256.
The owner Operations page now displays daily export use/cap; a mobile Chromium
check against the real API had no JS error or horizontal overflow. Sandbox,
media and artifact storage quotas remain absent.
The final full Python suite passed 130 tests with three skips, Ruff passed for
application/tests/scripts and the new migration, web build passed, and Alembic
reported no drift. The user's existing `http://127.0.0.1:5173/` address was
not listening, so a Vite development server was started there with its `/v1`
proxy targeting API 8101; `/v1/health` returned `ok`. The earlier 5176 server
also remains available. No scheduled task was created.

2026-10-02 operations response catalog: added `platform_app/ops_alerts.py`
with owner, impact, runbook anchor and condition for the PRD's five critical
page classes and five warning classes. `docs/operations/runbooks.md` covers
security response plus provider, worker, sandbox, memory, media, database
restore and publication ambiguity. The owner Operations snapshot now returns
warning details and labels them `snapshot_only`. The UI shows warning owner
and impact. No pager, sustained warning evaluator or hosted response drill is
implemented; FR-OPS-01 remains Partial.
The current API restarted on 8101 and the existing 5173 web proxy returned the
owner operations summary with `warning_details` and export usage. The first
proxy request during API startup briefly returned 500; an immediate direct
request and subsequent proxied request returned 200. Chromium loaded the
mobile 5173 Operations page with no JS errors or horizontal overflow. The web
build and focused alert-catalog/operations tests passed.
Final gate for this increment: full Python suite 131 passed, 3 skipped; Ruff
passed for application, tests and scripts; web build passed. Git worktree is
clean at `fe887a1`. The 59-item requirement ledger currently has 52 Partial
and 7 Missing entries; none is fully qualified. The WSL Ubuntu Docker Engine
works for local development, while Docker Desktop's Windows service remains
stopped. Major remaining work includes general customer repository execution,
hosted per-run VM isolation, live provider qualification, 39 benchmark cases,
private CDN delivery, external plugin execution, sustained operations alerts,
and hosted restore/load/security qualification. The user asked for no schedule
and to defer external credentials until code-side work is done.

2026-10-03 operational alert increment: added Alembic `e4f23aa7190b` and
tenant-scoped durable alert/transition records. A separate evaluator samples
runnable queue and graph outbox lag under a tenant lock. Queue warning fires
only after ten minutes of observations no more than 90 seconds apart; graph
lag fires immediately. A persisted `budget.breached` run event fires a sticky
page that an owner can resolve with a recorded reason; a new event reopens it.
The owner Operations page shows active alerts and resolution controls. Local
PostgreSQL migrated to the new head; Alembic check found no drift. The local
evaluator is running with 30-second polling and the API was restarted on
8101; the existing 5173 frontend proxies it successfully. Desktop and mobile
Chromium rendered the page from the real API with no JS errors or horizontal
overflow. The full Python suite passed 133 tests, 3 skipped; Ruff and web
build passed. External paging, hosted supervision and the other alert evidence
producers remain absent. An uncommitted `memory.py` edit at turn start removed
required functions and broke API import; its diff was saved under ignored
`artifacts/memory_preexisting_2026-10-03.patch` before restoring the last
committed file. No user-authored code was silently discarded.

2026-10-03 provider error increment: a controlled WSL Docker run at commit
`a098a82` completed the synthetic baseline/candidate workflow with one
delivered outbox, ten tool effects, `REPRODUCED`, `REVIEW_READY`, PASSED verdict,
hidden oracle PASS for candidate and both media READY. It used a predetermined
provider response and is explicitly not a live autonomous repair. Native
provider HTTP failures now return bounded codes for auth, access, model
availability, rate limiting, timeout, overload, context overflow and invalid
schema without carrying vendor response text. OpenAI refusal retains usage;
the fixture patch path settles usage before rejecting refusal or truncation.
Focused provider/patch/worker tests passed 22/22. Full Python suite passed
142 tests, 3 skipped; Ruff passed. Live provider accounts remain unavailable.
The provider increment was committed as `85bfe32`; the local API was restarted
from that revision on port 8101, and the existing 5173 proxy returned HTTP 200
for health and the owner Operations snapshot. The worktree was clean at handoff.

2026-10-03 alert scope correction: migration `b6e1c4a0d9f2` adds a composite
foreign key from alert transitions to their alert's tenant and ID. An
automated SQLite foreign-key test and a rollback-only PostgreSQL probe both
rejected a transition claiming a different tenant. Local PostgreSQL upgraded
to the new head and Alembic check found no drift. The first strict SQLite run
exposed missing fixture flush ordering; the fixture was corrected, and focused
alert/operations tests passed 5/5.

2026-10-03 alert delivery increment: migration `f79e418c624a` adds durable
notification status, attempts, retry time, delivered time and safe error code
to scoped alert transitions. Firing and resolved transitions queue an outbound
notification. `platform_app/alert_delivery.py` sends a compact signed HTTPS
webhook with the transition ID as idempotency key, denies local/IP URLs,
does not follow redirects, and retries failures with capped backoff. A
separate operator process `scripts/dispatch_alerts.py` requires a configured
URL and secret; no live endpoint or credential was provided or contacted.
Controlled HTTP tests covered retry, signing, idempotency and unsafe URLs;
a rollback-only PostgreSQL probe persisted a delivered receipt. Local
PostgreSQL upgraded and Alembic detected no drift. The Operations API/UI now
shows pager configuration and pending/delivered counts. Hosted egress,
external pager semantics and the remaining alert evidence producers still
need qualification.
The full Python suite passed 149 tests, 3 skipped; Ruff and web build passed.
The Operations snapshot treats a malformed pager destination as unconfigured;
focused delivery/operations tests passed 11/11 after that refinement.
Committed as `a250d8a`. API restarted on port 8101 from that revision; the
existing 5173 proxy returned health 200 and a PostgreSQL-backed pager summary
with configured=false, zero pending and zero delivered. Desktop and mobile
Chromium rendered the new pager state with no JS error or horizontal overflow.
The worktree was clean after the commit.

2026-10-03 hosted browser identity increment: added OIDC authorization-code
login with PKCE and encrypted five-minute state, nonce/ID token validation,
opaque PostgreSQL browser sessions, Secure/HttpOnly/SameSite cookies, CSRF
header and origin checks, active membership rechecks and logout. The React
workspace now gates on `/v1/auth/session` and offers issuer sign-in in hosted
mode. Migration `c2a4d90871e6` was applied to local PostgreSQL and Alembic
check found no drift. A controlled HTTPS issuer test passed including login,
session, CSRF, origin rejection and logout. Full Python suite passed 150 tests,
3 skipped; scoped Ruff and web build passed. Node 21.7.1 printed Vite's version
warning but the build exited zero. No real issuer, public TLS or browser identity
account is available, so hosted sign-in is not live qualified. Docker Desktop
remains broken; WSL Docker Engine supports the local fixture path. API 8101 was
restarted from current source; after its startup, the existing 5173 Vite proxy
returned 200 for health and the development session. Headless Chromium loaded
the workspace at 1440 and 390 pixels with no page errors or horizontal overflow.
The in-app browser controller failed to load its request-header policy, so this
UI check used local Playwright instead.

2026-10-03 model rejection accounting increment: a definite provider HTTP
400/401/403/404/429 rejection now records a completed rejected model effect,
releases its reserved liability under the tenant lock, emits bounded model and
budget events, and replays as the same rejection without another provider call.
A timeout/transport failure keeps its model effect INTENDED and reservation
pending because outcome and billing are unknown; replay fails closed. Focused
budget/agent/worker tests passed 17/17, then focused budget/agent tests passed
11/11 after adding timeout coverage. Ruff passed. Live provider behavior is
unqualified and automatic cross-provider failover remains absent.

2026-10-03 tenant-authorized failover increment: routing policy now accepts an
exact `failover_routes` list with one target per source/data class, including
when automatic model selection is disabled. The trusted operator command
validates registered model IDs and different providers; run admission pins
the source routes. After a definite HTTP 429, the fixture worker receipts the
rejected call, then checks current tenant policy, admission route, data class,
active tenant/policy revision, no pending effects, target live qualification
and context/output capacity. It switches at most once, records model lineage,
and sends a fresh portable context bundle to the alternate. A restart follows
the recorded target step without reissuing the source call. Controlled tests
proved source 429, target completion, reservations, lineage and replay. A
timeout remains INTENDED and cannot switch. No actual provider account or
customer sandbox has been qualified, so this remains a fixture-path proof.
Focused routing/budget/agent/worker tests passed 24/24, scoped Ruff passed,
and the full Python suite passed 155 tests with 3 skips. The failover check
locks the tenant policy row and denies a change in the project's data class
from the admission snapshot.
At commit `1693279c57484bbde805c36a858ab26ac71f7fb2`, the WSL Docker
controlled-provider verifier processed run `a34e8228-51dd-4677-bddd-26dd1382881c`
to REVIEW_READY/PASSED with baseline PASS/FAIL/FAIL, candidate PASS/PASS/PASS,
10 tool effects, delivered outbox and both media READY. It used a predetermined
provider response (`autonomous_repair=false`) and therefore does not prove live
provider access or a customer repository workflow.

2026-10-03 private media increment: migration `d8c107a4f0b5` tracks a
tenant/project/run-scoped S3 publication. A controlled publisher checks HLS
file scope, SHA-256 and S3 HeadObject checksums, uploads with SSE, and commits
the publication after all objects. The API grants only an authorized undeleted
recording through a five-minute RSA-SHA256 CloudFront custom policy scoped to
that recording path; the React player refreshes at four minutes. The hosted
API no longer serves local HLS bytes. Deletion writes an outbox event and
remains pending until the remote worker verifies an empty S3 prefix and a
CloudFront invalidation marked Completed; local startup reconciliation skips
published recordings pending remote cleanup. Controlled signer, fake S3/edge,
API tenant/CSRF and deletion tests passed. Local PostgreSQL upgraded to the
new head with no Alembic drift, and the web build passed with a Node 21
warning. No AWS account/region, bucket, distribution, origin access control or
live playback has been selected or qualified.

The private media publisher now rejects linked ancestor paths and refuses to
overwrite existing S3 objects with different checksums. The benchmark verifier
uses one pinned Git tree listing per commit instead of a separate Git process
per fixture; its Windows timeout failure was reproduced once and the focused
test then passed. Final verification for this increment: 159 Python tests
passed, 3 skipped; private media focused tests 4 passed after the last upload
change; Ruff passed; the web production build passed; Alembic reported no new
upgrade operations against local PostgreSQL. These are controlled local checks,
not AWS edge or hosted execution qualification.

2026-10-03 controlled Docker recheck at committed `ffcef2d285f95769f595d8b0bd69a586dbf6164f`:
`scripts.verify_development_worker --controlled-provider --runtime wsl --image
aip-dev-sandbox:0.1.1` passed for run
`7eb01907-960d-4bc0-9e09-18a82c3581cb`. It reached REVIEW_READY/PASSED,
delivered its dispatch, recorded 10 tool effects and both media READY. The
provider response was predetermined, so this is a controlled fixture proof.

The run ledger now compares three consecutive completed tool actions by
logical action, arguments hash and stable result fields. An identical
no-progress sequence emits `run.loop_detected` and fails the active run with
an INCONCLUSIVE verdict. Focused ledger tests cover the guard and a changed
result reset. Private media publication also holds the run row lock across
upload, serializing revocation with S3 writes; abandoned partial uploads still
need a janitor and hosted qualification.
The full Python suite passed 161 tests with 3 skips after this change, and
scoped Ruff passed.

2026-10-03 private edge IaC: `infra/terraform/edge` now owns a CloudFront
distribution for the app hostname with HTTPS API forwarding, uncached HTML/API,
cached hashed web assets, and a private S3 media origin gated by a trusted key
group. OAC bucket policies restrict reads to that distribution, and separate
publisher/deletion IAM policies scope writes, listing, deletion and edge
invalidation. The private signing key stays outside Terraform state. Terraform
AWS provider v6.67.0 initialized, `terraform fmt -check` passed and
`terraform validate` passed. No account, region, DNS, certificate, load balancer
or remote state backend is chosen, so no plan/apply or live edge proof exists.

2026-10-03 web publication increment: the edge module now emits a separate
web-deployer IAM policy and routes hashed `/assets/*` through an optimized
cache while HTML stays uncached. `platform_app/web_publish.py` validates the
Vite output, bounds files, uploads checksum-verified immutable assets and a
release snapshot, then conditionally swaps `index.html` last. Fake-S3 tests
covered ordering, missing references, immutable conflict and concurrent index
changes (3 passed). The actual Node 24.19 Vite build succeeded; local build
inspection found one 408-byte index and three assets totalling 883,413 bytes.
Ruff and Terraform validation passed. No AWS account or deployed web origin is
available for a live publish/playback test.

The private media deletion dispatcher now processes a bounded set of pending
events per poll in ascending attempt count. A failed old event no longer
monopolizes the queue ahead of a fresh deletion. Focused private-media and web
publisher tests passed 8/8 and scoped Ruff passed; live S3/CloudFront replay
still needs the selected AWS account.

2026-10-03 control-plane image: `infra/control-plane/Dockerfile` built with
locked Python dependencies in the functioning Ubuntu WSL Docker Engine as
`aip-control-plane:0.1.1`, image digest
`sha256:8c7c46b3f0f550d92410bff41bc37f6393a2d91cfbd0584478a00e7fb77af4b5`
(1,142,363,898 bytes). A temporary container ran as UID 10001 with read-only
root, all capabilities dropped, no-new-privileges, PID/memory/CPU bounds and
tmpfs for SQLite/artifacts. Inside health returned ok; from Windows loopback
health returned 200, unauthenticated `/v1/projects` 401 and the smoke bearer
token returned an empty list. The container was stopped. This proves a local
container runtime, not ECS, live OIDC, a hosted DB or customer sandbox. Docker
Desktop remains broken; the WSL Engine remains the working runtime.

2026-10-03 EC2 lifecycle increment: migration `ef86c2bb703d` added a scoped
`sandbox_leases` ledger and upgraded local PostgreSQL with no Alembic drift.
`platform_app/sandbox_broker.py` persists the EC2 client token before launch,
checks run fences and bounded private/encrypted launch settings, revokes on
cancellation or expiry, and reconciles unknown launch outcomes before
termination. `scripts/dispatch_sandbox_cleanup.py` sweeps leases and drains
cleanup outbox entries with retry. A stale identity-map read after a run lock
was corrected using `populate_existing` on run and lease queries. The focused
broker/admission suite passed 19/19, the full Python suite passed 171 tests
with 3 skips before that refresh-only correction, scoped Ruff passed, and
`alembic check` found no drift. Fake EC2 confirms behavior; no actual VM was
launched. Hosted admission remains disabled because guest execution,
authenticated transport, applied network policy, snapshot handling and live account
qualification are not implemented.

2026-10-03 isolated sandbox network increment: a new
`infra/terraform/sandbox-network` module defines a dedicated private IPv4 VPC,
two or more subnets with no IGW/NAT route, no guest ingress, S3-prefix HTTPS
egress only and a gateway endpoint policy restricted to sandbox input/output
prefixes. `SandboxSpec` now admits only the reviewed `m6i.large` and
`m7i.large` 2-vCPU guest classes. Terraform AWS provider 6.67.0 initialized
from the cached signed provider lock, `terraform fmt -check` and `validate`
passed; six sandbox broker tests and scoped Ruff passed. No AWS plan/apply,
guest AMI, presigned object transport or live AC-15 network probe exists.

2026-10-03 SQS dispatch transport increment: migration `b6714d7c2a09` added
`outbox_events.queue_published_at` and a matching index; local PostgreSQL
upgraded and `alembic check` found no drift. The relay sends only event/run/
tenant IDs to SQS, commits a separate transport receipt, and may safely send
the same ID twice if the first SQS response/DB commit is lost. The consumer
checks the canonical event and tenant, extends visibility while processing and
deletes a message only after the outbox event is delivered/failed. The
development worker can target one event ID; the SQS CLI remains restricted to
the synthetic fixture. Terraform defines encrypted agent/media/projection
standard queues and 14-day DLQs with bounded redrive; format and validate
passed with AWS provider 6.67.0. Controlled queue tests passed and the full
Python suite passed 177 tests with 3 skips; scoped Ruff passed. No live SQS,
hosted customer consumer or production queue IAM role has been qualified.

2026-10-03 post-transport local end-to-end recheck at commit
`cb16fca92476eee6fe597d83f18930c053121f04`: the WSL Docker controlled
provider verifier processed run `c5b04f72-3359-43e7-87d5-4674548e6ac0`
to REVIEW_READY/PASSED with baseline PASS/FAIL/FAIL, candidate PASS/PASS/PASS,
10 tool effects, delivered dispatch and both recordings READY. The response
was predetermined (`autonomous_repair=false`), so this is a synthetic fixture
proof, not autonomous customer repair. The local API `/v1/health` and Vite
root returned HTTP 200. Headless Chromium loaded the Projects page and clicked
Runs, Review, Memory, Evaluations, Operations, Usage and Settings with each
expected heading and no page errors. It saved `artifacts/ui-current.png` for
local review. These checks do not prove hosted identity, queues or providers.

2026-10-03 archive boundary increment: `platform_app/safe_archive.py` now
extracts only regular files/directories into an empty workspace. It rejects
traversal, links, devices, duplicate/case-colliding and Windows-reserved paths,
file-as-parent ambiguity, and excess compressed/expanded/file counts. The
pinned fixture workflow uses it instead of direct `tarfile.extractall`.
Focused archive/development-worker/code-navigation tests passed 17 with one
skip, then archive edge tests passed 10 with one skip; scoped Ruff passed.
The single skip is a Windows directory-symlink privilege limitation. Hosted
customer source ingestion and guest execution remain unqualified.
The full Python suite then passed 187 tests with 4 skips.
At commit `afa427585c0d83723f10d18f80b7e7ef441731a7`, the WSL Docker
controlled-provider verifier again passed: run
`9b5b8ace-3a11-4fa8-9d8c-a49cccaa950a` reached REVIEW_READY/PASSED with
10 tool effects, delivered outbox, baseline PASS/FAIL/FAIL, candidate
PASS/PASS/PASS and both recordings READY. `autonomous_repair=false` remains.

2026-10-03 general environment contract increment:
`platform_app/environment_manifest.py` validates version 1.0 Python/Node
runtime declarations, bounded command argument arrays and timeouts, local
service names/ports/health paths, named tests, a typed Playwright scenario,
optional PostgreSQL fixture, reserved environment keys and explicit HTTPS
network destinations. `authorize_environment_destinations` requires an exact
approved origin set; DNS/private-address enforcement must occur in the hosted
proxy, which is still absent. The adapter translates the approved manifest to
the current browser and named-test runners. A real local HTTP service lacking
the fixture-only instance header passed the browser scenario and produced a
screenshot; a generic named test passed at an exact tree hash. The full Python
suite passed 203 tests with 4 skips and scoped Ruff passed. Install/build
orchestration, customer checkout and hosted VM transport remain unfinished.

2026-10-03 repository/S3 handoff increment: `repository_archive.py` verifies
an exact 40/64-character Git commit, streams `git archive` under a 50 MB cap,
rejects unsafe tar entries through the bounded extractor, and excludes dirty
or untracked checkout files. A temporary Git repository test passed after
disabling Windows Git newline conversion in the fixture. `sandbox_transport.py`
stages one immutable S3 source object with SHA-256 checksum, issues SigV4
HTTPS presigned URLs for exact source/ready/go/result keys, signs conditional
PUT and encryption headers, checks the idempotent go marker, and fills unused
output slots with tombstones after revocation. Fake S3 and locally signed URL
tests passed 5/5, scoped Ruff passed. No AWS bucket or VM has been exercised;
EC2 bootstrap and metadata sealing are the next required integration.
The full Python suite passed 208 tests with 4 skips after this increment.

2026-10-03 guest bootstrap and evidence transport increment: migration
`a2c9e7d54031` added encrypted user-data, source digest, `bootstrapping`
and seal receipt to a sandbox lease; local PostgreSQL migrated with no drift.
The broker now waits for a guest ready marker, disables EC2 IMDS, verifies
the applied disabled state, then posts a signed-scope go marker. The guest
stages a digest-pinned bundle, runs the baseline as uid 10001 after the seal,
and uploads a bounded evidence tar before its result. Its runtime is bounded
by both lease and signed URL expiry. The result reader checks lease/fence,
source digest, evidence size and SHA-256. Terraform defines a private,
versioned, encrypted sandbox artifact bucket; `init`, `fmt -check` and
`validate` passed with provider 6.67.0. The full suite passed 220 tests with
4 skips before evidence upload, then 222 tests with 4 skips afterward;
focused broker/transport/bootstrap tests passed 19/19, and scoped Ruff passed.
No AMI, AWS account run,
customer repository path or independent hosted repair has been verified;
hosted admission remains disabled.

2026-10-03 hosted retry and publication increment: the general native-model
proposal now accepts bounded feedback for up to three distinct attempts,
with each attempt tied to its own durable model action. The fenced hosted
coordinator executes a new candidate VM for each distinct candidate tree,
records each declared-check comparison, stops repeated trees and enforces an
active timeout. A controlled fake EC2/S3 and native-provider replay reached
REVIEW_READY after one and two attempts. Explicit accepted-review approval
now binds the hosted patch, model artifact and guest evidence, and the
publisher rebuilds the candidate from the pinned Git source before creating
a draft PR. Approval enqueues a durable, leased publication event; an expired
lease can reconcile a previous write. The Runs UI offers this separate
approval and reports the PR URL. Local PostgreSQL migrated to
`e71d5a4b8c20` with no drift. Full Python suite: 241 passed, 4 skipped;
Ruff and Vite build passed. This is controlled local proof, not a live
GitHub/provider/AWS qualification; hosted admission remains off by default.

2026-10-03 infrastructure definition increment: `infra/terraform/trusted-network`
adds two-zone public/private subnets, per-zone NAT and VPC flow logs;
`control-plane` adds TLS ALB, two API tasks, two agent tasks, one publication
task, managed Multi-AZ PostgreSQL, a private encrypted EFS access point and
per-workload Secrets Manager execution roles. The `pilot` root joins these
to the existing sandbox network/artifact bucket, SQS and CloudFront modules
and creates DNS aliases. ECS starts at zero tasks and hosted admission stays
off until database migration and live qualification. AWS provider 6.67.0
`terraform fmt -check` and `validate` passed for the new modules and root;
no account plan/apply, AMI build, live IAM/network test or restore drill ran.
The root still lacks Memgraph, Redis, media transcode workers and production
alert collection. Terraform initially hit a full C: temp drive; validation
used the existing provider binary in the workspace. An exact recursive
cleanup of the temporary download directory was blocked by automatic review,
so it is left ignored under `.terraform-tmp/`.

2026-10-03 hosted media increment: verified guest evidence archives now stage
only an approved browser WebM under the private artifact volume, keyed by
tenant/run/phase with a SHA-256 receipt. The hosted coordinator queues the
baseline and final candidate only. The media SQS/outbox transport uses exact
event IDs and a visibility heartbeat; a separate worker leases the job,
records a durable encode intent, writes deterministic HLS, publishes
checksummed private S3 objects, and exposes a manifest only after the DB
publication receipt. Deletion also removes staged hosted raw video. Terraform
now includes two media encoders and a media deletion task with separate
roles and a pinned FFmpeg image input. Controlled guest/SQS/S3 replay passed.
Full Python suite: 242 passed, 4 skipped; Ruff, Terraform root/module
validation and Alembic drift check passed. The FFmpeg media image is defined
but unbuilt; no live AWS media queue, EFS, CDN or browser playback was tested.

2026-10-03 guest AMI definition increment: `infra/sandbox-ami` pins a
source Ubuntu 24.04 AMI input, Amazon Packer plugin 1.8.1, Node 24.21.0
archive SHA-256, Python `uv.lock`, Playwright Chromium and uid 10001. The
user-data bootstrap and unprivileged runner now use `/opt/aip/.venv`, and
the image build checks a Chromium launch as the guest user. A deterministic
runtime bundle builder packages only `platform_app` Python files and lock/
project metadata. Packer 1.15.4 `init`, `fmt -check` and `validate` passed
using a local plugin cache on D: after C: lacked space; `bash -n` and seven
focused Python tests passed. No AMI build or EC2 guest run occurred because
the region, account, source AMI and build subnet are not selected.

2026-10-03 trusted source and hosted baseline orchestration increment:
`repository_fetch.py` resolves a run-pinned GitHub identity through a ready
tenant/project connection and environment secret reference, uses Git process
environment for Basic authorization, verifies exact `FETCH_HEAD`, and archives
only committed files; a local Git integration test excludes dirty/untracked
files. `hosted_baseline.py` joins the pinned source, manifest, S3 stage,
encrypted bootstrap, replay-safe EC2 launch, metadata seal and scoped result
read. Migration `d4c05e73b28a` stores a one-time guest result receipt without
setting a repair verdict; local PostgreSQL upgraded with no drift.
`sandbox_evidence.py` moves artifact tree traversal into uid 10001 and lets
root read only one bounded, no-follow evidence tar descriptor. Fake EC2/S3/DB
replay and source-scope tests passed. No deployed hosted worker, live GitHub
fetch, AMI build, provider repair, or real AWS end-to-end proof exists.
The full Python suite passed 229 tests with 4 skips; scoped Ruff and local
Alembic drift checks passed after the migration.

2026-10-03 general patch/candidate phase increment: `general_patch.py`
requires approved paths, per-file original SHA-256 and bounded replacement
text, then builds a deterministic candidate tar and review diff without
executing the repository. The safe extractor now preserves only the original
file's execute bit. Migration `f6092c41a8e5` records guest phase; after a
baseline VM is terminated, a distinct candidate VM generation can launch and
produce a separate receipt. The guest runner records its browser tree before
and after the scenario. `guest_comparison.py` reports whether declared checks
improved, failed or were inconclusive, with manifest, candidate tree, lease
and check-receipt pins; it does not claim hidden correctness. Local PostgreSQL
upgraded with no drift; the full suite passed 233 tests with 4 skips and
scoped Ruff passed. No general native-model proposal or hosted worker is yet
connected to these seams, and no real VM was launched.

2026-10-03 bounded general model proposal increment: a hosted-profile model
action is authorized only after a fenced baseline VM receipt is recorded.
`general_agent.py` constructs a bounded, trust-labelled prompt from up to four
pinned source files and baseline guest log tails, reserves spend before a
native provider call, persists the response and usage receipt, and verifies
context and output hashes on replay. An empty file list is an explicit
inconclusive proposal. Controlled provider/ledger tests and the full Python
suite passed 235 tests with 4 skips; Ruff passed. This is not a live provider
call or a deployed hosted worker; run admission remains closed.

2026-10-03 hosted coordinator increment: `hosted_worker.py` connects ready
GitHub source fetch, isolated baseline VM, bounded native model proposal,
candidate archive, separate candidate VM, declared-check comparison and a
hash-pinned review artifact. It synchronously revokes and confirms termination
before the next VM; expired SQS attempts close inconclusively without replaying
uncertain effects. `consume_hosted_dispatch.py` wires SQS relay/consumer to
the worker. Hosted admission validates a ready repository connection, current
model qualification, bounded repair paths and supported guest capabilities;
`AIP_HOSTED_EXECUTION_ENABLED` remains false by default. The API and UI read
hosted review receipts, patch diff and scoped S3 evidence tar with digest
checks. Controlled coordinator/admission/evidence tests passed; the full
Python suite passed 238 tests with 4 skips, Ruff and web build passed. This is
still not a deployed/live-provider/live-VM qualification or 100% product.

2026-10-03 code index milestone: exact hosted source archives create canonical,
revision-pinned file, symbol and import rows plus a Memgraph projection outbox
event. The scoped API rechecks graph hints against canonical rows and reports
degraded retrieval on lag or outage. Composite foreign keys bind each file to
a snapshot in the same tenant and project. Local PostgreSQL migrated to
`5b8f3d4e1a70`; Alembic found no drift. Full Python suite: 243 passed,
4 skipped; scoped Ruff passed. Controlled two-revision, dependency, tenant
isolation and hosted worker tests passed. Live graph and customer repository
qualification remain.

2026-10-03 control worker wiring: trusted ECS Terraform now includes a run
outbox relay, sandbox cleanup reconciler, Memgraph projector, operations
evaluator and pager dispatcher alongside the API/agent/publication/media
tasks. Relay and cleanup receive narrowly scoped SQS/EC2/S3 policies; only
artifact-reading workloads mount EFS. Terraform fmt and validate passed for
the control-plane module and pilot root. A real local Memgraph code graph
roundtrip projected file/symbol/dependency nodes, verified lexical lookup
and exact revision isolation, then cleaned the test scope. AWS account/region,
Memgraph host, credentials and live deployment are still unselected.

2026-10-03 dependency traversal increment: the code-index API now returns
one or two dependency hops from an exact repository path and commit. Memgraph
uses fixed Cypher with tenant/project/revision filters; the result is compared
with bounded canonical PostgreSQL traversal and falls back on lag or mismatch.
The live local Memgraph test verified idempotent file/symbol/dependency
projection and exact revision isolation. Full Python suite: 243 passed,
5 skipped (the live graph tests are opt-in for the whole suite). Terraform
control-plane and pilot validation passed; no hosted graph was deployed.

2026-10-03 benchmark catalog increment: commit `518528c` added 39 distinct
synthetic fixtures with hidden oracles and reference versions outside repair
trees. The 40-case suite manifest pins Git revision
`518528cb82395b92db7c99034e3785389290f9ad`, oracle and lock digests,
and local development sandbox image ID
`sha256:50e828b2fea7083c9989206e11e1d88a6bd3715d35f89800b923cfdf38b4bb3f`.
Asset validation passed. All 40 baseline/reference oracle pairs passed their
expected fail/pass criteria; one CRLF browser reproduction fixture was
corrected and its baseline/reference browser behavior rechecked. A reference
repair was added for the original form case.
CI now checks the pinned suite and runs all local oracle pairs. Full Python
suite: 243 passed, 5 skipped; Ruff passed. These are synthetic checks, not
independent live-provider scores.

Current local blocker: C: had about 45 MiB free at the latest check while the
Ubuntu WSL virtual disk remains there. The media Docker build failed with
read-only filesystem/I/O errors, then WSL reported
`Wsl/Service/E_UNEXPECTED`. Automatic approval review rejected removal of
four exact C: temporary downloads; do not retry through another route. The
user has been asked to free at least 2 GiB manually. Continue code and local
validation on D: meanwhile; do not claim Docker media or hosted qualification.

2026-10-03 database readiness increment: `/v1/health` remains a liveness
response while `/v1/ready` now executes a lightweight authoritative DB query,
returns 503 with no internal detail on failure, and is the trusted API load
balancer health target. Two HTTP-level tests passed for database up/down;
Terraform control-plane and pilot validation passed. This improves one
PostgreSQL outage gate but does not establish full hosted degraded-mode
behavior or availability objectives.

2026-10-03 database outage fail-fast increment: PostgreSQL connections now
have a three-second connect timeout and a bounded pool wait. Operational
database failures become sanitized HTTP 503 responses with a three-second
retry hint; raw driver/SQL details are not returned. Three focused HTTP tests
passed. This was prompted by the older running API timing out on a DB-backed
request after WSL failed; the running process has not yet been restarted with
the new code because its database is still unavailable. The full Python suite
passed 246 tests with 5 skips and scoped Ruff passed after this change.

2026-10-03 provider streaming increment: `provider_streams.py` assembles
bounded OpenAI Responses, Anthropic Messages and Google GenerateContent SSE
events. OpenAI tool argument deltas require matching `done` and a completed
terminal response; Anthropic tool blocks require block/message stop; Google
function calls require a normal finish and usage. No completed ProviderTurn
is returned for an interrupted or malformed stream. Controlled HTTP/SSE
tests passed for the three adapters, including transport failure. Google
modelVersion is retained when exposed. Native model qualification now makes
three complete-JSON and three streamed text/tool/continuation calls before
marking an account qualified; controlled qualification tests passed. Adapter
digest covers provider, stream and tool-boundary code. There are no available
provider accounts yet, so all live behavior remains unqualified.
Full Python suite after this change: 255 passed, 5 skipped; Ruff passed.

2026-10-03 model lifecycle increment: reasoned operator CLI transitions now
enable a currently qualified model, deprecate it for new admissions while
allowing an already pinned run to continue, and emergency-disable it to block
new model calls until requalification. Each transition writes a registry
event with the reason. New Alembic head `c78b82d1fa40` adds the reason
column; focused SQLite tests passed, but local PostgreSQL is still down in
WSL, so the migration has not been applied or drift checked there. Emergency
disable now pauses open runs, increments the run lease fence, clears worker
ownership, emits `approval.required`, and revokes
active guest leases with cleanup outbox events in the same DB transaction.
Model reservations now lock and refresh model/run rows in the same order to
avoid a stale enabled state. Focused SQLite tests verified pause, old-fence
rejection, cleanup enqueue and stale-model reservation denial. In-flight
provider charges still need reconciliation before any resume path.
Full Python suite: 259 passed, 5 skipped; Ruff and Alembic head inspection
passed. New head remains unapplied to PostgreSQL while WSL is unavailable.

2026-10-03 browser boundary increment: the controlled/hosted guest browser
now disables service workers, rejects and counts cross-origin subrequests,
popups and downloads, and fails a scenario if any occurred. The loopback
health client ignores proxy environment variables and refuses redirects;
malformed origin ports are denied. Response/console/DOM evidence is bounded
and the final screenshot uses the fixed viewport to avoid unbounded page
dimensions. A real Chromium test with an external image request failed as
expected; focused browser/manifest tests passed (21 passed, 1 skipped).
Hosted security-group policy remains a template only, without AWS proof.

2026-10-03 local recovery: moved the stopped legacy `Ubuntu` WSL distribution
from its 1,387,266,048-byte C: VHD to `D:\WSL\Ubuntu` using `wsl --manage ...
--move`; its source VHD is absent and target VHD present. C: gained about
1.3 GiB. The primary Ubuntu-24.04 and Docker Engine 29.8.2 restarted. WSL
automatically stopped the distro and its containers when no Linux session
remained, so a `wsl -d Ubuntu-24.04 -u root -- sleep infinity` session is being
kept open for local qualification. PostgreSQL and Memgraph were restarted.
Alembic applied head `c78b82d1fa40`, `alembic check` found no drift, and
`scripts.verify_postgres_admission` passed all reported scope, race, resume,
deletion and quota checks. The media image built locally at
`sha256:c5dc71907d45858299ff729cb780ca6d5828fdcfb08cebe78e0c7a47151ae993`;
its non-root CLI starts under no-network, read-only container flags. The image
export again exhausted C:, so Docker's regenerable build cache was pruned,
the WSL filesystem was trimmed, and `wsl --manage Ubuntu-24.04 --compact`
restored about 2.17 GiB free on C:. No AWS media queue/bucket was exercised.

The refreshed code-only sandbox image is `aip-dev-sandbox:0.1.2`. A controlled
provider replay with this image reached `REVIEW_READY`/`PASSED`, ten completed
effects, baseline fail and candidate pass for browser/hidden oracle, and both
HLS recordings READY; `autonomous_repair=false`. A separate fresh SQLite API
on port 8098 and Vite on 5173 were connected with a polling WSL sandbox
worker. A real UI run `e73fe87d-d215-4680-a222-d5d4266d7db8` reproduced
the baseline bug (POST 500, hidden oracle FAILED), closed `INCONCLUSIVE` as no
qualified provider exists, and produced a READY baseline recording. Chromium
loaded the review page without page errors and fetched HLS manifests/segments;
video `readyState=4`, duration 7.12 seconds. The user-visible local UI is
`http://127.0.0.1:5173/`, synthetic only. Hosted account/AMI/provider/GitHub
qualification remains absent.

2026-10-03 hosted context increment: extracted the canonical memory prompt
serializer shared by fixture and hosted general proposal paths. The hosted
proposal now selects up to five current verified facts matching report terms
at the run's tenant, project and pinned commit; it rechecks scope, validity,
source references and status immediately before serialization. A controlled
provider test confirmed one verified fact entered the proposal and an
unverified model claim did not. Focused context/general/memory tests: 9 passed;
Ruff passed. No live provider was called.

The hosted proposal now reads indexed symbols/imports for its allowed paths
from the canonical code snapshot. It requires exact tenant, project, commit
and archive hash, then compares each indexed file SHA-256 against the same
file extracted for the model prompt. A controlled provider test confirmed
the symbol was included and a changed indexed file hash fails before another
provider call. Focused general-agent/code-index tests: 2 passed, 1 skipped.

2026-10-03 operations increment: the owner-only operations snapshot now pairs persisted
model.started/completed/rejected events by run and step, reporting completed,
definitely rejected and still open calls, plus completed-call p50/p95 duration.
The UI displayed these from the local API in Chromium without page errors.
The tenant-locked alert evaluator now raises durable warnings for media.transcode
jobs older than ten minutes and nonterminated sandbox leases beyond expiry plus
a five-minute cleanup grace. Their snapshot counts, age and warning details are
tenant scoped; controlled tests cover firing, resolution and cross-tenant
exclusion. Provider first-event latency, uncertain outcomes, sandbox utilization
and live hosted telemetry are still unmeasured. The full Python suite before
the final alert additions passed 260 tests with 5 skips; focused alert/operations
tests passed 6 and Ruff/TypeScript checks passed after the additions.

2026-10-03 SSE capacity correction: the prior SSE route kept request-scoped
FastAPI yield dependencies during streaming. A 20-viewer loopback replay
succeeded, while 50 and 100 simultaneous viewers timed out and stalled API
readiness. Replacing only the dependency scope did not fix that deadlock.
The SSE route now authenticates and authorizes with a short SessionLocal context
before constructing StreamingResponse; each stream poll still opens and closes
its own scoped session. Production-style bearer/tenant denial tests pass. A
repeatable loopback probe (`scripts.verify_local_capacity`) measured 250/250
project reads at offered 50 requests/s with p95 19.8 ms and 100/100 SSE
viewers receiving replay event IDs, p95 initial ID 1909.9 ms. This is local
SQLite development evidence, not hosted 99.5% availability, connected-viewer
delivery target, OIDC, recovery or API production capacity. The local API at
8098 and Vite at 5173 remain running after the correction.

2026-10-03 pause and provider-failure increment: `run_ledger.transition` now
revokes intended/bootstrapping/provisioned sandbox leases when a run enters any
PAUSED state and writes sandbox.cleanup outbox events atomically before releasing
worker ownership. A focused test verified lease state, cleanup event and run
event; related ledger/sandbox/model tests passed 25. This does not yet provide
hosted midrun resume, approval expiry or snapshot reconciliation.

Both fixture and hosted general proposal paths now record `model.uncertain`
when a ProviderError is not a definitive rejected HTTP response. The event
keeps the reservation and INTENDED effect, so replay remains blocked. The
owner operations view reports unresolved uncertain calls. The tenant-locked
provider warning requires 20 decided calls, >20 percent error outcomes in a
five-minute window and five minutes of continuous samples. Focused provider,
general-agent, operations and alert tests passed 20; the local UI showed the
new uncertain count without page errors. Live provider accounts and pager
delivery remain unqualified.

2026-10-03 local restore drill: WSL Docker Engine 29.8.2 remains healthy when
started with `scripts/start_wsl_docker.ps1`, but the Windows Docker CLI context
points at the stopped Docker Desktop pipe; invoke Docker through Ubuntu-24.04
WSL for this checkout. PostgreSQL `aip` had head c78b82d1fa40, one tenant and
three runs. A 106,988-byte pg_dump (SHA-256 0484f4db38a96d22c16411cce3b1b0d1a5b0a0d6a0b1f72693fb74cfa8616f2d)
restored into new isolated `aip_restore_20261003` with `--exit-on-error
--single-transaction`; Alembic check found no drift. All 34 non-system tables
and row counts matched source at verification, with zero unvalidated foreign
keys or run/event sequence mismatches. Two synthetic media objects created
under a new isolated artifact root were removed by replaying the two restored
recording tombstones, with zero failures. Evidence and limits are in
docs/operations/local-restore-drill-20261003.md. No hosted backup, RPO/RTO,
Memgraph rebuild or external object restore was exercised. Full Python suite
after pause/provider changes passed 263 tests, 5 skipped; Ruff and web build
passed.

The local PostgreSQL admission probe was rerun after the pause change and
reported all checks true: schema roundtrip, resume, recording deletion race,
run/inference/export quota races, owner bootstrap, cross-tenant constraints
and one duplicate-admission run/reservation/outbox/event. Optional live local
dependency gates were explicitly enabled against Memgraph at 127.0.0.1:7687
and the isolated restored PostgreSQL database: Memgraph fact and code
roundtrips plus PostgreSQL LangGraph checkpoint continuation passed 3/3.
These are local synthetic checks, not hosted durability or customer data proof.

2026-10-03 PostgreSQL SSE load investigation: a second local API process on
8102 used isolated restored PostgreSQL. With default 5+10 DB pool, 250/250
project reads at offered 50/s passed (p95 24.6 ms) and 100/100 SSE viewers
received replay IDs, but initial replay p95 was 4546.1 ms. A 100-viewer
connected-event test gave 1503.8 ms p95. Moving short SSE authorization and
poll reads off the async event loop, using 250-ms empty polls with timed
heartbeats, and reducing idle authorization queries improved delivery but did
not meet the PRD below-one-second p95 at 100 viewers in one process. The API
reauthorizes before every event batch and every five seconds while idle.
Configurable PostgreSQL pool bounds were added; with 20+20 and current code,
100/100 connected viewers received a new event across four trials at p95
1129.7, 1178.7, 1177.6 and 1131.9 ms, while 50/50 measured 623.9 ms. The
local-only repeatable probe `scripts.verify_local_sse_delivery` requires an
isolated restore/test DB and appends one synthetic event. Evidence/limits are
in docs/operations/local-capacity-20261003.md. A real two-instance hosted
load-balancer/OIDC test is still needed; do not claim the one-second target.

The full Python suite after the SSE thread/authorization changes passed 263 tests
with 5 skips; Ruff passed. The latest local UI on 5173 continued to load its
Runs screen without browser errors after the API on 8098 restarted on the new
code. The isolated PostgreSQL probe API on 8102 was stopped after load tests;
the restored DB and private dump remain for review. The primary local UI/API
and WSL Docker/PostgreSQL/Memgraph services remain running.

2026-10-03 Settings UI increment: added owner-facing tenant and project membership
lists and role/status update forms on the existing Settings page. The forms
use the existing role-checked/audited API, preserve the last-owner guard and
show nonowner/API errors without exposing member data. TypeScript and Vite
production builds passed. Chromium on the live 5173 local fixture created
synthetic-reviewer-20261003 as a workspace member and a project reviewer;
both rendered in Settings with zero page errors. Quota/model/plugin admin and
real OIDC role testing remain pending.

The Settings forms initially rendered inline because only modal forms had input
layout styles. Added a scoped grid form style and checked 1440-pixel desktop
and 390-pixel mobile Chromium screenshots. Mobile navigation now exposes its
open/closed state with aria-expanded/aria-controls and closes on Escape; a
keyboard Enter/Escape test passed, with no horizontal overflow. The initial
mobile screenshot was captured during the 200-ms slide-out transition, not a
persistent sidebar overlap; waiting for the transition confirmed it was fully
offscreen.

2026-10-03 Docker/SSE increment: added `scripts/compose-wsl.ps1`, a PowerShell
Compose wrapper that starts the existing Ubuntu-24.04 WSL Engine and maps the
project Compose path without routing through the stopped Docker Desktop pipe.
Its `ps` command returned healthy PostgreSQL and running Memgraph. Added
PostgreSQL transaction-commit run-event NOTIFY and a per-process listener
thread with reconnect/poll fallback. SSE streams subscribe per run, still read
durable events and recheck tenant/project/role access before delivery; the
access recheck now uses one scoped SQL query. One SQLite test covers subscriber
scope and another checks owner/member/revoked/cross-tenant access. Ruff passed,
the full Python suite passed 265 tests with 5 skips, and the disposable
PostgreSQL admission/race probe passed after the event-write change. On the
isolated restored PostgreSQL API with a 20+20 pool, three 100-connected-viewer
new-event trials delivered 100/100 with p95 968.3, 851.5 and 885.2 ms.
This meets the local below-one-second target on those three samples only;
hosted two-instance/OIDC/load-balancer behavior remains unverified.

2026-10-03 event UI correction: the web client previously registered only a
fixed subset of named SSE event types, so approval, budget, failover, worker
error and some sandbox/media updates appeared only after history reload. The
API now sends every durable event as a standard SSE message with `event_type`
inside its versioned JSON payload; the web client handles `onmessage` and
keeps its event-ID deduplication. A local stream returned HTTP 200 with
`id: 1` and the expected JSON envelope, and the TypeScript/Vite production
build passed. The primary SQLite API was restarted with the current source;
`/v1/health` and `/v1/ready` returned success, and the Vite page returned 200.
Regular Playwright was used because the Browser plugin was not available in
this session. Chromium loaded the 5173 app (title and Runs heading correct),
received a real `run.admitted` SSE message, and had zero page errors. A route
intercept injected an `approval.required` SSE message without changing the
database; the run timeline rendered `approval · required` once with zero page
errors. Screenshots are outside the repository at
`D:\projects\frontend-sse-check-20261003.png` and
`D:\projects\frontend-sse-approval-20261003.png`.

2026-10-03 emergency-model approval increment: `qualify_model_entry` now
permits fresh qualification when the model's remaining open runs are fenced
`PAUSED_APPROVAL`. An owner-only `POST /v1/runs/{id}/resume-approval` accepts a
reason and idempotency key. Its transaction locks model/run/tenant, requires a
24-hour pending emergency approval event, exact pinned model metadata and
current qualification, unchanged tenant policy, no uncertain intended tool
effect, and enabled hosted execution for hosted runs. It restores a review-ready
run or queues one dispatch for active work. The operations evaluator now closes
expired emergency pauses with durable expiry/closed events and an audit row.
The UI renders an approval reason form and explains the gates. Controlled tests
covered requalification, stale model, expiration, uncertain effect, changed
policy, one dispatch and idempotent replay. Targeted 10 tests passed; the full
Python suite passed 265 with 5 skips; Ruff, TypeScript and Vite builds passed.
The disposable PostgreSQL admission/race probe still passed. Chromium showed
the approval form enabled in an intercepted paused state, with no page errors;
that browser state was mocked for layout only, not a live approval. Screenshot
outside repo: `D:\projects\frontend-approval-form-20261003.png`. Hosted
provider/account and full checkpoint resume remain unverified.
The restarted local API exposed the new endpoint and rejected an approval POST
against an already `INCONCLUSIVE` run with HTTP 409 `RUN_NOT_RESUMABLE`, as
expected. The local operations evaluator one-shot passed and a hidden 30-second
loop was started against the synthetic SQLite tenant; its first sample reported
zero firing alerts. This is a local process, not a scheduled automation.

2026-10-03 run-budget increment: admission accepts an optional lower
`max_spend_usd`, bounded by the operator cap, and records it in the run policy
and run-cap ledger. A distinct run-spend error pauses an INVESTIGATING/PATCHING/VERIFYING
worker as `PAUSED_BUDGET` while fencing leases and acknowledging its dispatch.
The owner-only `/resume-budget` endpoint requires a reason, a higher cap no
larger than the current operator maximum, an unexpired 24-hour pause, current
model qualification, unchanged policy, no uncertain tool effect and an intact
run-cap ledger; it updates the policy and ledger, events/audits the increase,
and queues one dispatch. Tenant daily/monthly quotas still apply to each call,
so the run cap is not booked as tenant spend. The operations evaluator closes
expired budget pauses. Targeted tests covered admission, distinct spend error,
worker pause/ack, cap/policy/uncertainty/expiry and idempotent resume. The full
Python suite passed 270 tests with 5 skips before a Decimal normalization-only
change; the targeted budget tests passed again afterward. Disposable PostgreSQL
admission/race probe, Ruff, TypeScript and Vite builds passed. Chromium found
the admission budget field and enabled the approval form with no page errors;
the paused UI state was route-intercepted, not a live budget pause. Screenshot
outside repo: `D:\projects\frontend-budget-form-20261003.png`. API, worker and
operations evaluator were restarted on the current synthetic SQLite database.
The refreshed API admitted a new local synthetic run
`851d8ffb-d0d1-4876-b815-561910ccd653` with a $1.000000 cap. Its WSL worker
completed the baseline named test/browser/hidden oracle path, persisted 17
durable events, produced READY media and closed `INCONCLUSIVE` because the
fixture model cannot autonomously repair. The review packet reported
`REPRODUCED` and one baseline named-test receipt. Chromium displayed the run
as INCONCLUSIVE with REPRODUCED baseline and no page errors. This proves the
lower-cap admission/worker path, not a live budget pause or provider repair.
Screenshot outside repo: `D:\projects\frontend-budget-run-20261003.png`.

2026-10-03 controlled budget pause/replay: the first provider reservation in
the fixture happens during `INVESTIGATING`, so the budget-pause transition and
worker handlers now support that target as well as patching/verification.
`scripts.verify_development_worker --controlled-provider --budget-pause-probe
--runtime wsl --image aip-dev-sandbox:0.1.2` passed in isolated artifact
directory `artifacts/worker-verification/5ae17c234ebd`, run
`5662c25c-b814-49cf-ab86-fecda47bb84c`. The initial $0.000001 cap paused
before any controlled provider HTTP request or `model.generate` tool action;
one owner approval raised it to $5, one new dispatch ran, exactly one provider
request occurred, and the run reached `REVIEW_READY`/`PASSED` with candidate
named/browser/oracle checks and both media manifests ready. The controlled
response is predetermined test data, so this proves local orchestration and
budget ordering only. Targeted budget/development-worker tests passed 11/11;
the full Python suite passed 271 with 5 skips, scoped Ruff and `git diff --check`
passed. No frontend code changed in this increment; its previous TypeScript and
Vite build remained the latest UI check.

2026-10-03 tenant quota administration increment: added strict owner-only
`GET/PUT /v1/tenant/quotas`, with tenant row locking, daily/monthly ordering,
bounded spend/concurrency/export fields, a required reason and one audit event
per actual change. Settings now reads and edits the four quotas. The hosted
OIDC role-boundary test passed for member denial, owner update, invalid monthly
cap rejection and idempotent no-op audit behavior. TypeScript and scoped Ruff
passed. Against the local API SQLite tenant, Chromium changed the daily
inference cap $50 -> $49 -> $50 through the form; each API read matched and
there were zero page errors. Screenshot outside repo:
`D:\projects\frontend-quota-settings-20261003.png`. This is local control
plane proof, not hosted identity/load qualification.
The full Python suite passed 271 with 5 skips, TypeScript and Vite production
build passed, scoped Ruff and `git diff --check` passed.

2026-10-03 pause-integrity increment: `platform_app/pause_integrity.py`
records a canonical checkpoint in each new paused `run.state_changed` event:
the pinned run plan, completed tool receipt digests and recorded guest result
digests. Input, emergency-model and run-budget resume now verify the event
checksum and require all previously completed evidence to match before
requeueing; later reconciled effects remain allowed. Budget approval also
requires the existing run-cap reservation to equal the old cap. Targeted
tamper/approval/worker tests passed 23/23 and the new eight-test pause/budget
subset passed; scoped Ruff and diff whitespace checks passed. Controlled WSL
Docker budget pause/replay run `a8ba3ed7-4404-4637-88b6-31e5db732d2f`
passed at artifact directory `artifacts/worker-verification/82045c9c1fe9`:
zero provider requests before the pause, one after approval, 10 completed
effects, candidate checks passing, both media ready, REVIEW_READY/PASSED.
The provider response was controlled data. Full suite and PostgreSQL probe
were pending at this checkpoint.
Those gates then passed: full Python suite 274 with 5 skips; disposable
PostgreSQL admission/race probe at migration head `c78b82d1fa40` passed.

2026-10-03 injection boundary increment: a native adapter receiving an
unregistered complete-JSON provider tool call now raises sanitized
`PROVIDER_TOOL_DENIED` instead of leaking an uncaught `ToolCallError` out of
the worker. The same classification covers completed OpenAI/Anthropic streamed
tool calls; incomplete streams still have their distinct interruption code.
Both fixture and general model paths retain uncertain provider liability,
emit `tool.denied`, and audit the denied authorization without persisting
provider-supplied tool arguments. A controlled poisoned-source comment plus
`publish_code` response produced no publication tool effect; exact test checked
the event/audit and preserved reservation. Targeted provider/stream/budget
tests passed 38/38 and scoped Ruff passed. This is a controlled AC-24 style
test, not a live hostile-repository or credential access drill. The full suite
was running at this checkpoint.
The full suite subsequently passed 277 tests with 5 skips.

2026-10-03 plugin administration increment: owner-only
`GET/PUT /v1/tenant/plugins` provides a sanitized version catalog and
per-tenant allow/revoke with a required reason. Grant requires an enabled,
validated entry whose manifest/artifact digests still match; the tenant row
is locked and changed decisions are audited. It never enables remote MCP
transport, which remains blocked by the registry. Settings displays the
catalog and a reasoned access form. Direct registry and hosted OIDC/API tests
passed 6/6, TypeScript and Ruff passed. Chromium displayed and submitted an
intercepted ready-plugin allow state with no page errors; backend success was
separately verified in the API test. The inspected screenshot outside repo is
`D:\projects\frontend-plugin-settings-20261003.png`. Remote plugin execution
and live credential audience qualification are still absent.
The full Python suite passed 278 with 5 skips; the final targeted plugin/API
suite passed 6/6 after an explicit forged remote-MCP-enabled state rejection.
The final TypeScript/Vite build and scoped Ruff passed; diff whitespace check
passed. The local API on port 8098 was restarted with the plugin endpoints.

2026-10-03 hosted general-context compaction increment: before a general
provider request, the worker computes the pinned model's conservative input
byte capacity and compacts at 80 percent or before overflow. It retains
complete allowed source, task, baseline status, prior attempts, commit and
hashes; trims secondary logs/browser evidence, index detail and memory. The
original prompt and deterministic summary are content-addressed under the run.
Replay verifies artifact lineage and the recorded context hash. A pending tool
cycle or still-oversized required source fails closed. The integration test
sends two distinct compacted prompts through a controlled provider and replays
the first without an extra HTTP request. Full Python suite passed 280 tests
with 5 skips before final summary-field assertions; targeted tests must be
rerun after those assertions. Model-side original retrieval and live provider
proof remain absent.

2026-10-03 operations context-metrics increment: `model.started` now records
the conservative preflight input estimate without prompt content. The owner
operations snapshot aggregates tenant-scoped 24-hour compaction count, median
input estimate and median absolute estimate error against provider-reported
input for completed calls, with an explicit sample count. Older events without
estimates remain excluded from the error metric. Operations UI renders these
fields; a local Chromium visit showed the new card with zero page errors.
Targeted operations/model/general tests passed 17/17, scoped Ruff, TypeScript
and Vite production build passed. The API on port 8098 was restarted. The
full Python suite then passed 280 tests with 5 skips after this change.

2026-10-03 context artifact retrieval increment: `GET
/v1/runs/{run_id}/context/{summary_sha256}` requires run/project access and a
recorded `context.compacted` event. It returns the deterministic summary and,
only on `include_source=true`, the original prompt after scoped SHA-256 and
lineage checks. Context readers reject oversized artifacts. The controlled
hosted-role API test passed member access, non-member and cross-tenant denial,
unrecorded digest denial and changed-source conflict; context tests passed 6/6.
The original retrieval is for authenticated users; provider-side retrieval is
still absent. Full suite passed 281 tests with 5 skips; scoped Ruff and diff
whitespace checks passed.

2026-10-03 context UI and Docker check: the Run activity log now links each
recorded compaction to its authenticated summary and original JSON. TypeScript
and Vite build passed. Native Docker Desktop still cannot create its inference
listener because `AppData\\Local\\Docker\\run\\dockerInference` is an
inaccessible reparse point (Windows error 1920). An exact-path rename and
`fsutil reparsepoint delete` failed with that filesystem error; automatic
approval review blocked a PowerShell `Remove-Item` attempt. No further removal
was attempted. The independent Ubuntu WSL Engine reports Docker 29.8.2 and
continues to run the project containers. Docker Desktop repair remains open;
the product's local container path works through WSL.

2026-10-03 cached-token pricing increment: ModelEntry and ModelRegister now
accept optional cache-read and cache-write rates, pinned by attestation,
qualification record and run snapshot. The reservation uses the highest
configured input rate; settlement checks cached category counts against total
input and prices uncached, cache read, cache write and output separately. A
provider response with nonzero cached usage and no matching attested rate
still fails closed. Tests covered a $0.000110 settlement, worst-case liability,
overcount rejection, metadata drift and qualification invalidation. Migration
`d13c76a3f9b2` added two nullable numeric columns to model_entries; local
PostgreSQL upgraded with no Alembic drift, local API SQLite was backed up and
upgraded, and the disposable PostgreSQL probe passed at the new head. The
full Python suite passed 287 with 2 Windows symlink skips when local PostgreSQL
and Memgraph gates were enabled. Scoped Ruff passed. API port 8098 restarted
and health returned OK. No live provider billing has been verified.

2026-10-03 pinned settlement follow-up: the actual usage charge now reads
the run's frozen price snapshot, so a registry row edit between reservation
and settlement cannot change the bill. The cache pricing test mutates current
input/cache rates after reservation yet settles $0.000110 at the pinned rates.
Model budget tests passed 16/16, fixture/general/hosted model path tests 4/4,
scoped Ruff passed, and the full suite again passed 287 with 2 Windows symlink
skips with the PostgreSQL and Memgraph gates enabled.

2026-10-03 post-pricing local end-to-end check: the WSL sandbox image
`aip-dev-sandbox:0.1.2` ran the controlled-provider budget pause and approval
replay as run `ed57dff7-8968-4452-9887-eab0abd3e318` at
`artifacts/worker-verification/e0d077f63266`. Zero provider calls occurred
before approval; exactly one controlled call occurred after it. Ten effects
completed, baseline browser/oracle failed as expected, candidate named test,
browser and oracle passed, both recordings became READY, and run ended
REVIEW_READY/PASSED. This remains synthetic controlled evidence. After the
SQLite migration, local Chromium loaded the Forge workspace with no page
errors or failed network responses.

2026-10-03 hosted prelaunch crash recovery: an expired PREPARING dispatch now
requeues only if it has no sandbox lease of any state, no tool action and fewer
than three delivery attempts. It claims a new fence, clears ownership and
records the state change before the existing dispatch becomes pending again.
The controlled coordinator test replayed such a run through baseline and
candidate guests to REVIEW_READY; a PREPARING run with an INTENDED model action
still closed FAILED without replay. Hosted worker tests passed 3/3; scoped
Ruff and diff whitespace checks passed. The full Python suite passed 287 with
2 Windows symlink skips with PostgreSQL and Memgraph gates enabled. Active
sandbox and later-phase crash replay remain blocked pending reconciliation.

2026-10-03 component status: the owner Operations summary now reports seven
separate components from authenticated API/database observation, persisted
tenant-scoped queue age, local configuration and current non-fixture model
qualification. Unprobed service reachability is explicitly UNVERIFIED;
unconfigured components are DISABLED or LOCAL_ONLY. Focused API tests passed,
the full suite passed 287 with two Windows symlink skips (PostgreSQL and
Memgraph gates enabled), Ruff, TypeScript and Vite production build passed.
The API on 8098 was restarted, and headless Chromium rendered all seven rows
without page errors; the initial inline label spacing was fixed and checked.

2026-10-03 sandbox-minute quota: migration `f89e4d70ab12` adds a default
120-minute daily tenant cap and durable sandbox lease reservation/settlement
fields. The hosted broker now checks the cap under a tenant row lock before
S3/EC2, charges one lease once across replay, warns at 80%, and settles elapsed
seconds only after EC2 confirms termination. Missing launch timestamps are
charged conservatively. The owner Settings/API and trusted operator CLI expose
the cap, and Operations shows the daily liability. Local PostgreSQL upgraded
without Alembic drift; its disposable two-run quota race admitted exactly one
30-minute lease. The local SQLite API database was backed up, upgraded and
restarted on 8098. The UI showed the new 120-minute field and operations
metric without page errors. Full Python suite: 288 passed, 2 Windows symlink
skips with PostgreSQL and Memgraph gates enabled; Ruff, TypeScript and Vite
production build passed. Media-minute and artifact-byte quotas remain.

2026-10-03 hosted media-minute quota: migration `a804e7b9c122` adds a default
120-minute daily cap and a tenant/run/label/attempt charge ledger. The hosted
media worker probes source duration, reserves seconds under a tenant row lock
before FFmpeg, marks completed/failed attempts, and recognizes an already
ready HLS encode with its charge so publication replay does not reserve again.
Failed or uncertain attempts keep liability; a fresh retry consumes another
reservation. The trusted CLI, owner Settings and Operations summary expose
the cap and usage. Local PostgreSQL upgraded with no Alembic drift; disposable
two-run media race admitted one 70-second charge at a 120-second cap. Local
SQLite API database was backed up, extended and restarted on 8098. Full suite
289 passed, 2 Windows symlink skips with PostgreSQL/Memgraph gates; Ruff,
TypeScript and Vite passed; Chromium showed the new field/metric with no page
errors. Artifact-byte quota remains.

2026-10-03 private media artifact-byte quota: migration `b902d4ef6a30` adds a
5 GB default tenant cap and a durable tenant/run/label charge. Hosted HLS
publication computes a digest-verified inventory, reserves exact object and
manifest bytes under the tenant row lock before S3 upload, activates the
charge after publication, and releases it only after S3 purge plus CloudFront
invalidation complete. Replay reuses the same charge; 80% usage creates an
audit warning. Owner Settings/Operations and trusted CLI expose cap and usage.
Local PostgreSQL upgraded with no Alembic drift, and a disposable concurrent
probe admitted exactly one 70-byte artifact at a 100-byte cap. The local SQLite
API database was backed up and upgraded, API readiness passed, and Chromium
showed the 5 GB field and metric without page errors. Full suite: 291 passed,
2 Windows symlink skips with PostgreSQL/Memgraph gates; Ruff, TypeScript and
Vite production build passed. The quota covers private HLS objects only; live
AWS publication and other artifact classes remain unverified/unmetered.

2026-10-03 hosted recorded-effect restart: stale hosted dispatches may requeue
PREPARING/REPRODUCING with no prior effect, or later active phases only when
all guest leases are terminated with canonical results and all tool actions
are completed. A new worker fence is claimed, and completed guest replay
rechecks the pinned source bundle, launch spec, old-fence S3 result and its
canonical digest/summary. A controlled post-baseline restart reached
REVIEW_READY while launching only the candidate guest; pending effects still
fail closed. Focused hosted tests, a tampered-receipt rejection, Ruff and the full
Python suite (291 passed, 2 Windows symlink skips with PostgreSQL/Memgraph
gates) passed. Live AWS restart and uncertain in-flight provider/guest outcomes
remain unverified.

2026-10-03 browser recording integrity: the fixture browser now records a
SHA-256 digest for each WebM beside the existing screenshot digest. Completed
browser action replay rejects a changed recording, and the media encoder
checks that digest before authorizing a new encode. Focused tamper tests passed.
The refreshed WSL code-only image `aip-dev-sandbox:0.1.3` completed controlled
provider budget pause/replay as run `2c68adf4-a172-4c38-aaed-4737f9198d44`:
zero provider calls before approval, one after, 10 completed effects,
REVIEW_READY/PASSED, and two READY recordings. The local development worker
was restarted with the refreshed image. This is synthetic controlled evidence,
not a live model or hosted customer-repository result. Full regression
suite passed 292 with 2 Windows symlink skips under PostgreSQL/Memgraph gates.

2026-10-03 repeatable local PostgreSQL restore: new
`scripts.verify_local_postgres_restore` creates and drops a uniquely named
local disposable database, exports a repeatable-read snapshot, restores it
transactionally, compares all public table counts and migration revision,
checks event sequences/foreign keys and Alembic drift, and replays restored
recording tombstones against synthetic local media. Latest run at head
`b902d4ef6a30` passed 32 table counts, zero sequence mismatches, no drift,
and two of two synthetic media removals; local structural restore elapsed
4.56 seconds. This does not establish hosted backup age, RPO, RTO or failover.

2026-10-03 private HLS publication recovery and playlist scope: the publisher
now rejects external/missing HLS references, unknown URI tags and unreferenced
variants or segments before S3 upload. Actual baseline/candidate FFmpeg HLS
from the controlled WSL run passed validation. A fake-S3 failure after two
objects left no DB publication and one reserved byte charge; a second simulated
crash after all S3 objects but before DB commit still left no publication.
Replay verified existing objects and committed one publication without another
byte reservation. The refreshed sandbox image security probe passed all seven
local containment checks. Live S3/CDN behavior remains unverified.

2026-10-03 local playback observability: the React HLS player now shows
actual decoded rendition height, time from Play to the first rendered video
frame, and post-first-frame stall time. It listens for native video resize
and hls.js rendition/fragment changes. Local Chromium played an admitted
fixture recording at 360p, measured 111 ms to first frame and showed no page
errors; at 390 px the player had no horizontal overflow. These are local
observations, not CDN or bandwidth-adaptation qualification.

2026-10-03 visible UI end-to-end recheck after image refresh: Chromium used
`http://127.0.0.1:5173/` to admit synthetic run
`78286c44-53c1-41f3-9b4b-6dfc4f5a2c00`. The polling WSL worker completed
four effects and 17 durable events, reproduced the form bug, and closed
INCONCLUSIVE without a qualified provider. Media reached READY; the local WebM
SHA-256 matched its browser action receipt. Chromium played the seven-second
HLS at 360p and observed 230 ms to first frame with no page or server errors.
This is a fixture-only flow and does not qualify live model repair.

2026-10-03 current deployment-image check: WSL Docker built the trusted
control-plane image from revision `c74dbb60aaf1c778ce21b08fd459f242e948f9c5`
as `sha256:d74d049c419e8bc754047231f462f2a27c92a82d9ba94487cae44116e7ba1885`.
An unprivileged, read-only, capability-dropped smoke container passed internal
health and readiness 200. The media image built as
`sha256:c38b984b501b1580bfaa53b63c17051191f0ee8d7df60eee0c858da0bb380d26`.
Under no-network, read-only, capability-dropped flags it imported the worker
and encoded the fresh UI run's real WebM through `encode_hls`, producing a
master playlist. Neither image has been pushed or hosted; the AWS queue,
OIDC, EFS, Memgraph server, CDN and provider accounts remain unqualified.

2026-10-03 hosted trace continuity: the run SQS consumer now starts its
`dispatch.consume` span from the canonical outbox trace, and media staging
stores the active trace in its durable media outbox. The media SQS consumer
starts `media.consume` from that parent. Both hosted worker entry points enable
the configured OTLP exporter; no endpoint is assumed or credentials logged.
The focused lineage and media replay tests passed, Ruff passed, and the full
PostgreSQL/Memgraph-enabled regression suite passed 293 with two Windows
symlink skips. Actual collector delivery and hosted trace correlation are not
yet qualified.

2026-10-03 deployment images after trace continuity: revision
`70aa0e12a7f3966d4d947c53c47bbe922bb3e37a` rebuilt as control-plane
`sha256:07a2fb0d38470c8a97f320b2f5e3c7b59c78fba6d1681e0ae4b9c70b7fa7daa4`
and media
`sha256:d6683e7550fb5073eaa51a1c7621a63f5623fabc2d0d8548826d968612234f41`.
The control image imported API and hosted consumer modules as non-root under
read-only/no-network/capability-dropped flags. The media image completed an
actual `encode_hls` of the UI run WebM under the same flags with a writable
tmpfs. These are local image checks; neither image was pushed or hosted.

2026-10-03 OTLP export check: a separate Python process configured the
development loopback OTLP endpoint, emitted `hosted.export.smoke`, flushed its
batch processor, and a controlled local HTTP receiver parsed the protobuf
request and found exactly that span. `tests/test_telemetry.py` passed 4/4.
This verifies local export wiring, not a hosted collector or correlated load.

2026-10-03 dialog keyboard behavior: React project, report and run dialogs now
focus the first form field on open, trap Tab and Shift+Tab within visible
controls, close with Escape and restore focus to the opening control (or main
landmark if it is gone). The background is inert and hidden from assistive
technology while a dialog is open. TypeScript and Vite production build passed.
Chromium verified all three dialogs, including the project dialog at 390 px,
with zero page errors. A full accessibility audit remains.

2026-10-03 disposable PostgreSQL worker end-to-end: a verifier wrapper creates
one randomly named `aip_verify_*` database, migrates it, runs the controlled
fixture worker with WSL containers, then drops the database. Normal repair,
input resume (`877391d5-f5a2-43b6-b90c-45b84a1b06e8`) and budget approval
resume (`a41e38d8-572c-4ef7-9f9f-8398bd924a8b`) reached
REVIEW_READY/PASSED, ten effects, two READY media recordings and delivered
outbox rows. The budget run paused before provider HTTP, and the approved
resume made one controlled provider request. The run exposed a canonical state
bug: `process_next` reset resumed runs to PREPARING while the graph restarted
at its saved patch stage. It now restores only the approved saved active stage;
the transition guard requires a recorded resume key and matching target. A
focused regression test covers this. No live model, AWS or customer repository
was used.
The final PostgreSQL/Memgraph-enabled Python suite passed 295 with two Windows
symlink skips; focused resume tests passed 18/18, and Ruff passed.
