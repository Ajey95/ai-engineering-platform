"""Verify admitted synthetic runs against real WSL containers.

The optional controlled provider mode uses a predetermined HTTP response. It
tests the protocol, not a live provider account or autonomous diagnosis.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.api import review_packet
from platform_app.db import Base
from platform_app.development_worker import DevelopmentWorker
from platform_app.models import ModelEntry, OutboxEvent, Project, Run, Task, Tenant, ToolAction
from platform_app.providers import OpenAIResponses
from platform_app.schemas import RunCreate
from platform_app.service import admit_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controlled-provider", action="store_true")
    args = parser.parse_args()
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
                capabilities={"live_qualified": True}
                if args.controlled_provider
                else {"database_fixture_only": True},
                validated_at=datetime.now(UTC) if args.controlled_provider else None,
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

    adapter = None
    if args.controlled_provider:
        original = (root / "benchmarks/fixtures/form-submit/base/server.py").read_text(
            encoding="utf-8"
        )
        fixed = original.replace("quantity: int | None = None", "quantity: int = 1")
        if fixed == original:
            raise RuntimeError("Controlled patch no longer matches the fixture")
        proposal = json.dumps(
            {
                "diagnosis": "A missing quantity is compared to zero",
                "files": [{"path": "server.py", "content": fixed}],
            }
        )

        def respond(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "fixture-only",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": proposal},
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 1500, "output_tokens": 500},
                },
            )

        adapter = OpenAIResponses(
            "controlled-response-only", client=httpx.Client(transport=httpx.MockTransport(respond))
        )
    worker = DevelopmentWorker(root, target, session_factory=factory, patch_provider=adapter)
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
            "qualification_scope": packet["qualification_scope"],
            "patch_hash": packet["patch_hash"],
            "media_status": packet["media_status"],
            "media_manifest_urls": packet["media_manifest_urls"],
        }
        (target / "review-packet.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")
        (target / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"artifact_dir": str(target), **result}, indent=2))
    engine.dispose()
    expected = (
        {
            "named": "PASSED",
            "browser": "FAILED",
            "media_baseline": "READY",
            "oracle": "FAILED",
            "model-1": "COMPLETED",
            "candidate_patch": "COMPLETED",
            "candidate_named": "PASSED",
            "candidate_browser": "PASSED",
            "media_candidate": "READY",
            "candidate_oracle": "PASSED",
        }
        if args.controlled_provider
        else {"named": "PASSED", "browser": "FAILED", "media_baseline": "READY", "oracle": "FAILED"}
    )
    return (
        0
        if (
            processed == run_id
            and result["outbox_status"] == "delivered"
            and result["state"] == ("REVIEW_READY" if args.controlled_provider else "INCONCLUSIVE")
            and result["verdict"] == ("PASSED" if args.controlled_provider else "INCONCLUSIVE")
            and result["tool_statuses"] == expected
            and result["media_status"] == "READY"
            and set(result["media_manifest_urls"]) == (
                {"baseline", "candidate"} if args.controlled_provider else {"baseline"}
            )
            and result["reproduction_status"] == "REPRODUCED"
            and result["autonomous_repair"] is False
            and (
                not args.controlled_provider
                or result["qualification_scope"] == "synthetic_container_controlled_provider"
            )
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
