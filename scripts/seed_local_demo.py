"""Create one clearly labelled synthetic fixture workspace in the local DB."""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select

from platform_app.config import settings
from platform_app.db import Base, SessionLocal, engine
from platform_app.models import ModelEntry, Project, Task, Tenant


def main() -> int:
    config = settings()
    if config.environment != "development":
        raise SystemExit("Synthetic demo seeding is development only")
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        tenant = db.get(Tenant, config.dev_tenant)
        if tenant is None:
            tenant = Tenant(id=config.dev_tenant, name="Local development")
            db.add(tenant)
        fixture = db.get(ModelEntry, "local-fixture-model")
        if fixture is None:
            fixture = ModelEntry(
                id="local-fixture-model",
                provider="openai",
                model_id="synthetic-fixture",
                registry_revision="synthetic-v1",
                state="enabled",
                capabilities={"database_fixture_only": True},
                context_limit=32000,
                output_limit=4000,
                price_revision="synthetic-zero",
                price_per_m_input=Decimal("0"),
                price_per_m_output=Decimal("0"),
            )
            db.add(fixture)
        elif (fixture.capabilities or {}).get(
            "database_fixture_only"
        ) is not True or fixture.state != "enabled":
            raise SystemExit("Existing model ID is not a synthetic fixture")
        project = db.scalar(
            select(Project).where(
                Project.tenant_id == config.dev_tenant,
                Project.name == "Synthetic form fixture",
            )
        )
        if project is None:
            project = Project(
                tenant_id=config.dev_tenant,
                name="Synthetic form fixture",
                repository_url="https://example.test/platform-fixture.git",
                test_url="http://127.0.0.1:8001",
                environment_manifest={"case_id": "form-submit-001"},
            )
            db.add(project)
            db.flush()
        elif (
            project.environment_manifest != {"case_id": "form-submit-001"}
            or project.repository_url != "https://example.test/platform-fixture.git"
            or project.test_url != "http://127.0.0.1:8001"
        ):
            raise SystemExit("Existing project is not the reviewed synthetic fixture")
        task = db.scalar(
            select(Task).where(
                Task.tenant_id == config.dev_tenant,
                Task.project_id == project.id,
                Task.created_by == "fixture-seed",
            )
        )
        if task is None:
            task = Task(
                tenant_id=config.dev_tenant,
                project_id=project.id,
                report="Submitting a valid form returns HTTP 500",
                expected_behavior="The form creates a ticket with HTTP 201",
                actual_behavior="The API returns HTTP 500 for a valid submit",
                created_by="fixture-seed",
            )
            db.add(task)
            db.flush()
        db.commit()
        print(
            json.dumps(
                {
                    "scope": "synthetic_local_only",
                    "project_id": project.id,
                    "task_id": task.id,
                    "model_entry_id": fixture.id,
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
