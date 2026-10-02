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
