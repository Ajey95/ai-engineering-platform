import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from platform_app.db import Base
from platform_app.models import OutboxEvent, Project, Run, Task, Tenant
from platform_app.queue_consumer import consume_one_run_dispatch
from platform_app.queue_dispatch import publish_run_dispatches, reset_stale_queue_receipt
from platform_app.service import ServiceError


class FakeSQS:
    def __init__(self):
        self.messages = []
        self.lose_first_receipt = False
        self.deleted = []

    def send_message(self, **kwargs):
        self.messages.append(kwargs)
        if self.lose_first_receipt:
            self.lose_first_receipt = False
            raise OSError("response lost after send")
        return {"MessageId": f"message-{len(self.messages)}"}

    def receive_message(self, **kwargs):
        if not self.messages:
            return {"Messages": []}
        return {"Messages": [{
            "Body": self.messages[0]["MessageBody"], "ReceiptHandle": "receipt-a"
        }]}

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs["ReceiptHandle"])
        self.messages.pop(0)

    def change_message_visibility(self, **kwargs):
        return {}


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="A"))
        session.add(Project(id="project-a", tenant_id="tenant-a", name="A"))
        session.add(Task(
            id="task-a", tenant_id="tenant-a", project_id="project-a",
            report="bug", expected_behavior="works", actual_behavior="broken",
            created_by="alice",
        ))
        session.add(Run(
            id="run-a", tenant_id="tenant-a", project_id="project-a", task_id="task-a",
            created_by="alice", idempotency_key="key-a", request_hash="h" * 64,
            base_commit="a" * 40, model_entry_id="model-a", state="QUEUED",
            config_snapshot={"policy_version": "1.0"},
        ))
        session.add(OutboxEvent(
            id="event-a", tenant_id="tenant-a", topic="run.dispatch",
            payload={"run_id": "run-a", "traceparent": "safe-trace"},
        ))
        session.commit()
        yield session
    engine.dispose()


def test_sqs_relay_records_receipt_without_changing_run_execution_status(db):
    sqs = FakeSQS()
    url = "https://sqs.example.test/123/runs"
    assert publish_run_dispatches(db, sqs, url) == 1
    event = db.get(OutboxEvent, "event-a")
    assert event.status == "pending" and event.queue_published_at is not None
    assert json.loads(sqs.messages[0]["MessageBody"]) == {
        "event_id": "event-a", "run_id": "run-a", "tenant_id": "tenant-a", "version": 1,
    }
    assert publish_run_dispatches(db, sqs, url) == 0
    assert len(sqs.messages) == 1


def test_lost_send_receipt_replays_same_outbox_id(db):
    sqs = FakeSQS()
    sqs.lose_first_receipt = True
    url = "https://sqs.example.test/123/runs"
    with pytest.raises(OSError):
        publish_run_dispatches(db, sqs, url)
    assert db.get(OutboxEvent, "event-a").queue_published_at is None
    assert publish_run_dispatches(db, sqs, url) == 1
    assert [json.loads(item["MessageBody"])["event_id"] for item in sqs.messages] == [
        "event-a", "event-a"
    ]


def test_recovery_requires_operator_and_pending_event(db):
    sqs = FakeSQS()
    url = "https://sqs.example.test/123/runs"
    publish_run_dispatches(db, sqs, url)
    with pytest.raises(ServiceError) as denied:
        reset_stale_queue_receipt(db, "event-a")
    assert denied.value.code == "FORBIDDEN"
    reset_stale_queue_receipt(db, "event-a", operator=True)
    assert publish_run_dispatches(db, sqs, url) == 1
    assert len(sqs.messages) == 2
    db.get(OutboxEvent, "event-a").status = "delivered"
    db.commit()
    with pytest.raises(ServiceError) as closed:
        reset_stale_queue_receipt(db, "event-a", operator=True)
    assert closed.value.code == "DISPATCH_CLOSED"


def test_mismatched_tenant_run_is_never_sent(db):
    db.add(Tenant(id="tenant-b", name="B"))
    db.add(OutboxEvent(
        id="event-b", tenant_id="tenant-b", topic="run.dispatch",
        payload={"run_id": "run-a"},
    ))
    db.commit()
    sqs = FakeSQS()
    assert publish_run_dispatches(db, sqs, "https://sqs.example.test/123/runs") == 1
    assert [json.loads(item["MessageBody"])["event_id"] for item in sqs.messages] == [
        "event-a"
    ]
    assert db.scalar(select(OutboxEvent).where(OutboxEvent.id == "event-b")).status == "failed"


def test_sqs_consumer_processes_exact_event_and_deletes_only_after_terminal_receipt(db):
    sqs = FakeSQS()
    url = "https://sqs.example.test/123/runs"
    publish_run_dispatches(db, sqs, url)
    called = []

    def finish(event_id):
        called.append(event_id)
        with Session(db.bind) as session:
            session.get(OutboxEvent, event_id).status = "delivered"
            session.commit()
        return "run-a"

    assert consume_one_run_dispatch(lambda: Session(db.bind), sqs, url, finish, wait_seconds=0)
    assert called == ["event-a"] and sqs.deleted == ["receipt-a"]


def test_sqs_consumer_leaves_unfinished_or_mismatched_message_for_retry(db):
    sqs = FakeSQS()
    url = "https://sqs.example.test/123/runs"
    publish_run_dispatches(db, sqs, url)
    assert not consume_one_run_dispatch(
        lambda: Session(db.bind), sqs, url, lambda _: None, wait_seconds=0
    )
    assert sqs.deleted == []
    body = json.loads(sqs.messages[0]["MessageBody"])
    body["tenant_id"] = "tenant-b"
    sqs.messages[0]["MessageBody"] = json.dumps(body)
    assert not consume_one_run_dispatch(
        lambda: Session(db.bind), sqs, url,
        lambda _: pytest.fail("scope mismatch executed"), wait_seconds=0,
    )
    assert sqs.deleted == []
