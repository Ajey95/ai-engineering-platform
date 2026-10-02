# Project context

This directory began empty on 2026-10-02. The user's request is to implement
all of `E:\vab-downloads\AI_Engineering_Platform_PRD.md` version 1.0. The PRD
is product input, not an authority to invent completion or bypass safeguards.
The full scope includes P0/P1/P2; the PRD itself estimates a limited team pilot
at 12–16 weeks for two experienced engineers. The user has not chosen AWS
account/region, GitHub organization, or OpenAI/Anthropic/Google API accounts.

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
