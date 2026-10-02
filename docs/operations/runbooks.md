# Operations response runbooks

These runbooks describe the intended response for the reference deployment.
The current checkout runs locally. It has no external pager, hosted sandbox,
CDN, or qualified restore region. A local operational evaluator persists queue,
graph and budget-breach alert state; the other catalog conditions need their
own evidence producers. An owner verifies the tenant, run, revision and
time window before taking action. Keep restricted evidence out of ordinary
chat, dashboards and tickets. The machine-readable owner and impact catalog is
`platform_app/ops_alerts.py`.

Run the evaluator as a supervised process with
`python -m scripts.evaluate_operations --serve --poll-seconds 30`. Queue
warnings require samples no more than 90 seconds apart for ten minutes after
crossing five minutes of queue age. A stopped evaluator cannot prove continuous
failure. Firing alerts appear in the owner Operations view; resolving one
requires a recorded reason. A new budget-breach event reopens a resolved alert.
There is no external paging delivery yet, so operators must inspect the view.

## Security incident

**Owner:** security on-call for tenant access, secrets and media edge; platform
on-call for uncontrolled tool effects. **Impact:** cross-tenant disclosure,
credential misuse, an unapproved effect or unauthorized recording delivery.

1. Disable the affected API capability, model route, plugin version,
   publication command or media grant at its policy boundary. Stop new affected
   work before retrying anything.
2. Preserve the run event range, audit records, effect intent/receipt, trace ID,
   artifact hashes and access logs in restricted storage. Do not copy raw
   credentials or customer content into a general incident channel.
3. Determine tenant, project, run, actor and artifact scope from canonical
   PostgreSQL records. Revoke exposed credentials and grants at their issuer.
4. Verify that no stale graph/cache result or media URL remains accessible.
   Reopen the capability only after an independent authorization retest.

## Provider unavailable

**Owner:** model on-call. **Impact:** new model calls fail or an in-flight call
has unknown usage and possible spend liability.

1. Check provider status and the exact model/version response. Stop blind
   retries of calls with uncertain effects; preserve the request ID and usage
   receipt status.
2. Mark the model route unavailable. Use an alternate only if the tenant policy
   and current qualification explicitly allow it. Otherwise pause the run and
   release its worker/sandbox capacity.
3. Retain a conservative budget reservation until final provider accounting or
   verified cancellation. Resume from the checkpoint with original policy or a
   stricter policy and preserve provider-scoped continuation state.

## Budget breach

**Owner:** platform on-call. **Impact:** a tenant may be charged beyond its
authorized daily, monthly or run ceiling.

1. Pause admission and new model calls for the affected tenant while keeping
   read-only review and evidence access available.
2. Reconcile each reservation against the provider request ID and final usage
   receipt. Preserve unknown liability rather than marking it free, and check
   that retries consumed the same cap.
3. Identify the affected runs and period, correct the ledger only with an
   audited compensating entry, and obtain tenant approval before reopening
   spend. Review routing and pricing revision before re-enabling models.

## Worker crash

**Owner:** platform on-call. **Impact:** a run may be stranded or a tool effect
may have an uncertain outcome.

1. Inspect the canonical run state, lease owner/fence, last checkpoint, outbox
   dispatch and tool intents. Do not infer success from worker process exit.
2. Wait for or expire the old lease, then reconcile each uncertain effect from
   its remote receipt or deterministic marker. Never reissue an external write
   merely because its local receipt is missing.
3. Restore the compatible checkpoint and redispatch with a new fence. Confirm
   only one owner can write run events. Retain partial evidence if recovery is
   inconclusive.

## Sandbox failure

**Owner:** sandbox on-call. **Impact:** untrusted code execution may be stuck or
the review may lack test/browser evidence.

1. Stop new work for the affected guest, capture its sandbox ID and bounded
   logs, and mark the run inconclusive if verification cannot be established.
2. Terminate the guest and revoke credentials/network grants. Verify cleanup of
   the guest, volume and orphan process after the grace period.
3. Retry only in a clean guest with the pinned repository commit and image
   digest, within the same run budget and retry bound. Do not execute customer
   code on the API host or in the development fixture container.

## Memory unavailable

**Owner:** platform on-call. **Impact:** connected retrieval is delayed; the
canonical PostgreSQL records remain authoritative.

1. Confirm the API reports `canonical_degraded` and still applies tenant,
   project, revision, status and validity checks. Disable graph retrieval if
   it produces inconsistent IDs.
2. Inspect `memory.project` outbox age and failures. Repair the Memgraph
   service, then stop the projection worker and rebuild each affected scope
   with `python -m scripts.project_memory_graph --rebuild-tenant TENANT_ID
   --rebuild-project PROJECT_ID`.
3. Restart projection and confirm pending events drain and canonical rechecks
   agree. Keep degraded status visible until the graph is current.

## Media failure

**Owner:** media on-call. **Impact:** recordings may be missing or delayed while
code verification remains independently reviewable.

1. Keep the review packet, screenshots, timeline and tests available. Mark
   media as failed or pending; never turn a failed code verdict into success.
2. Validate the raw recording hash, encoder exit code, segment manifest and
   private origin policy. Retry transcoding only within its bounded policy.
3. Publish a complete HLS manifest atomically after segment checks, or retain
   the failure state and screenshots. Recheck recording deletion tombstones
   before reissuing any media grant.

## Database restore

**Owner:** database on-call. **Impact:** canonical execution, authorization or
deletion state may be unavailable or inconsistent.

1. Pause admission and external writes. Restore a PostgreSQL backup to an
   isolated environment; do not overwrite the active database in place.
2. Verify schema revision, tenant/project foreign keys, event sequence,
   run/lease consistency, budget and export ledgers, and audit retention.
3. Reapply recording/memory deletion ledgers before making artifacts reachable.
   Rebuild derived Memgraph scopes and caches from canonical PostgreSQL.
4. Run authorization and recovery checks, then reopen traffic gradually. The
   current repository has no qualified hosted restore drill.

## Publication ambiguity

**Owner:** platform on-call with project maintainer. **Impact:** an approved
draft PR might already exist even when the local call timed out.

1. Preserve the approved run, patch/test digest, base commit and durable
   `ToolAction` intent. Confirm approval has not expired or been revoked.
2. Query the deterministic branch and PR marker at the exact GitHub
   destination. Compare commit tree, parent and PR head/base against the
   approved binding. Do not create a second branch or PR blindly.
3. Record the matching remote receipt, or leave the effect uncertain for
   human resolution. Never promote a conflicting PR as a successful repair.
