"""Verify admitted synthetic runs against real development containers.

The optional controlled provider mode uses a predetermined HTTP response. It
tests the protocol, not a live provider account or autonomous diagnosis.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import sessionmaker

from platform_app.api import review_packet
from platform_app.db import Base
from platform_app.development_worker import DevelopmentWorker
from platform_app.models import (
    ModelEntry,
    OutboxEvent,
    Project,
    Run,
    RunEvent,
    Task,
    Tenant,
    ToolAction,
)
from platform_app.providers import OpenAIResponses
from platform_app.run_ledger import claim_run, resume_budget_run, resume_input_run, transition
from platform_app.schemas import RunCreate
from platform_app.service import admit_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controlled-provider", action="store_true")
    parser.add_argument("--resume-probe", action="store_true")
    parser.add_argument("--budget-pause-probe", action="store_true")
    parser.add_argument("--runtime", choices=("wsl", "native"), default="wsl")
    parser.add_argument("--image", default="aip-dev-sandbox:0.1.0")
    parser.add_argument(
        "--disposable-postgres-env", action="store_true",
        help="Use AIP_VERIFY_DATABASE_URL for a migrated disposable loopback database",
    )
    args = parser.parse_args()
    if args.budget_pause_probe and not args.controlled_provider:
        parser.error("Budget pause probe requires the controlled provider")
    root = Path(__file__).resolve().parents[1]
    target = root / "artifacts" / "worker-verification" / uuid4().hex[:12]
    target.mkdir(parents=True)
    if args.disposable_postgres_env:
        database_url = os.environ.get("AIP_VERIFY_DATABASE_URL", "")
        try:
            parsed = make_url(database_url)
        except (ArgumentError, ValueError) as error:
            parser.error(f"AIP_VERIFY_DATABASE_URL is invalid: {type(error).__name__}")
        if (
            parsed.drivername != "postgresql+psycopg"
            or parsed.host not in {"127.0.0.1", "localhost"}
            or not re.fullmatch(r"aip_verify_[0-9a-f]{12}", parsed.database or "")
        ):
            parser.error("Only a migrated disposable loopback PostgreSQL database is allowed")
        engine = create_engine(database_url)
        storage_backend = "migrated_local_postgresql"
    else:
        database = target / "verify.db"
        engine = create_engine(f"sqlite:///{database.as_posix()}")
        Base.metadata.create_all(engine)
        storage_backend = "local_sqlite"
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    with factory() as db:
        db.add(Tenant(id="fixture-tenant", name="Isolated worker verification"))
        db.flush()
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
        db.flush()
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
                capabilities={"database_fixture_only": True, "controlled_provider_fixture": True}
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
                max_spend_usd=Decimal("0.000001") if args.budget_pause_probe else None,
            ),
        )
        db.commit()
        run_id = run.id

    if args.resume_probe:
        with factory() as db:
            paused, fence = claim_run(db, run_id, "preflight-worker")
            transition(db, paused, "preflight-worker", fence, "PREPARING")
            transition(db, paused, "preflight-worker", fence, "PAUSED_INPUT")
            db.commit()
            resume_input_run(
                db,
                "fixture-tenant",
                run_id,
                "fixture-verifier",
                "Use the valid form submission scenario",
                "resume-probe-key-001",
            )
            db.commit()

    adapter = None
    provider_requests: list[httpx.Request] = []
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
            provider_requests.append(_request)
            if args.resume_probe and (
                b"Use the valid form submission scenario" not in _request.content
                or b"authenticated_project_contributor_input" not in _request.content
            ):
                raise RuntimeError("Resumed input is missing from the model context")
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
    worker = DevelopmentWorker(
        root, target, session_factory=factory, patch_provider=adapter, runtime=args.runtime,
        image=args.image,
    )
    processed = worker.process_next()
    budget_pause_verified = False
    if args.budget_pause_probe:
        with factory() as db:
            paused = db.get(Run, run_id)
            if paused.state != "PAUSED_BUDGET":
                raise RuntimeError(f"Expected a budget pause, got {paused.state}")
            if provider_requests or db.scalar(select(ToolAction.id).where(
                ToolAction.run_id == run_id,
                ToolAction.logical_action == "model.generate",
            )) is not None:
                raise RuntimeError("Provider effect started before budget approval")
            if db.scalar(select(RunEvent.id).where(
                RunEvent.run_id == run_id, RunEvent.event_type == "budget.pause",
            )) is None:
                raise RuntimeError("Durable budget pause event is missing")
            budget_pause_verified = True
            resume_budget_run(
                db, "fixture-tenant", run_id, "fixture-verifier",
                "Reviewed controlled run budget", Decimal("5"), "budget-resume-probe-001",
            )
            db.commit()
        processed = worker.process_next()
    with factory() as db:
        run = db.get(Run, run_id)
        outboxes = db.scalars(select(OutboxEvent).where(OutboxEvent.topic == "run.dispatch")).all()
        actions = db.scalars(select(ToolAction).where(ToolAction.run_id == run_id)).all()
        packet = review_packet(run_id, ("fixture-tenant", "fixture-verifier"), db)
        last_events = list(db.scalars(select(RunEvent).where(
            RunEvent.run_id == run_id,
        ).order_by(RunEvent.sequence.desc()).limit(8)))
        result = {
            "storage_backend": storage_backend,
            "run_id": run_id,
            "pinned_commit": commit,
            "processed_run_id": processed,
            "outbox_status": "delivered"
            if all(event.status == "delivered" for event in outboxes)
            else "incomplete",
            "outbox_count": len(outboxes),
            "resume_probe": args.resume_probe,
            "budget_pause_probe": args.budget_pause_probe,
            "budget_pause_verified": budget_pause_verified,
            "provider_request_count": len(provider_requests),
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
            "last_events": [
                {
                    "type": event.event_type,
                    "code": event.payload.get("code"),
                    "state": event.payload.get("state"),
                }
                for event in reversed(last_events)
            ],
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
            and result["outbox_count"] == (
                2 if args.resume_probe or args.budget_pause_probe else 1
            )
            and result["state"] == ("REVIEW_READY" if args.controlled_provider else "INCONCLUSIVE")
            and result["verdict"] == ("PASSED" if args.controlled_provider else "INCONCLUSIVE")
            and result["tool_statuses"] == expected
            and result["media_status"] == "READY"
            and set(result["media_manifest_urls"])
            == ({"baseline", "candidate"} if args.controlled_provider else {"baseline"})
            and result["reproduction_status"] == "REPRODUCED"
            and result["autonomous_repair"] is False
            and (
                not args.budget_pause_probe
                or result["budget_pause_verified"] and result["provider_request_count"] == 1
            )
            and (
                not args.controlled_provider
                or result["qualification_scope"] == "synthetic_container_controlled_provider"
            )
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
