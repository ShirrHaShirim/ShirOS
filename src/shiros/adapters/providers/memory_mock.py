"""Deterministic test providers, not a learned embedding or semantic summarizer."""

import hashlib
import math
import re

from shiros.adapters.providers import Embedding, SummaryOutput
from shiros.core.schemas import Provenance

DIMENSIONS = 32
MODEL = "hash-bow-32-v1"


class MockEmbeddingProvider:
    """Signed hashed token counts. Stable across processes, unlike Python hash()."""

    async def embed(self, texts: list[str]) -> list[Embedding]:
        result = []
        for text in texts:
            values = [0.0] * DIMENSIONS
            tokens = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]", text.casefold()) or ["<empty>"]
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                values[digest[0] % DIMENSIONS] += 1 if digest[1] % 2 else -1
            norm = math.sqrt(sum(value * value for value in values))
            if not norm:
                values[0], norm = 1.0, 1.0
            result.append(
                Embedding(
                    model=MODEL,
                    dimensions=DIMENSIONS,
                    values=tuple(v / norm for v in values),
                )
            )
        return result


class MockSummaryProvider:
    """Extractive bounded prefix; keeps original language and exact source metadata."""

    async def summarize(self, text: str, sources: tuple[Provenance, ...]) -> SummaryOutput:
        return SummaryOutput(text=text[:400], sources=sources)
