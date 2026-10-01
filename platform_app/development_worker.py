"""Execute the trusted synthetic fixture from a pinned platform commit.

This worker is deliberately limited to development fixtures. It never checks
out a customer repository or treats the baseline as an autonomous repair.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from platform_app.config import settings
from platform_app.db import SessionLocal, utcnow
from platform_app.dev_sandbox import (
    SandboxError,
    _docker_prefix,
    run_browser_fixture,
    run_verifier_fixture,
)
from platform_app.models import OutboxEvent, Run, RunEvent, ToolAction
from platform_app.run_ledger import (
    assert_fence,
    aware,
    begin_tool_action,
    claim_run,
    complete_tool_action,
    heartbeat,
    transition,
)
from platform_app.service import ServiceError, append_event, request_cancel
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
    result_name = {
        "named": "test-baseline.json",
        "browser": "result.json",
        "oracle": "oracle.json",
    }[step]
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
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as source:
        for member in source.getmembers():
            parts = Path(member.name).parts
            allowed_parents = {
                "benchmarks",
                "benchmarks/fixtures",
                FIXTURE_PATH,
                "benchmarks/oracles",
                "benchmarks/oracles/form-submit-001",
            }
            if (
                (
                    member.name not in allowed_parents
                    and not member.name.startswith(FIXTURE_PATH + "/")
                    and member.name != ORACLE_PATH
                )
                or ".." in parts
                or not (member.isfile() or member.isdir())
            ):
                raise ServiceError(
                    "FIXTURE_UNSAFE", "Fixture archive contains an unsafe entry", 409
                )
        source.extractall(destination, filter="data")
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
    ):
        if settings().environment != "development":
            raise RuntimeError("Synthetic fixture worker is development only")
        self.repository = repository.resolve(strict=True)
        self.artifact_root = artifact_root.resolve()
        self.worker_id = worker_id or f"dev-worker-{uuid4().hex[:12]}"
        self.runtime = runtime
        self.image = image
        self.session_factory = session_factory
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
        if self._cancelled.is_set():
            raise ServiceError("RUN_CANCELLED", "Worker observed cancellation", 409)
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            count = db.scalar(
                select(func.count())
                .select_from(ToolAction)
                .where(
                    ToolAction.run_id == run_id,
                )
            )
            if count >= int(run.config_snapshot["max_tool_calls"]):
                raise ServiceError("TOOL_BUDGET_EXHAUSTED", "Run tool call limit reached", 409)
            arguments = {
                "fixture_case_id": "form-submit-001",
                "base_commit": run.base_commit,
                "workspace_tree_sha256": tree_hash(workspace),
                "step": step,
            }
            action = begin_tool_action(
                db, run, self.worker_id, fence, step, f"fixture.{step}", arguments, True
            )
            if action.status == "COMPLETED":
                return _verified_receipt(step, target, action.receipt)
            db.commit()  # Persist effect intent before starting the container.
            action_id = action.id

        name = f"aip-dev-{uuid4().hex[:12]}"
        self._active_container = name
        try:
            if step == "browser":
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
                    mode="oracle" if step == "oracle" else "named",
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

    def execute(self, run_id: str, fence: int | None = None) -> None:
        with self.session_factory() as db:
            if fence is None:
                run, fence = claim_run(db, run_id, self.worker_id, settings().lease_seconds)
            else:
                run = db.get(Run, run_id)
                assert_fence(run, self.worker_id, fence)
            if run.state not in {"QUEUED", "PREPARING", "REPRODUCING", "INVESTIGATING"}:
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
            with tempfile.TemporaryDirectory(prefix="aip-pinned-fixture-") as temporary:
                manifest, workspace, oracle = _pinned_fixture(
                    self.repository, commit, Path(temporary)
                )
                source = json.loads(manifest.read_text(encoding="utf-8"))
                if source.get("case_id") != "form-submit-001":
                    raise ServiceError(
                        "FIXTURE_UNAVAILABLE", "Pinned fixture identity changed", 409
                    )
                target = self.artifact_root / run_id / "baseline"
                target.mkdir(parents=True, exist_ok=True)
                if initial_state in {"QUEUED", "PREPARING"}:
                    self._transition(run_id, fence, "REPRODUCING")
                named = self._action(run_id, fence, "named", manifest, workspace, target)
                browser = self._action(run_id, fence, "browser", manifest, workspace, target)
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
                            db,
                            run,
                            "verification.completed",
                            {
                                "scope": "synthetic_baseline_only",
                                "reproduced": reproduced,
                                "candidate_status": "NOT_RUN",
                            },
                        )
                    db.commit()
                if reproduced and initial_state != "INVESTIGATING":
                    self._transition(run_id, fence, "INVESTIGATING")
                self._transition(run_id, fence, "INCONCLUSIVE", "INCONCLUSIVE")
        except (ServiceError, SandboxError, OSError, ValueError) as error:
            with self.session_factory() as db:
                run = db.get(Run, run_id)
                if run and run.lease_owner == self.worker_id and run.lease_fence == fence:
                    if run.cancel_requested:
                        transition(db, run, self.worker_id, fence, "CANCELLED", "NOT_RUN")
                    elif run.state in {"PREPARING", "REPRODUCING"}:
                        transition(db, run, self.worker_id, fence, "FAILED", "INCONCLUSIVE")
                    elif run.state == "INVESTIGATING":
                        transition(db, run, self.worker_id, fence, "INCONCLUSIVE", "INCONCLUSIVE")
                    append_event(
                        db,
                        run,
                        "worker.error",
                        {
                            "code": error.code
                            if isinstance(error, ServiceError)
                            else type(error).__name__,
                        },
                    )
                    db.commit()
            raise
        finally:
            self._stop.set()
            watcher.join(timeout=2)

    def process_next(self) -> str | None:
        self.recover_stale()
        with self.session_factory() as db:
            events = db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.topic == "run.dispatch",
                    OutboxEvent.status == "pending",
                )
                .order_by(OutboxEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(100)
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
                if run.state in {"COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"}:
                    candidate.status = "delivered"
                    continue
                if (
                    run.cancel_requested
                    or (run.lease_until and aware(run.lease_until) > utcnow())
                    or run.state not in {"QUEUED", "PREPARING", "REPRODUCING", "INVESTIGATING"}
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
            elif run.state not in {"PREPARING", "REPRODUCING", "INVESTIGATING"}:
                raise ServiceError("RUN_NOT_EXECUTABLE", "Dispatch state is not executable", 409)
            event.status = "processing"
            event.attempts += 1
            event_id = event.id
            db.commit()
        try:
            self.execute(run_id, fence)
            final_status = "delivered"
        except (ServiceError, SandboxError, OSError, ValueError):
            final_status = "failed"
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
                if run.state in {"COMPLETED", "INCONCLUSIVE", "FAILED", "CANCELLED"}:
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
                    stop_state = "FAILED" if owned.state == "PREPARING" else "INCONCLUSIVE"
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--artifacts", type=Path, default=Path(settings().artifact_dir))
    parser.add_argument("--runtime", choices=["native", "wsl"], default="wsl")
    args = parser.parse_args()
    worker = DevelopmentWorker(args.repository, args.artifacts, runtime=args.runtime)
    run_id = worker.process_next()
    print(json.dumps({"run_id": run_id, "status": "none" if run_id is None else "processed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
