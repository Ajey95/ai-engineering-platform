from decimal import Decimal

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.models import ModelEntry, OutboxEvent, Project, Task, Tenant
from platform_app.schemas import RunCreate
from platform_app.service import admit_run
from platform_app.telemetry import extract_trace, inject_trace, set_safe_attributes, tracer


@pytest.fixture(scope="module")
def exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()


def test_trace_context_survives_outbox_boundary_without_content(exporter):
    with tracer.start_as_current_span("admission"):
        set_safe_attributes(run_id="run-a", prompt={"private": "never export"})
        carrier = inject_trace()
    assert set(carrier) == {"traceparent"}
    with tracer.start_as_current_span("dispatch.consume", context=extract_trace(carrier)):
        with tracer.start_as_current_span("run.execute"):
            set_safe_attributes(run_id="run-a")
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["run.execute"].parent.span_id == spans["dispatch.consume"].context.span_id
    assert spans["dispatch.consume"].parent.span_id == spans["admission"].context.span_id
    assert len({span.context.trace_id for span in spans.values()}) == 1
    assert spans["admission"].attributes == {"aip.run_id": "run-a"}


def test_admission_persists_traceparent_for_dispatch(exporter):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        db.add(
            Project(
                id="project-a",
                tenant_id="tenant-a",
                name="Fixture",
                repository_url="https://example.test/repo",
                test_url="http://fixture.test",
                environment_manifest={"case_id": "form-submit-001"},
            )
        )
        db.add(
            Task(
                id="task-a",
                tenant_id="tenant-a",
                project_id="project-a",
                report="500",
                expected_behavior="201",
                actual_behavior="500",
                created_by="alice",
            )
        )
        db.add(
            ModelEntry(
                id="model-a",
                provider="openai",
                model_id="fixture",
                registry_revision="rev-a",
                state="enabled",
                capabilities={"database_fixture_only": True},
                context_limit=32000,
                output_limit=4000,
                price_revision="price-a",
                price_per_m_input=Decimal("1"),
                price_per_m_output=Decimal("2"),
            )
        )
        db.commit()
        with tracer.start_as_current_span("http.request") as request:
            admit_run(
                db,
                "tenant-a",
                "alice",
                "task-a",
                "key-a",
                RunCreate(
                    base_commit="a" * 40,
                    selected_model_entry="model-a",
                    reproduction={"fixture_case_id": "form-submit-001"},
                ),
            )
            db.commit()
        event = db.scalar(select(OutboxEvent))
        assert event.payload["traceparent"].startswith("00-")
        with tracer.start_as_current_span(
            "dispatch.consume", context=extract_trace(event.payload)
        ) as dispatch:
            assert dispatch.get_span_context().trace_id == request.get_span_context().trace_id
    engine.dispose()
