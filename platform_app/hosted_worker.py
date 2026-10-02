"""Run one fenced customer repair through two isolated guest generations.

The API keeps hosted admission closed until the infrastructure is deployed and
qualified. This worker is the execution path behind that gate, not a shortcut
around it.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from platform_app.config import settings
from platform_app.db import SessionLocal, utcnow
from platform_app.environment_manifest import EnvironmentManifest
from platform_app.general_agent import request_general_patch
from platform_app.general_patch import build_candidate_tree
from platform_app.guest_comparison import compare_guest_observations
from platform_app.hosted_baseline import seal_and_collect_baseline, stage_and_launch_baseline
from platform_app.hosted_media import stage_hosted_recording
from platform_app.model_qualification import qualification_current
from platform_app.models import ModelEntry, OutboxEvent, Run, RunEvent, SandboxLease
from platform_app.providers import ProviderError
from platform_app.repository_fetch import fetch_authorized_run_source
from platform_app.run_ledger import assert_fence, aware, claim_run, heartbeat, transition
from platform_app.sandbox_broker import (
    SandboxSpec,
    revoke_sandbox,
    terminate_revoked_sandbox,
)
from platform_app.sandbox_bundle import build_guest_bundle
from platform_app.service import ServiceError, append_event, request_cancel


class HostedWorker:
    def __init__(
        self, spec: SandboxSpec, ec2, s3, bucket: str, envelope_key: bytes,
        work_root: Path, artifact_root: Path, *, session_factory=SessionLocal,
        provider=None, worker_id: str | None = None, poll_seconds: float = 2.0,
    ):
        if not bucket or len(envelope_key) != 32 or not 0 < poll_seconds <= 10:
            raise ValueError("Hosted worker configuration is incomplete")
        self.spec, self.ec2, self.s3 = spec, ec2, s3
        self.bucket, self.envelope_key = bucket, envelope_key
        self.work_root = work_root.resolve(strict=True)
        self.artifact_root = artifact_root.resolve()
        self.session_factory, self.provider = session_factory, provider
        self.worker_id = worker_id or f"hosted-{uuid4().hex[:12]}"
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._deadline: float | None = None

    def _heartbeat(self, run_id: str, fence: int) -> None:
        while not self._stop.wait(10):
            try:
                with self.session_factory() as db:
                    heartbeat(db, run_id, self.worker_id, fence, settings().lease_seconds)
                    if db.get(Run, run_id).cancel_requested:
                        self._lost.set()
                    db.commit()
            except (ServiceError, SQLAlchemyError):
                self._lost.set()
            if self._lost.is_set():
                return

    def _check(self, run_id: str, fence: int) -> None:
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise ServiceError("RUN_ACTIVE_TIMEOUT", "Active run time exceeded policy", 409)
        if self._lost.is_set():
            raise ServiceError("LEASE_LOST", "Hosted worker lost its lease", 409)
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            assert_fence(run, self.worker_id, fence)
            if run.cancel_requested:
                raise ServiceError("RUN_CANCELLED", "Run was cancelled", 409)

    def _state(self, run_id: str, fence: int, next_state: str, verdict=None) -> None:
        self._check(run_id, fence)
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            if run.state != next_state:
                transition(db, run, self.worker_id, fence, next_state, verdict)
                db.commit()

    def _guest(self, run_id: str, fence: int, source, phase: str):
        self._check(run_id, fence)
        with self.session_factory() as db:
            lease = stage_and_launch_baseline(
                db, run_id, self.worker_id, fence, source, self.spec,
                self.ec2, self.s3, self.bucket, self.envelope_key, phase=phase,
            )
            lease_id = lease.id
        deadline = min(
            time.monotonic() + self.spec.ttl_seconds,
            self._deadline or float("inf"),
        )
        try:
            while time.monotonic() < deadline:
                self._check(run_id, fence)
                with self.session_factory() as db:
                    result = seal_and_collect_baseline(
                        db, lease_id, self.worker_id, fence,
                        self.ec2, self.s3, self.bucket,
                    )
                if result is not None:
                    return result
                self._stop.wait(self.poll_seconds)
            raise ServiceError("SANDBOX_TIMEOUT", "Guest result did not arrive", 504)
        finally:
            with self.session_factory() as db:
                revoke_sandbox(db, lease_id, "closed")
            # A second VM may launch only after EC2 confirms termination.
            cleanup_deadline = time.monotonic() + 120
            while time.monotonic() < cleanup_deadline:
                with self.session_factory() as db:
                    if terminate_revoked_sandbox(db, lease_id, self.ec2):
                        break
                self._stop.wait(self.poll_seconds)
            else:
                raise ServiceError("SANDBOX_CLEANUP_PENDING", "VM termination is unconfirmed", 503)

    def _inputs(self, run_id: str, fence: int):
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            assert_fence(run, self.worker_id, fence)
            snapshot = run.config_snapshot or {}
            if snapshot.get("execution_profile") != "hosted_vm_v1":
                raise ServiceError(
                    "EXECUTION_UNAVAILABLE", "Run lacks hosted execution policy", 409
                )
            model = db.get(ModelEntry, run.model_entry_id)
            if model is None or not qualification_current(model):
                raise ServiceError("MODEL_UNAVAILABLE", "Model has no current qualification", 409)
            try:
                manifest = EnvironmentManifest.model_validate(snapshot["environment_manifest"])
                paths = snapshot["repair_paths"]
            except (KeyError, TypeError, ValueError) as error:
                raise ServiceError(
                    "ENVIRONMENT_UNAVAILABLE", "Run plan is incomplete", 409
                ) from error
            if (
                not isinstance(paths, list) or not 1 <= len(paths) <= 4
                or any(not isinstance(path, str) for path in paths)
                or len(set(paths)) != len(paths)
            ):
                raise ServiceError("REPAIR_SCOPE_INVALID", "Repair scope is invalid", 409)
            source = fetch_authorized_run_source(db, run_id, self.work_root)
        return source, manifest, frozenset(paths)

    def execute(self, run_id: str) -> str:
        with self.session_factory() as db:
            run, fence = claim_run(db, run_id, self.worker_id, settings().lease_seconds)
            if run.state != "QUEUED":
                raise ServiceError("RUN_NOT_EXECUTABLE", "Hosted dispatch must start queued", 409)
            requested_timeout = (run.config_snapshot or {}).get(
                "active_timeout_seconds", settings().active_timeout_seconds
            )
            if type(requested_timeout) is not int or requested_timeout < 30:
                raise ServiceError("RUN_TIMEOUT_INVALID", "Run active timeout is invalid", 409)
            transition(db, run, self.worker_id, fence, "PREPARING")
            db.commit()
        self._stop.clear()
        self._lost.clear()
        self._deadline = time.monotonic() + min(
            requested_timeout, settings().active_timeout_seconds
        )
        watcher = threading.Thread(target=self._heartbeat, args=(run_id, fence), daemon=True)
        watcher.start()
        try:
            source, manifest, paths = self._inputs(run_id, fence)
            self._state(run_id, fence, "REPRODUCING")
            baseline = self._guest(run_id, fence, source, "baseline")
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                stage_hosted_recording(
                    db, run, self.worker_id, fence,
                    baseline, "baseline", self.artifact_root,
                )
                db.commit()
            checks = (baseline.result.get("baseline") or {}).get("browser") or {}
            if baseline.result.get("guest_exit_code") != 0 or checks.get("status") != "FAILED":
                self._state(run_id, fence, "INCONCLUSIVE", "INCONCLUSIVE")
                return "INCONCLUSIVE"
            self._state(run_id, fence, "INVESTIGATING")
            with self.session_factory() as db:
                snapshot = db.get(Run, run_id).config_snapshot
                max_attempts = snapshot.get("max_patch_attempts", 1)
            if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
                raise ServiceError("PATCH_ATTEMPT_INVALID", "Repair attempt cap is invalid", 409)
            feedback: tuple[dict, ...] = ()
            seen_trees: set[str] = set()
            manifest_sha = build_guest_bundle(source, manifest).manifest_sha256
            for attempt in range(1, max_attempts + 1):
                proposal = request_general_patch(
                    self.session_factory, run_id, self.worker_id, fence,
                    source, baseline, paths, self.artifact_root,
                    provider=self.provider, attempt=attempt, feedback=feedback,
                ).proposal
                if not proposal.files:
                    stop = "INCONCLUSIVE" if attempt == 1 else "FAILED"
                    self._state(run_id, fence, stop, "INCONCLUSIVE")
                    return "INCONCLUSIVE"
                if attempt == 1:
                    self._state(run_id, fence, "PATCHING")
                candidate = build_candidate_tree(source, proposal, self.work_root)
                if candidate.tree_sha256 in seen_trees:
                    with self.session_factory() as db:
                        run = db.get(Run, run_id)
                        assert_fence(run, self.worker_id, fence)
                        append_event(db, run, "run.loop_detected", {
                            "attempt": attempt,
                            "candidate_tree_sha256": candidate.tree_sha256,
                        })
                        db.commit()
                    self._state(run_id, fence, "FAILED", "INCONCLUSIVE")
                    return "INCONCLUSIVE"
                seen_trees.add(candidate.tree_sha256)
                self._state(run_id, fence, "VERIFYING")
                observed = self._guest(run_id, fence, candidate.source, "candidate")
                comparison = compare_guest_observations(
                    baseline, observed,
                    manifest_sha256=manifest_sha,
                    candidate_tree_sha256=candidate.tree_sha256,
                )
                packet = {
                    "scope": "declared_guest_checks", "attempt": attempt,
                    "status": comparison.status, "reason": comparison.reason,
                    "baseline_checks": comparison.baseline_checks,
                    "candidate_checks": comparison.candidate_checks,
                    "patch_sha256": proposal.patch_sha256,
                    "candidate_tree_sha256": candidate.tree_sha256,
                    "candidate_source_sha256": candidate.source.sha256,
                    "baseline_lease_id": baseline.result["lease_id"],
                    "candidate_lease_id": observed.result["lease_id"],
                }
                with self.session_factory() as db:
                    run = db.get(Run, run_id)
                    assert_fence(run, self.worker_id, fence)
                    append_event(db, run, "verification.attempt", packet)
                    db.commit()
                if comparison.status == "FAILED" and attempt < max_attempts:
                    feedback += ({
                        "status": comparison.status, "reason": comparison.reason,
                        "candidate_checks": comparison.candidate_checks,
                        "patch_sha256": proposal.patch_sha256,
                        "candidate_tree_sha256": candidate.tree_sha256,
                    },)
                    self._state(run_id, fence, "PATCHING")
                    continue
                break
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                stage_hosted_recording(
                    db, run, self.worker_id, fence,
                    observed, "candidate", self.artifact_root,
                )
                db.commit()
            target = self.artifact_root / run_id / "candidate"
            target.mkdir(parents=True, exist_ok=True)
            raw = json.dumps({**packet, "diagnosis": proposal.diagnosis,
                              "diff": candidate.diff}, sort_keys=True).encode()
            packet_path = target / "guest-review.json"
            temporary = target / ".guest-review.tmp"
            temporary.write_bytes(raw)
            os.replace(temporary, packet_path)
            packet["artifact_ref"] = f"{run_id}/candidate/guest-review.json"
            packet["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                assert_fence(run, self.worker_id, fence)
                prior = db.scalar(select(RunEvent.id).where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "verification.completed",
                    RunEvent.payload["scope"].as_string() == "declared_guest_checks",
                ))
                if prior is None:
                    append_event(db, run, "verification.completed", packet)
                transition(
                    db, run, self.worker_id, fence,
                    "REVIEW_READY" if comparison.status == "SUPPORTED" else
                    "FAILED" if comparison.status == "FAILED" else "INCONCLUSIVE",
                    "PASSED" if comparison.status == "SUPPORTED" else
                    "FAILED" if comparison.status == "FAILED" else "INCONCLUSIVE",
                )
                db.commit()
            return comparison.status
        except Exception as error:
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                if run and run.lease_owner == self.worker_id and run.lease_fence == fence:
                    active = db.scalars(select(SandboxLease).where(
                        SandboxLease.run_id == run_id,
                        SandboxLease.tenant_id == run.tenant_id,
                        SandboxLease.state.in_(["intended", "bootstrapping", "provisioned"]),
                    )).all()
                    for lease in active:
                        revoke_sandbox(db, lease.id, "closed")
                    db.refresh(run)
                    state = "CANCELLED" if run.cancel_requested else (
                        "INCONCLUSIVE" if run.state in {"REPRODUCING", "INVESTIGATING"}
                        else "FAILED"
                    )
                    transition(db, run, self.worker_id, fence, state, "INCONCLUSIVE")
                    append_event(db, run, "worker.error", {
                        "code": error.code if isinstance(error, (ServiceError, ProviderError))
                        else type(error).__name__,
                    })
                    db.commit()
            raise
        finally:
            self._stop.set()
            watcher.join(timeout=2)
            self._deadline = None

    def process_event(self, event_id: str) -> str | None:
        """Bind one SQS wakeup to its canonical outbox and scoped run."""
        with self.session_factory() as db:
            event = db.scalar(select(OutboxEvent).where(
                OutboxEvent.id == event_id,
                OutboxEvent.topic == "run.dispatch",
            ).with_for_update())
            if event is None or event.status != "pending":
                return None
            run = db.scalar(select(Run).where(
                Run.id == event.payload.get("run_id"),
                Run.tenant_id == event.tenant_id,
            ))
            if run is None:
                event.status = "failed"
                db.commit()
                return None
            if (run.config_snapshot or {}).get("execution_profile") != "hosted_vm_v1":
                return None
            if run.state in {
                "REVIEW_READY", "COMPLETED", "FAILED", "INCONCLUSIVE", "CANCELLED",
                "PAUSED_INPUT", "PAUSED_BUDGET", "PAUSED_APPROVAL",
            }:
                event.status = "delivered"
                db.commit()
                return run.id
            if run.state != "QUEUED":
                return None
            event.status = "processing"
            event.attempts += 1
            run_id = run.id
            db.commit()
        try:
            self.execute(run_id)
        except ServiceError as error:
            final = "pending" if error.code == "LEASE_HELD" else "failed"
        except Exception:
            final = "failed"
        else:
            final = "delivered"
        with self.session_factory() as db:
            event = db.get(OutboxEvent, event_id)
            event.status = final
            db.commit()
        return run_id

    def recover_stale(self, limit: int = 100) -> int:
        """Close an expired hosted attempt without repeating uncertain effects."""
        recovered = 0
        with self.session_factory() as db:
            events = db.scalars(select(OutboxEvent).where(
                OutboxEvent.topic == "run.dispatch",
                OutboxEvent.status == "processing",
            ).order_by(OutboxEvent.created_at).with_for_update(skip_locked=True)
                .limit(limit)).all()
            for event in events:
                run = db.scalar(select(Run).where(
                    Run.id == event.payload.get("run_id"),
                    Run.tenant_id == event.tenant_id,
                ).with_for_update().execution_options(populate_existing=True))
                if run is None:
                    event.status = "failed"
                    recovered += 1
                    continue
                if (run.config_snapshot or {}).get("execution_profile") != "hosted_vm_v1":
                    continue
                if run.lease_until and aware(run.lease_until) > utcnow():
                    continue
                if run.state in {
                    "REVIEW_READY", "COMPLETED", "FAILED", "INCONCLUSIVE", "CANCELLED",
                    "PAUSED_INPUT", "PAUSED_BUDGET", "PAUSED_APPROVAL",
                }:
                    event.status = "delivered"
                    recovered += 1
                    continue
                active = db.scalars(select(SandboxLease).where(
                    SandboxLease.run_id == run.id,
                    SandboxLease.tenant_id == run.tenant_id,
                    SandboxLease.state.in_(["intended", "bootstrapping", "provisioned"]),
                )).all()
                for lease in active:
                    revoke_sandbox(db, lease.id, "expired")
                db.refresh(run)
                if run.cancel_requested:
                    request_cancel(db, run, self.worker_id)
                elif run.state == "QUEUED":
                    event.status = "pending"
                    recovered += 1
                    continue
                else:
                    owned, fence = claim_run(
                        db, run.id, self.worker_id, settings().lease_seconds
                    )
                    final = "INCONCLUSIVE" if owned.state in {
                        "REPRODUCING", "INVESTIGATING"
                    } else "FAILED"
                    transition(db, owned, self.worker_id, fence, final, "INCONCLUSIVE")
                    append_event(db, owned, "worker.error", {
                        "code": "EFFECT_OUTCOME_UNKNOWN", "replay_blocked": True,
                    })
                event.status = "failed"
                recovered += 1
            db.commit()
        return recovered
