import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from shiros.adapters.projection import MockWorkspaceProjection
from shiros.adapters.providers.mock import MockConversationProvider, MockWorker
from shiros.apps.api import create_app
from shiros.config import Settings
from shiros.core.context import ContextBundle
from shiros.core.conversations import AuthMethod, SendMessageRequest
from shiros.core.jobs import RunRequest
from shiros.core.permissions import AccessRequest, ExplicitGrantPermissions
from shiros.core.privacy import PrivacyInput, RuleBasedPrivacyPolicy
from shiros.core.schemas import Entity, EvidenceLevel, Provenance, Record
from shiros.services import ProjectionService, build_services


def make_entity(title: str = "Synthetic note") -> Entity:
    return Entity(
        kind="knowledge",
        title=title,
        provenance=Provenance(
            source_id=uuid4(),
            observed_at=datetime.now(UTC),
            created_by="synthetic-test",
            level=EvidenceLevel.EXPLICIT_STATEMENT,
            confidence=1,
        ),
    )


async def test_provider_response_and_fork() -> None:
    provider = MockConversationProvider()
    model = (await provider.list_models())[0]
    assert set(model.auth_methods) == set(AuthMethod)
    conversation = await provider.create_conversation(model.id)
    request = SendMessageRequest(
        conversation_id=conversation.id, request_id=uuid4(), content="hello"
    )
    message = await provider.send_message(request)
    assert message.content == "Mock: hello"
    assert message.conversation_id == conversation.id
    fork = await provider.create_conversation(model.id, parent_id=conversation.id)
    assert fork.parent_id == conversation.id and fork.id != conversation.id
    with pytest.raises(ValidationError):
        conversation.model_id = "another-model"  # type: ignore[misc]


async def test_provider_rejects_unknown_ids() -> None:
    provider = MockConversationProvider()
    with pytest.raises(ValueError, match="Unknown mock model"):
        await provider.create_conversation("real-model")
    with pytest.raises(ValueError, match="Unknown parent"):
        await provider.create_conversation("mock-echo-v1", parent_id=uuid4())
    with pytest.raises(ValueError, match="Unknown conversation"):
        await provider.send_message(
            SendMessageRequest(
                conversation_id=uuid4(),
                request_id=uuid4(),
                content="hello",
            )
        )


async def test_stream_cancel_is_request_scoped() -> None:
    provider = MockConversationProvider()
    conversation = await provider.create_conversation("mock-echo-v1")
    request = SendMessageRequest(
        conversation_id=conversation.id,
        request_id=uuid4(),
        content="a fairly long message",
    )
    stream = provider.stream_message(request)
    assert (await anext(stream)).delta
    await provider.cancel(request.request_id)
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)
    later = request.model_copy(update={"request_id": uuid4()})
    chunks = [chunk async for chunk in provider.stream_message(later)]
    assert chunks[-1].done
    assert "".join(chunk.delta for chunk in chunks) == "Mock: a fairly long message"
    await provider.cancel(uuid4())


async def test_worker_runs_are_isolated() -> None:
    worker = MockWorker()
    request = RunRequest(job_id=uuid4(), task="synthetic", context=ContextBundle(token_count=0))
    first, second = await worker.run(request), await worker.run(request)
    assert first.id != second.id
    assert first.job_id == request.job_id and first.status == "completed"
    assert worker.cost_profile.per_run == 0
    worker.availability = "unavailable"
    with pytest.raises(RuntimeError, match="unavailable"):
        await worker.run(request)


@pytest.mark.parametrize(
    "text",
    [
        "api_key=synthetic-not-a-real-key",
        "token: dummy-value",
        "Bearer synthetic-value",
        "password=dummy",
        "Cookie: session=synthetic",
        "sk-synthetic000000",
        "保密：今天讨论的事",
        "不要记录这个",
        "私下说",
        "do not save this",
    ],
)
def test_private_inputs_are_never_persistable(text: str) -> None:
    decision = RuleBasedPrivacyPolicy().evaluate(PrivacyInput(text=text, reviewed=True))
    assert not decision.persistence_allowed
    assert decision.safe_text is None


