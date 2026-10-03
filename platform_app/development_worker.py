"""Execute the trusted synthetic fixture from a pinned platform commit.

This worker is deliberately limited to development fixtures. It never checks
out a customer repository or treats the baseline as an autonomous repair.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from platform_app.action_policy import (
    FIXTURE_VERSION,
    MEDIA_VERSION,
    PATCH_VERSION,
    ActionIntent,
    authorize_run_effect,
)
from platform_app.agent_patch import request_fixture_patch
from platform_app.code_navigation import SnapshotNavigator
from platform_app.config import settings
from platform_app.db import SessionLocal, utcnow
from platform_app.dev_sandbox import (
    SandboxError,
    _docker_prefix,
    run_browser_fixture,
    run_verifier_fixture,
)
from platform_app.fixture_workflow import build_fixture_workflow, invoke_fixture_workflow
from platform_app.media import MediaError, encode_hls, sha256_file
from platform_app.model_qualification import qualification_for_pinned_run
from platform_app.models import ModelEntry, OutboxEvent, Run, RunEvent, ToolAction
from platform_app.patch_workspace import PatchError, PatchProposal, materialize_candidate
from platform_app.providers import ProviderError
from platform_app.run_ledger import (
    assert_fence,
    aware,
    claim_run,
    complete_tool_action,
    heartbeat,
    transition,
)
from platform_app.safe_archive import UnsafeArchive, extract_regular_tar
from platform_app.service import ServiceError, append_event, request_cancel
from platform_app.telemetry import (
    configure_telemetry,
    extract_trace,
    set_safe_attributes,
    tracer,
)
from platform_app.verifier import tree_hash

FIXTURE_PATH = "benchmarks/fixtures/form-submit"
ORACLE_PATH = "benchmarks/oracles/form-submit-001/test_hidden.py"
TRUSTED_GIT_OBJECTS = {
    f"{FIXTURE_PATH}/base": "cd325d9eccb7aa86aa77c5cc5ca5a06bbf336db5",
    f"{FIXTURE_PATH}/manifest.json": "ee94c5e5edaa8ba68d97efca92ceef6937e23539",
    ORACLE_PATH: "85c0e5c6c29cd7391bc3c0b2a2b4044ef7c36587",
}


def _verified_receipt(step: str, target: Path, receipt: dict) -> dict:
    """A replay may trust a completed effect only while its artifacts agree."""
    kind = step.removeprefix("candidate_")
    result_name = {
        "named": "test-baseline.json",
        "browser": "result.json",
        "oracle": "oracle.json",
    }[kind]
    result_path = target / result_name
    try:
        if json.loads(result_path.read_text(encoding="utf-8")) != receipt:
            raise ValueError("Receipt artifact differs from the ledger")
        for key in ("output_file", "final_screenshot", "recording"):
            name = receipt.get(key)
            if name:
                if Path(name).name != name:
                    raise ValueError("Receipt artifact path is not local")
                artifact = target / name
                if not artifact.is_file():
                    raise ValueError("Receipt artifact is missing")
                if key == "output_file" and (
                    hashlib.sha256(artifact.read_bytes()).hexdigest()
                    != receipt.get("output_sha256")
                ):
                    raise ValueError("Receipt output hash differs")
                if key == "final_screenshot" and (
                    hashlib.sha256(artifact.read_bytes()).hexdigest()
                    != receipt.get("screenshot_sha256")
                ):
                    raise ValueError("Screenshot digest differs")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ServiceError(
            "EFFECT_OUTCOME_UNKNOWN", "Completed effect evidence is unavailable", 409
        ) from error
    return receipt


def _pinned_fixture(repository: Path, commit: str, destination: Path) -> tuple[Path, Path, Path]:
    """Extract the fixture and trusted oracle from the requested Git commit."""
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ServiceError("FIXTURE_UNAVAILABLE", "Pinned fixture commit is invalid", 409)
    for path, expected in TRUSTED_GIT_OBJECTS.items():
        revision = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", f"{commit}:{path}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if revision.returncode or revision.stdout.strip() != expected:
            raise ServiceError("FIXTURE_UNAVAILABLE", "Pinned fixture revision is untrusted", 409)
    archive = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "archive",
            "--format=tar",
            commit,
            FIXTURE_PATH,
            ORACLE_PATH,
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if archive.returncode:
        raise ServiceError("FIXTURE_UNAVAILABLE", "Pinned fixture commit is unavailable", 409)
    allowed_parents = {
        "benchmarks", "benchmarks/fixtures", FIXTURE_PATH,
        "benchmarks/oracles", "benchmarks/oracles/form-submit-001",
    }
    try:
        extract_regular_tar(
            archive.stdout, destination,
            permitted=lambda name: (
                name in allowed_parents
                or name.startswith(FIXTURE_PATH + "/")
                or name == ORACLE_PATH
            ),
        )
    except UnsafeArchive as error:
        raise ServiceError(
            "FIXTURE_UNSAFE", "Fixture archive contains an unsafe entry", 409
        ) from error
    root = destination / FIXTURE_PATH
    return root / "manifest.json", root / "base", destination / ORACLE_PATH


class DevelopmentWorker:
    def __init__(
        self,
        repository: Path,
        artifact_root: Path,
        worker_id: str | None = None,
        runtime: str = "wsl",
        image: str = "aip-dev-sandbox:0.1.0",
        session_factory=SessionLocal,
        patch_provider=None,
    ):
        if settings().environment != "development":
            raise RuntimeError("Synthetic fixture worker is development only")
        self.repository = repository.resolve(strict=True)
        self.artifact_root = artifact_root.resolve()
        self.worker_id = worker_id or f"dev-worker-{uuid4().hex[:12]}"
        self.runtime = runtime
        self.image = image
        self.session_factory = session_factory
        self.patch_provider = patch_provider
        self._stop = threading.Event()
        self._cancelled = threading.Event()
        self._active_container: str | None = None

    def _kill_active_container(self) -> None:
        name = self._active_container
        if name is None:
            return
        try:
            subprocess.run(
                [*_docker_prefix(self.runtime, "Ubuntu-24.04"), "kill", name],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _heartbeat(self, run_id: str, fence: int) -> None:
        while not self._stop.wait(10):
            try:
                with self.session_factory() as db:
                    heartbeat(db, run_id, self.worker_id, fence, settings().lease_seconds)
                    run = db.get(Run, run_id)
                    cancelled = bool(run and run.cancel_requested)
                    db.commit()
                if cancelled:
                    self._cancelled.set()
                    self._kill_active_container()
                    return
            except (ServiceError, OSError, SQLAlchemyError):
                self._cancelled.set()
                self._kill_active_container()
                return

    def _transition(self, run_id: str, fence: int, state: str, verdict: str | None = None) -> None:
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            transition(db, run, self.worker_id, fence, state, verdict)
            db.commit()

    @tracer.start_as_current_span("tool.execute")
    def _action(
        self,
        run_id: str,
        fence: int,
        step: str,
        manifest: Path,
        workspace: Path,
        target: Path,
        oracle: Path | None = None,
    ) -> dict:
        set_safe_attributes(run_id=run_id, tool_step=step)
        if self._cancelled.is_set():
            raise ServiceError("RUN_CANCELLED", "Worker observed cancellation", 409)
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            arguments = {
                "fixture_case_id": "form-submit-001",
                "base_commit": run.base_commit,
                "workspace_tree_sha256": tree_hash(workspace),
                "step": step,
            }
            action = authorize_run_effect(
                db,
                run,
                self.worker_id,
                fence,
                ActionIntent(
                    step,
                    f"fixture.{step}",
                    "isolated_execution",
                    "form-submit-001",
                    FIXTURE_VERSION,
                    arguments,
                ),
            )
            if action.status == "COMPLETED":
                return _verified_receipt(step, target, action.receipt)
            db.commit()  # Persist effect intent before starting the container.
            action_id = action.id

        name = f"aip-dev-{uuid4().hex[:12]}"
        self._active_container = name
        try:
            kind = step.removeprefix("candidate_")
            if kind == "browser":
                receipt = run_browser_fixture(
                    self.image,
                    workspace,
                    manifest,
                    target,
                    name,
                    runtime=self.runtime,
                )
            else:
                receipt = run_verifier_fixture(
                    self.image,
                    workspace,
                    manifest,
                    target,
                    name,
                    mode="oracle" if kind == "oracle" else "named",
                    oracle=oracle,
                    runtime=self.runtime,
                )
        finally:
            self._active_container = None
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            action = db.get(ToolAction, action_id)
            complete_tool_action(db, run, self.worker_id, fence, action, receipt)
            db.commit()
        return receipt

    @tracer.start_as_current_span("workspace.patch")
    def _materialize_patch(
        self, run_id: str, fence: int, source: Path, proposal: PatchProposal
    ) -> Path:
        set_safe_attributes(run_id=run_id)
        destination = self.artifact_root / run_id / "candidate" / "workspace"
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            action = authorize_run_effect(
                db,
                run,
                self.worker_id,
                fence,
                ActionIntent(
                    "candidate_patch",
                    "fixture.patch",
                    "workspace_write",
                    "server.py",
                    PATCH_VERSION,
                    {
                        "patch_sha256": proposal.patch_sha256,
                        "baseline_tree_sha256": tree_hash(source),
                    },
                ),
            )
            if action.status == "COMPLETED":
                if not destination.is_dir() or tree_hash(destination) != (action.receipt or {}).get(
                    "candidate_tree_sha256"
                ):
                    raise ServiceError("EFFECT_OUTCOME_UNKNOWN", "Candidate tree changed", 409)
                return destination
            db.commit()
            action_id = action.id
        destination.parent.mkdir(parents=True, exist_ok=True)
        candidate_hash = materialize_candidate(source, destination, proposal)
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            action = db.get(ToolAction, action_id)
            complete_tool_action(
                db,
                run,
                self.worker_id,
                fence,
                action,
                {
                    "status": "COMPLETED",
                    "patch_sha256": proposal.patch_sha256,
                    "changed_files": [name for name, _ in proposal.files],
                    "candidate_tree_sha256": candidate_hash,
                    "diagnosis_hypothesis": proposal.diagnosis,
                    "provider_mode": "controlled_test" if self.patch_provider else "native_api",
                },
            )
            db.commit()
        return destination

    @tracer.start_as_current_span("media.publish")
    def _encode_media(
        self, run_id: str, fence: int, label: str, browser_receipt: dict, evidence: Path
    ) -> None:
        set_safe_attributes(run_id=run_id, media_side=label)
        recording = browser_receipt.get("recording")
        if not isinstance(recording, str) or Path(recording).name != recording:
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                assert_fence(run, self.worker_id, fence)
                run.media_status = "FAILED"
                append_event(
                    db,
                    run,
                    "artifact.failed",
                    {
                        "label": label,
                        "reason": "recording_missing",
                    },
                )
                db.commit()
            return
        source = evidence / recording
        if not source.is_file():
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                assert_fence(run, self.worker_id, fence)
                run.media_status = "FAILED"
                append_event(
                    db,
                    run,
                    "artifact.failed",
                    {
                        "label": label,
                        "reason": "recording_missing",
                    },
                )
                db.commit()
            return
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            action = authorize_run_effect(
                db,
                run,
                self.worker_id,
                fence,
                ActionIntent(
                    f"media_{label}",
                    "media.encode",
                    "private_artifact_write",
                    run.id,
                    MEDIA_VERSION,
                    {"recording_sha256": sha256_file(source), "profile_revision": MEDIA_VERSION},
                ),
            )
            if action.status == "COMPLETED":
                return
            db.commit()
            action_id, tenant_id = action.id, run.tenant_id
        try:
            master = encode_hls(
                source,
                self.artifact_root / "private-media",
                tenant_id,
                f"{run_id}_{label}",
            )
        except (MediaError, OSError, subprocess.TimeoutExpired) as error:
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                assert_fence(run, self.worker_id, fence)
                action = db.get(ToolAction, action_id)
                complete_tool_action(
                    db,
                    run,
                    self.worker_id,
                    fence,
                    action,
                    {
                        "status": "FAILED",
                        "label": label,
                        "reason": type(error).__name__,
                    },
                )
                run.media_status = "FAILED"
                append_event(
                    db,
                    run,
                    "artifact.failed",
                    {
                        "label": label,
                        "reason": type(error).__name__,
                    },
                )
                db.commit()
            return
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            action = db.get(ToolAction, action_id)
            complete_tool_action(
                db,
                run,
                self.worker_id,
                fence,
                action,
                {
                    "status": "READY",
                    "label": label,
                    "master": str(master.relative_to(self.artifact_root)),
                },
            )
            media_actions = db.scalars(
                select(ToolAction).where(
                    ToolAction.run_id == run_id,
                    ToolAction.logical_action == "media.encode",
                    ToolAction.id != action_id,
                    ToolAction.status == "COMPLETED",
                )
            ).all()
            run.media_status = (
                "FAILED"
                if any((item.receipt or {}).get("status") == "FAILED" for item in media_actions)
                else "READY"
            )
            append_event(db, run, "artifact.ready", {"label": label, "kind": "hls"})
            db.commit()

    @contextmanager
    def _fixture(self, commit: str):
        with tempfile.TemporaryDirectory(prefix="aip-pinned-fixture-") as temporary:
            manifest, workspace, oracle = _pinned_fixture(
                self.repository, commit, Path(temporary)
            )
            source = json.loads(manifest.read_text(encoding="utf-8"))
            if source.get("case_id") != "form-submit-001":
                raise ServiceError("FIXTURE_UNAVAILABLE", "Pinned fixture identity changed", 409)
            yield manifest, workspace, oracle

    def _transition_when(
        self, run_id: str, fence: int, current_states: set[str], next_state: str,
        verdict: str | None = None,
    ) -> None:
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            assert_fence(run, self.worker_id, fence)
            if run.state in current_states:
                transition(db, run, self.worker_id, fence, next_state, verdict)
                db.commit()

    @tracer.start_as_current_span("workflow.baseline")
    def baseline_stage(self, run_id: str, fence: int, commit: str) -> bool:
        with self._fixture(commit) as (manifest, workspace, oracle):
            target = self.artifact_root / run_id / "baseline"
            target.mkdir(parents=True, exist_ok=True)
            self._transition_when(run_id, fence, {"PREPARING"}, "REPRODUCING")
            named = self._action(run_id, fence, "named", manifest, workspace, target)
            browser = self._action(run_id, fence, "browser", manifest, workspace, target)
            self._encode_media(run_id, fence, "baseline", browser, target)
            oracle_receipt = self._action(
                run_id, fence, "oracle", manifest, workspace, target, oracle
            )
            reproduced = (
                named["status"] == "PASSED"
                and browser["status"] == "FAILED"
                and oracle_receipt["status"] == "FAILED"
            )
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            assert_fence(run, self.worker_id, fence)
            prior_verification = db.scalar(
                select(RunEvent.id)
                .where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "verification.completed",
                )
                .limit(1)
            )
            if prior_verification is None:
                append_event(
                    db, run, "verification.completed",
                    {
                        "scope": "synthetic_baseline_only",
                        "reproduced": reproduced,
                        "candidate_status": "NOT_RUN",
                    },
                )
            db.commit()
        if reproduced:
            self._transition_when(run_id, fence, {"REPRODUCING"}, "INVESTIGATING")
        else:
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                stop = "FAILED" if run.state in {"PATCHING", "VERIFYING"} else "INCONCLUSIVE"
            self._transition(run_id, fence, stop, "INCONCLUSIVE")
        return reproduced

    @tracer.start_as_current_span("workflow.qualify")
    def qualification_stage(self, run_id: str, fence: int) -> bool:
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            assert_fence(run, self.worker_id, fence)
            model = db.get(ModelEntry, run.model_entry_id)
            qualified = bool(
                model
                and (
                    qualification_for_pinned_run(model)
                    or (
                        model.state in {"enabled", "deprecated"}
                        and model.validated_at
                        and (model.capabilities or {}).get("controlled_provider_fixture") is True
                    )
                )
            )
            stop = "FAILED" if run.state in {"PATCHING", "VERIFYING"} else "INCONCLUSIVE"
        if not qualified:
            self._transition(run_id, fence, stop, "INCONCLUSIVE")
        return qualified

    @tracer.start_as_current_span("workflow.patch")
    def patch_stage(self, run_id: str, fence: int, commit: str) -> str:
        with self._fixture(commit) as (manifest, workspace, oracle):
            target = self.artifact_root / run_id / "baseline"
            named = self._action(run_id, fence, "named", manifest, workspace, target)
            browser = self._action(run_id, fence, "browser", manifest, workspace, target)
            oracle_receipt = self._action(
                run_id, fence, "oracle", manifest, workspace, target, oracle
            )
            patch = request_fixture_patch(
                self.session_factory,
                run_id,
                self.worker_id,
                fence,
                {"named": named, "browser": browser, "oracle": oracle_receipt},
                SnapshotNavigator(workspace, commit).read_excerpt(
                    "server.py", max_lines=400
                )["text"],
                self.artifact_root,
                provider=self.patch_provider,
            )
            self._transition_when(
                run_id, fence, {"INVESTIGATING"}, "PATCHING"
            )
            self._materialize_patch(run_id, fence, workspace, patch.proposal)
            return patch.proposal.patch_sha256

    @tracer.start_as_current_span("workflow.verify")
    def verification_stage(self, run_id: str, fence: int, commit: str) -> bool:
        with self._fixture(commit) as (manifest, workspace, oracle):
            baseline_target = self.artifact_root / run_id / "baseline"
            named = self._action(run_id, fence, "named", manifest, workspace, baseline_target)
            browser = self._action(run_id, fence, "browser", manifest, workspace, baseline_target)
            oracle_receipt = self._action(
                run_id, fence, "oracle", manifest, workspace, baseline_target, oracle
            )
            patch = request_fixture_patch(
                self.session_factory,
                run_id,
                self.worker_id,
                fence,
                {"named": named, "browser": browser, "oracle": oracle_receipt},
                SnapshotNavigator(workspace, commit).read_excerpt(
                    "server.py", max_lines=400
                )["text"],
                self.artifact_root,
                provider=self.patch_provider,
            )
            candidate = self._materialize_patch(run_id, fence, workspace, patch.proposal)
            self._transition_when(run_id, fence, {"PATCHING"}, "VERIFYING")
            candidate_target = self.artifact_root / run_id / "candidate" / "evidence"
            candidate_target.mkdir(parents=True, exist_ok=True)
            candidate_named = self._action(
                run_id, fence, "candidate_named", manifest, candidate, candidate_target
            )
            candidate_browser = self._action(
                run_id, fence, "candidate_browser", manifest, candidate, candidate_target
            )
            self._encode_media(run_id, fence, "candidate", candidate_browser, candidate_target)
            candidate_oracle = self._action(
                run_id, fence, "candidate_oracle", manifest, candidate, candidate_target, oracle
            )
            checks = [candidate_named, candidate_browser, candidate_oracle]
            passed = all(receipt["status"] == "PASSED" for receipt in checks)
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            assert_fence(run, self.worker_id, fence)
            prior = db.scalars(
                select(RunEvent).where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "verification.completed",
                )
            ).all()
            if not any(event.payload.get("scope") == "synthetic_candidate" for event in prior):
                append_event(
                    db, run, "verification.completed",
                    {
                        "scope": "synthetic_candidate",
                        "patch_sha256": patch.proposal.patch_sha256,
                        "checks": {
                            name: receipt["status"]
                            for name, receipt in zip(
                                ("named", "browser", "oracle"), checks, strict=True
                            )
                        },
                        "verdict": "PASSED" if passed else "FAILED",
                    },
                )
            db.commit()
        self._transition(
            run_id, fence,
            "REVIEW_READY" if passed else "FAILED",
            "PASSED" if passed else "FAILED",
        )
        return passed

    def _run_workflow(self, run_id: str, fence: int, commit: str) -> None:
        with self.session_factory() as db:
            bind = db.get_bind()
            dialect = bind.dialect.name
            connection_url = (
                bind.url.set(drivername="postgresql").render_as_string(hide_password=False)
                if dialect == "postgresql" else None
            )
        if connection_url:
            with PostgresSaver.from_conn_string(connection_url) as saver:
                saver.conn.execute("SET search_path TO aip_workflow, public")
                saver.setup()
                graph = build_fixture_workflow(self, run_id, fence, commit, saver)
                invoke_fixture_workflow(graph, run_id, commit)
        else:
            # SQLite is confined to local development and isolated unit tests.
            graph = build_fixture_workflow(self, run_id, fence, commit, InMemorySaver())
            invoke_fixture_workflow(graph, run_id, commit)

    @tracer.start_as_current_span("run.execute")
    def execute(self, run_id: str, fence: int | None = None) -> None:
        set_safe_attributes(run_id=run_id)
        with self.session_factory() as db:
            if fence is None:
                run, fence = claim_run(db, run_id, self.worker_id, settings().lease_seconds)
            else:
                run = db.get(Run, run_id)
                assert_fence(run, self.worker_id, fence)
            if run.state not in {
                "QUEUED",
                "PREPARING",
                "REPRODUCING",
                "INVESTIGATING",
                "PATCHING",
                "VERIFYING",
            }:
                raise ServiceError(
                    "RUN_NOT_EXECUTABLE", "Development worker cannot resume this state", 409
                )
            if (
                run.config_snapshot.get("reproduction", {}).get("fixture_case_id")
                != "form-submit-001"
            ):
                raise ServiceError(
                    "FIXTURE_UNAVAILABLE", "Only the reviewed fixture is supported", 409
                )
            commit = run.base_commit
            initial_state = run.state
            if initial_state == "QUEUED":
                transition(db, run, self.worker_id, fence, "PREPARING")
            db.commit()

        self._stop.clear()
        self._cancelled.clear()
        watcher = threading.Thread(target=self._heartbeat, args=(run_id, fence), daemon=True)
        watcher.start()
        try:
            self._run_workflow(run_id, fence, commit)
        except (
            ServiceError,
            SandboxError,
            PatchError,
            ProviderError,
            OSError,
            ValueError,
        ) as error:
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                if run and run.lease_owner == self.worker_id and run.lease_fence == fence:
                    if run.cancel_requested:
                        transition(db, run, self.worker_id, fence, "CANCELLED", "NOT_RUN")
                    elif (
                        isinstance(error, ServiceError)
                        and error.code == "RUN_SPEND_EXHAUSTED"
                        and run.state in {"PATCHING", "VERIFYING"}
                    ):
                        transition(db, run, self.worker_id, fence, "PAUSED_BUDGET")
                        append_event(db, run, "budget.pause", {
                            "spend_limit_usd": run.config_snapshot["spend_limit_usd"],
                        })
                    elif run.state in {"PREPARING", "REPRODUCING"}:
                        transition(db, run, self.worker_id, fence, "FAILED", "INCONCLUSIVE")
                    elif run.state == "INVESTIGATING":
                        transition(db, run, self.worker_id, fence, "INCONCLUSIVE", "INCONCLUSIVE")
                    elif run.state in {"PATCHING", "VERIFYING"}:
                        transition(db, run, self.worker_id, fence, "FAILED", "INCONCLUSIVE")
                    append_event(
                        db,
                        run,
                        "worker.error",
                        {
                            "code": error.code
                            if isinstance(error, (ServiceError, ProviderError))
                            else type(error).__name__,
                        },
                    )
                    db.commit()
            raise
        finally:
            self._stop.set()
            watcher.join(timeout=2)

    def process_next(self, event_id: str | None = None) -> str | None:
        self.recover_stale()
        with self.session_factory() as db:
            query = select(OutboxEvent).where(
                OutboxEvent.topic == "run.dispatch",
                OutboxEvent.status == "pending",
            )
            if event_id is not None:
                query = query.where(OutboxEvent.id == event_id)
            events = db.scalars(
                query.order_by(OutboxEvent.created_at)
                .with_for_update(skip_locked=True).limit(100)
            ).all()
            event = None
            for candidate in events:
                run = db.get(Run, candidate.payload.get("run_id"))
                if run is None:
                    candidate.status = "failed"
                    continue
                if run.config_snapshot.get("reproduction", {}).get("fixture_case_id") != (
                    "form-submit-001"
                ):
                    continue
                if run.state in {
                    "COMPLETED",
                    "INCONCLUSIVE",
                    "FAILED",
                    "CANCELLED",
                    "REVIEW_READY",
                    "PAUSED_INPUT",
                    "PAUSED_APPROVAL",
                    "PAUSED_BUDGET",
                }:
                    candidate.status = "delivered"
                    continue
                if (
                    run.cancel_requested
                    or (run.lease_until and aware(run.lease_until) > utcnow())
                    or run.state
                    not in {
                        "QUEUED",
                        "PREPARING",
                        "REPRODUCING",
                        "INVESTIGATING",
                        "PATCHING",
                        "VERIFYING",
                    }
                ):
                    continue
                event = candidate
                break
            if event is None:
                db.commit()
                return None
            run_id = event.payload["run_id"]
            run, fence = claim_run(db, run_id, self.worker_id, settings().lease_seconds)
            if run.state == "QUEUED":
                transition(db, run, self.worker_id, fence, "PREPARING")
            elif run.state not in {
                "PREPARING",
                "REPRODUCING",
                "INVESTIGATING",
                "PATCHING",
                "VERIFYING",
            }:
                raise ServiceError("RUN_NOT_EXECUTABLE", "Dispatch state is not executable", 409)
            event.status = "processing"
            event.attempts += 1
            event_id = event.id
            db.commit()
        with tracer.start_as_current_span("dispatch.consume", context=extract_trace(event.payload)):
            set_safe_attributes(run_id=run_id, dispatch_event_id=event_id)
            try:
                self.execute(run_id, fence)
                final_status = "delivered"
            except (ServiceError, SandboxError, PatchError, ProviderError, OSError, ValueError):
                with self.session_factory() as db:
                    state = db.get(Run, run_id).state
                final_status = "delivered" if state.startswith("PAUSED") else "failed"
        with self.session_factory() as db:
            event = db.get(OutboxEvent, event_id)
            event.status = final_status
            db.commit()
        return run_id

    def recover_stale(self) -> int:
        """Requeue safe expired dispatches; stop when a side effect is uncertain."""
        recovered = 0
        with self.session_factory() as db:
            events = db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.topic == "run.dispatch",
                    OutboxEvent.status == "processing",
                )
                .order_by(OutboxEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(100)
            ).all()
            for event in events:
                run = db.get(Run, event.payload.get("run_id"))
                if run is None:
                    event.status = "failed"
                    recovered += 1
                    continue
                if run.lease_until and aware(run.lease_until) > utcnow():
                    continue
                if run.state in {
                    "REVIEW_READY", "COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"
                }:
                    event.status = "delivered"
                    recovered += 1
                    continue
                if run.cancel_requested:
                    request_cancel(db, run, "worker-recovery")
                    event.status = "delivered"
                    recovered += 1
                    continue
                uncertain = db.scalar(
                    select(ToolAction.id)
                    .where(
                        ToolAction.run_id == run.id,
                        ToolAction.status == "INTENDED",
                    )
                    .limit(1)
                )
                if uncertain is not None:
                    owned, fence = claim_run(db, run.id, self.worker_id, settings().lease_seconds)
                    stop_state = (
                        "FAILED"
                        if owned.state in {"PREPARING", "PATCHING", "VERIFYING"}
                        else "INCONCLUSIVE"
                    )
                    transition(db, owned, self.worker_id, fence, stop_state, "INCONCLUSIVE")
                    append_event(
                        db,
                        owned,
                        "worker.error",
                        {
                            "code": "EFFECT_OUTCOME_UNKNOWN",
                            "replay_blocked": True,
                        },
                    )
                    event.status = "failed"
                else:
                    event.status = "pending"
                recovered += 1
            db.commit()
        return recovered


def main() -> int:
    configure_telemetry()
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", type=Path, default=Path(settings().artifact_dir))
    parser.add_argument("--runtime", choices=["native", "wsl"], default="wsl")
    parser.add_argument("--image", default="aip-dev-sandbox:0.1.0")
    parser.add_argument("--serve", action="store_true", help="Poll the durable dispatch outbox")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.image.startswith("-") or any(character.isspace() for character in args.image):
        parser.error("--image must be a Docker image reference")
    worker = DevelopmentWorker(
        args.repository, args.artifacts, runtime=args.runtime, image=args.image
    )
    if args.serve:
        if not 0.2 <= args.poll_seconds <= 60:
            parser.error("--poll-seconds must be between 0.2 and 60")
        try:
            while True:
                if worker.process_next() is None:
                    time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            return 0
    run_id = worker.process_next()
    print(json.dumps({"run_id": run_id, "status": "none" if run_id is None else "processed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
