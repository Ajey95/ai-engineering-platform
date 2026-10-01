"""Run an admitted synthetic baseline through the real WSL containers.

The enabled model row exists only in this disposable database. No model call,
patch, or candidate verification occurs in this check.
"""

from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.api import review_packet
from platform_app.db import Base
from platform_app.development_worker import DevelopmentWorker
from platform_app.models import ModelEntry, OutboxEvent, Project, Run, Task, Tenant, ToolAction
from platform_app.schemas import RunCreate
from platform_app.service import admit_run


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / "artifacts" / "worker-verification" / uuid4().hex[:12]
    target.mkdir(parents=True)
    database = target / "verify.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    with factory() as db:
        db.add(Tenant(id="fixture-tenant", name="Isolated worker verification"))
        db.add(
            Project(
                id="fixture-project",
                tenant_id="fixture-tenant",
                name="Synthetic fixture",
                repository_url="https://example.test/platform-fixture.git",
                test_url="http://127.0.0.1:8001",
                environment_manifest={"case_id": "form-submit-001"},
            )
        )
        db.add(
            Task(
                id="fixture-task",
                tenant_id="fixture-tenant",
                project_id="fixture-project",
                report="Submitting a valid form returns HTTP 500",
                expected_behavior="A new ticket is created",
                actual_behavior="The API returns HTTP 500",
                created_by="fixture-verifier",
            )
        )
        db.add(
            ModelEntry(
                id="database-fixture-model",
                provider="openai",
                model_id="fixture-only",
                registry_revision="fixture",
                state="enabled",
                capabilities={"database_fixture_only": True},
                context_limit=32000,
                output_limit=4000,
                price_revision="fixture",
                price_per_m_input=Decimal("1"),
                price_per_m_output=Decimal("2"),
            )
        )
        db.commit()
        run = admit_run(
            db,
            "fixture-tenant",
            "fixture-verifier",
            "fixture-task",
            "worker-verify-key",
            RunCreate(
                base_commit=commit,
                selected_model_entry="database-fixture-model",
                reproduction={"fixture_case_id": "form-submit-001"},
            ),
        )
        db.commit()
        run_id = run.id

    worker = DevelopmentWorker(root, target, session_factory=factory)
    processed = worker.process_next()
    with factory() as db:
        run = db.get(Run, run_id)
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.topic == "run.dispatch"))
        actions = db.scalars(select(ToolAction).where(ToolAction.run_id == run_id)).all()
        packet = review_packet(run_id, ("fixture-tenant", "fixture-verifier"), db)
        result = {
            "run_id": run_id,
            "pinned_commit": commit,
            "processed_run_id": processed,
            "outbox_status": outbox.status,
            "state": run.state,
            "verdict": run.verdict,
            "tool_actions": len(actions),
            "tool_statuses": {a.step_id: a.receipt["status"] for a in actions if a.receipt},
            "reproduction_status": packet["reproduction_status"],
            "autonomous_repair": packet["autonomous_repair"],
        }
        (target / "review-packet.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")
        (target / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"artifact_dir": str(target), **result}, indent=2))
    engine.dispose()
    return (
        0
        if (
            processed == run_id
            and result["outbox_status"] == "delivered"
            and result["state"] == "INCONCLUSIVE"
            and result["verdict"] == "INCONCLUSIVE"
            and result["tool_statuses"]
            == {"named": "PASSED", "browser": "FAILED", "oracle": "FAILED"}
            and result["reproduction_status"] == "REPRODUCED"
            and result["autonomous_repair"] is False
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