def test_privacy_redacts_contacts_and_preserves_names() -> None:
    policy = RuleBasedPrivacyPolicy()
    decision = policy.evaluate(
        PrivacyInput(
            text="SyntheticName synthetic@example.com +86 138-0000-0000 支付 42元",
            reviewed=True,
        )
    )
    assert decision.persistence_allowed
    assert decision.safe_text is not None
    assert "SyntheticName" in decision.safe_text
    assert "synthetic@example.com" not in decision.safe_text
    assert "138" not in decision.safe_text
    assert "42" not in decision.safe_text
    assert policy.redact("2026-10-06 v0.1.0.md") == "2026-10-06 v0.1.0.md"
    assert policy.redact("会议 2026-10-07T03:52 进行") == "会议 2026-10-07T03:52 进行"


def test_unknown_and_derived_data_fail_closed() -> None:
    policy = RuleBasedPrivacyPolicy()
    assert not policy.is_persistence_allowed(PrivacyInput(text="unknown"))
    assert not policy.is_persistence_allowed(
        PrivacyInput(
            text="innocent-looking summary",
            reviewed=True,
            source_persistence_allowed=False,
        )
    )


async def test_projection_stable_path_update_remove() -> None:
    adapter = MockWorkspaceProjection()
    entity = make_entity()
    path = await adapter.project(entity)
    changed = entity.model_copy(update={"title": "Renamed"})
    assert path == await adapter.project(changed) == f"{entity.id}.md"
    assert "# Renamed" in adapter.documents[entity.id]
    assert "schema_version: 1" in adapter.documents[entity.id]
    assert str(entity.provenance.source_id) in adapter.documents[entity.id]
    assert await adapter.detect_external_changes() == []
    await adapter.remove_projection(entity)
    await adapter.remove_projection(entity)
    assert adapter.documents == {}


async def test_projection_gate_denies_and_sanitizes() -> None:
    actor, entity = uuid4(), make_entity("Shir synthetic@example.com")
    adapter = MockWorkspaceProjection()
    permissions = ExplicitGrantPermissions(frozenset({(actor, "project", entity.id)}))
    service = ProjectionService(permissions, RuleBasedPrivacyPolicy(), adapter)
    with pytest.raises(PermissionError):
        await service.project(uuid4(), entity, reviewed=True)
    with pytest.raises(PermissionError):
        await service.project(actor, entity)
    with pytest.raises(PermissionError):
        await service.project(actor, entity, reviewed=True, source_persistence_allowed=False)
    assert not adapter.documents
    await service.project(actor, entity, reviewed=True)
    assert "synthetic@example.com" not in adapter.documents[entity.id]
    assert not ExplicitGrantPermissions().allows(
        AccessRequest(
            actor_id=actor,
            action="persist",
            resource_id=entity.id,
        )
    )


def test_id_time_and_schema_validation() -> None:
    first, second = Record(), Record()
    assert first.id != second.id and first.id.version == 4
    assert first.created_at.utcoffset() == UTC.utcoffset(None)
    assert Record.model_validate_json(first.model_dump_json()) == first
    assert Record(created_at="2026-10-06T10:00:00+08:00").created_at.hour == 2
    with pytest.raises(ValidationError):
        Record(created_at=datetime(2026, 10, 6))
    with pytest.raises(ValidationError):
        Record.model_validate({"id": "not-an-id"})
    with pytest.raises(ValidationError):
        Record.model_validate({"schema_version": 2})
    with pytest.raises(ValidationError):
        ContextBundle(token_count=-1)


def test_config_secret_repr_and_empty_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHIROS_DB_PASSWORD", raising=False)
    settings = Settings(_env_file=None, db_password="synthetic:@%password")
    assert "synthetic" not in repr(settings)
    assert "synthetic" not in str(settings.database_url())
    assert settings.database_url().password == "synthetic:@%password"
    with pytest.raises(ValueError, match="SHIROS_DB_PASSWORD"):
        Settings(_env_file=None).database_url()


async def test_api_live_and_unready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHIROS_DB_PASSWORD", "")
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        assert (await client.get("/health/live")).json()["service"] == "ShirOS"
        response = await client.get("/health/ready")
        assert response.status_code == 503 and response.json() == {"ready": False}


def test_composition() -> None:
    services = build_services()
    assert services.conversations.id == "mock"
    assert services.worker.id == "mock-worker"
