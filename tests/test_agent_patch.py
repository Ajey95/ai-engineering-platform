import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from platform_app.agent_patch import request_fixture_patch
from platform_app.db import Base
from platform_app.models import BudgetEntry, ModelEntry, Run, Task, Tenant, ToolAction
from platform_app.providers import OpenAIResponses
from platform_app.run_ledger import claim_run


def test_native_provider_patch_is_reserved_and_settled_from_usage(tmp_path):
    source = Path(__file__).resolve().parents[1] / "benchmarks/fixtures/form-submit/base/server.py"
    fixed = source.read_text(encoding="utf-8").replace(
        "quantity: int | None = None", "quantity: int = 1"
    )
    patch = json.dumps(
        {
            "diagnosis": "Quantity is absent on valid form submits",
            "files": [
                {"path": "server.py", "content": fixed},
            ],
        }
    )
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "model": "live-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": patch},
                        ],
                    }
                ],
                "usage": {"input_tokens": 300, "output_tokens": 100},
            },
        )

    adapter = OpenAIResponses(
        "test-only-key", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'agent.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(
            Task(
                id="task-a",
                tenant_id="tenant-a",
                project_id="project-a",
                report="Submitting a valid form returns HTTP 500",
                expected_behavior="Ticket created",
                actual_behavior="HTTP 500",
                created_by="alice",
            )
        )
        db.add(
            ModelEntry(
                id="model-a",
                provider="openai",
                model_id="live-model",
                registry_revision="rev-a",
                state="enabled",
                capabilities={"live_qualified": True},
                validated_at=datetime.now(UTC),
                context_limit=32000,
                output_limit=4000,
                price_revision="price-a",
                price_per_m_input=Decimal("1"),
                price_per_m_output=Decimal("2"),
            )
        )
        db.add(
            Run(
                id="run-a",
                tenant_id="tenant-a",
                task_id="task-a",
                project_id="project-a",
                created_by="alice",
                idempotency_key="key-a",
                request_hash="hash",
                base_commit="a" * 40,
                model_entry_id="model-a",
                state="QUEUED",
                config_snapshot={
                    "max_model_calls": 3,
                    "spend_limit_usd": 5,
                    "model_registry_revision": "rev-a",
                    "model_price_revision": "price-a",
                    "model_context_limit": 32000,
                    "model_output_limit": 4000,
                    "model_price_per_m_input": "1.000000",
                    "model_price_per_m_output": "2.000000",
                },
            )
        )
        db.commit()
        _, fence = claim_run(db, "run-a", "worker-one")
        db.commit()

    result = request_fixture_patch(
        factory,
        "run-a",
        "worker-one",
        fence,
        {"browser": {"status": "FAILED", "responses": [{"status": 500}]}},
        source.read_text(encoding="utf-8"),
        tmp_path,
        provider=adapter,
    )
    assert result.proposal.files[0][0] == "server.py"
    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.openai.com/v1/responses"
    with factory() as db:
        action = db.scalar(select(ToolAction).where(ToolAction.logical_action == "model.generate"))
        reservation = db.scalar(select(BudgetEntry).where(BudgetEntry.category == "call:model-1"))
        assert action.status == "COMPLETED"
        assert action.receipt["usage"] == {"input_tokens": 300, "output_tokens": 100}
        assert reservation.status == "settled"
        assert Decimal(reservation.actual_usd) == Decimal("0.000500")
    artifact = json.loads((tmp_path / result.artifact_ref).read_text(encoding="utf-8"))
    assert artifact["text"] == patch
    engine.dispose()
