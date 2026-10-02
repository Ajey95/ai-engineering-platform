import re
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from platform_app.db import Base
from platform_app.model_qualification import (
    MetadataAttestation,
    QualificationError,
    qualification_current,
    qualify_model_entry,
    register_model_entry,
)
from platform_app.models import ModelEntry, ModelRegistryEvent
from platform_app.providers import ProviderTurn
from platform_app.schemas import ModelRegister
from platform_app.tool_broker import CompletedToolCall


class ScriptedAdapter:
    provider = "openai"

    def __init__(self, fail_step=0):
        self.calls = 0
        self.fail_step = fail_step
        self.nonce = ""

    def generate(
        self, model, instruction, prompt, tools, max_output_tokens, previous=None, results=None
    ):
        self.calls += 1
        assert model == "model-a"
        assert max_output_tokens == 256
        usage = {"input_tokens": 10, "output_tokens": 5}
        if self.calls == 1:
            self.nonce = re.search(r"[0-9a-f]{24}", prompt).group()
            return ProviderTurn(
                "openai",
                "model-a-2026",
                self.nonce,
                (),
                "completed",
                usage,
                {"request_model": model},
            )
        if self.calls == 2:
            assert (
                tools["qualification_echo"].input_schema["properties"]["nonce"]["const"]
                == self.nonce
            )
            calls = (CompletedToolCall("call-a", "qualification_echo", {"nonce": self.nonce}),)
            if self.fail_step == 2:
                calls = ()
            return ProviderTurn(
                "openai", "model-a-2026", "", calls, "completed", usage, {"request_model": model}
            )
        assert previous.calls[0].call_id == "call-a"
        assert results == {"call-a": {"status": "ok", "nonce": self.nonce}}
        text = "wrong" if self.fail_step == 3 else self.nonce
        return ProviderTurn(
            "openai", "model-a-2026", text, (), "completed", usage, {"request_model": model}
        )


@pytest.fixture
def registry(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'registry.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(
            ModelEntry(
                id="entry-a",
                provider="openai",
                model_id="model-a",
                registry_revision="rev-a",
                state="registered",
                capabilities={},
                context_limit=32000,
                output_limit=1000,
                price_revision="price-a",
                price_per_m_input=Decimal("1"),
                price_per_m_output=Decimal("2"),
            )
        )
        db.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def attestation():
    return MetadataAttestation.from_dict(
        {
            "registry_revision": "rev-a",
            "context_limit": 32000,
            "output_limit": 1000,
            "price_revision": "price-a",
            "price_per_m_input": "1",
            "price_per_m_output": "2",
            "limits_source_url": "https://vendor.example/models/model-a",
            "pricing_source_url": "https://vendor.example/pricing",
            "effective_date": "2026-10-02",
        }
    )


def test_successful_live_probe_enables_only_attested_revision(registry, attestation, monkeypatch):
    adapter = ScriptedAdapter()
    result = qualify_model_entry(registry, "entry-a", adapter, attestation, "operator-a", True)
    assert adapter.calls == 3
    assert result["state"] == "enabled"
    assert result["resolved_model"] == "model-a-2026"
    with registry() as db:
        model = db.get(ModelEntry, "entry-a")
        assert qualification_current(model)
        assert [item.outcome for item in db.query(ModelRegistryEvent).all()] == [
            "validating",
            "enabled",
        ]
        assert model.capabilities["qualification"]["checks"] == [
            "text",
            "schema_validated_tool",
            "continuation",
            "usage",
        ]
        model.price_revision = "price-b"
        db.commit()
        assert not qualification_current(model)
        model.price_revision = "price-a"
        db.commit()
        monkeypatch.setattr("platform_app.model_qualification.adapter_digest", lambda: "changed")
        assert not qualification_current(model)


def test_failed_probe_disables_entry_and_keeps_no_live_marker(registry, attestation):
    with pytest.raises(QualificationError) as error:
        qualify_model_entry(
            registry, "entry-a", ScriptedAdapter(fail_step=2), attestation, "operator-a", True
        )
    assert error.value.code == "MODEL_QUALIFICATION_FAILED"
    with registry() as db:
        model = db.get(ModelEntry, "entry-a")
        assert model.state == "registered"
        assert model.validated_at is None
        assert model.capabilities["live_qualified"] is False
        assert model.capabilities["qualification"]["status"] == "failed"
        assert [item.outcome for item in db.query(ModelRegistryEvent).all()] == [
            "validating",
            "failed",
        ]


def test_mismatched_metadata_is_rejected_before_spend(registry, attestation):
    changed = MetadataAttestation.from_dict(
        {
            **attestation.__dict__,
            "context_limit": 64000,
            "price_per_m_input": "1",
            "price_per_m_output": "2",
        }
    )
    adapter = ScriptedAdapter()
    with pytest.raises(QualificationError) as error:
        qualify_model_entry(registry, "entry-a", adapter, changed, "operator-a", True)
    assert error.value.code == "MODEL_REVISION_CHANGED"
    assert adapter.calls == 0


def test_registration_keeps_declared_capabilities_untrusted(registry):
    with registry() as db:
        model = register_model_entry(
            db,
            ModelRegister(
                id="entry-b",
                provider="google",
                model_id="model-b",
                registry_revision="rev-b",
                context_limit=32000,
                output_limit=1000,
                price_revision="price-b",
                price_per_m_input=1,
                price_per_m_output=2,
                capabilities={"live_qualified": True, "database_fixture_only": True},
            ),
            "operator-a",
        )
        db.commit()
        assert model.state == "registered"
        assert model.capabilities["declared"]["live_qualified"] is True
        assert model.capabilities.get("live_qualified") is None
        event = db.query(ModelRegistryEvent).one()
        assert event.action == "register"
        assert event.outcome == "registered"
