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
repair evidence. Non-development startup fails closed until hosted identity
and roles exist.

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
commit `16ba81e`; PASS/FAIL/FAIL baseline and INCONCLUSIVE run. Tests: 42 passed,
1 Windows symlink skip; ruff clean.
