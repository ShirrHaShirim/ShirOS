"""Composition root for trusted local applications; no external model integration."""

from dataclasses import dataclass

from sqlalchemy import Engine

from shiros.adapters.providers.memory_mock import MockEmbeddingProvider, MockSummaryProvider
from shiros.context_compiler import SharedContextCompiler
from shiros.core.permissions import PermissionService
from shiros.core.privacy import RuleBasedPrivacyPolicy
from shiros.layers import MemoryLayerProcessor
from shiros.memory_service import MemoryService
from shiros.retrieval import RetrievalService
from shiros.review import ReviewAuthority


@dataclass(frozen=True)
class SharedMemoryServices:
    reviews: ReviewAuthority
    memory: MemoryService
    layers: MemoryLayerProcessor
    retrieval: RetrievalService
    context: SharedContextCompiler


def build_shared_memory(engine: Engine, permissions: PermissionService) -> SharedMemoryServices:
    privacy = RuleBasedPrivacyPolicy()
    reviews = ReviewAuthority(permissions, privacy)
    memory = MemoryService(engine, permissions, privacy, reviews, MockEmbeddingProvider())
    retrieval = RetrievalService(memory)
    return SharedMemoryServices(
        reviews=reviews,
        memory=memory,
        layers=MemoryLayerProcessor(memory, MockSummaryProvider()),
        retrieval=retrieval,
        context=SharedContextCompiler(retrieval),
    )
