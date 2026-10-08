"""Contract-only ports have annotation checks; implementations have behavioral tests."""

import inspect
from typing import get_type_hints

import pytest

from shiros.adapters.projection import WorkspaceProjectionAdapter
from shiros.adapters.providers import EmbeddingProvider, SummaryProvider
from shiros.adapters.storage import StorageAdapter
from shiros.core.context import ContextCompiler
from shiros.core.conversations import ConversationProvider, ConversationRepository
from shiros.core.entities import EntityRepository
from shiros.core.events import EventStore
from shiros.core.jobs import AgentWorker
from shiros.core.memory import MemoryRepository
from shiros.core.permissions import PermissionService
from shiros.core.privacy import PrivacyPolicy


@pytest.mark.parametrize(
    "port",
    [
        EntityRepository,
        EventStore,
        MemoryRepository,
        ConversationRepository,
        StorageAdapter,
        WorkspaceProjectionAdapter,
        EmbeddingProvider,
        SummaryProvider,
        ConversationProvider,
        AgentWorker,
        PrivacyPolicy,
        PermissionService,
        ContextCompiler,
    ],
)
def test_port_annotations_are_complete_and_resolvable(port: type[object]) -> None:
    methods = [
        member
        for name, member in vars(port).items()
        if not name.startswith("_") and inspect.isfunction(member)
    ]
    assert methods
    for method in methods:
        hints = get_type_hints(method)
        assert "return" in hints
        for name in inspect.signature(method).parameters:
            if name != "self":
                assert name in hints
