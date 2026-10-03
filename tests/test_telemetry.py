import json
import os
import subprocess
import sys
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from opentelemetry import trace
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.media_queue import consume_one_media_dispatch
from platform_app.models import ModelEntry, OutboxEvent, Project, Task, Tenant
from platform_app.queue_consumer import consume_one_run_dispatch
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


def test_hosted_run_and_media_consumers_resume_recorded_parent(exporter):
    class FakeSQS:
        def __init__(self, event_id):
            self.event_id = event_id
            self.deleted = False

        def receive_message(self, **_):
            if self.deleted:
                return {"Messages": []}
            return {"Messages": [{
                "Body": json.dumps({
                    "version": 1, "event_id": self.event_id,
                    "tenant_id": "tenant-a", "run_id": "run-a",
                }),
                "ReceiptHandle": "receipt-a",
            }]}

        def change_message_visibility(self, **_):
            return {}

        def delete_message(self, **_):
            self.deleted = True

    exporter.clear()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with tracer.start_as_current_span("run.parent") as parent:
        parent_id = parent.get_span_context().trace_id
        carrier = inject_trace()
    with Session(engine) as db:
        db.add(Tenant(id="tenant-a", name="A"))
        for event_id, topic in (("run-event", "run.dispatch"),
                                ("media-event", "media.transcode")):
            db.add(OutboxEvent(
                id=event_id, tenant_id="tenant-a", topic=topic,
                payload={"run_id": "run-a", **carrier},
            ))
        db.commit()

    def finish(event_id):
        assert trace.get_current_span().get_span_context().trace_id == parent_id
        with Session(engine) as db:
            db.get(OutboxEvent, event_id).status = "delivered"
            db.commit()
        return event_id

    def factory():
        return Session(engine)
    queue_url = "https://sqs.example.test/123/events"
    assert consume_one_run_dispatch(
        factory, FakeSQS("run-event"), queue_url, finish, wait_seconds=0,
    )
    assert consume_one_media_dispatch(
        factory, FakeSQS("media-event"), queue_url, finish, wait_seconds=0,
    )
    spans = [span for span in exporter.get_finished_spans()
             if span.name in {"run.parent", "dispatch.consume", "media.consume"}]
    assert {span.name for span in spans} == {
        "run.parent", "dispatch.consume", "media.consume",
    }
    assert all(span.context.trace_id == parent_id for span in spans)
    assert all(span.parent.span_id == next(
        item.context.span_id for item in spans if item.name == "run.parent"
    ) for span in spans if span.name != "run.parent")
    engine.dispose()


def test_configured_otlp_exports_to_local_collector():
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/v1/traces"
            length = int(self.headers["Content-Length"])
            received.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Collector)
    server.timeout = 10
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    environment = os.environ.copy()
    environment.update({
        "AIP_ENVIRONMENT": "development",
        "AIP_OTLP_TRACES_ENDPOINT": (
            f"http://127.0.0.1:{server.server_port}/v1/traces"
        ),
    })
    script = (
        "from opentelemetry import trace; "
        "from platform_app.telemetry import configure_telemetry, tracer; "
        "configure_telemetry(); "
        "span=tracer.start_span('hosted.export.smoke'); span.end(); "
        "assert trace.get_tracer_provider().force_flush(timeout_millis=5000)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script], check=False, capture_output=True,
            text=True, timeout=12, env=environment,
        )
        thread.join(timeout=10)
        assert result.returncode == 0, result.stderr
        assert len(received) == 1
        request = ExportTraceServiceRequest.FromString(received[0])
        names = [span.name for resource in request.resource_spans
                 for scope in resource.scope_spans for span in scope.spans]
        assert names == ["hosted.export.smoke"]
    finally:
        server.server_close()
