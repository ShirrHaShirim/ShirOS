"""Conservative rule baseline, not a production PII or confidentiality detector."""

import re
from enum import StrEnum
from typing import Protocol

from shiros.core.schemas import Schema


class Sensitivity(StrEnum):
    ORDINARY = "ordinary"
    PERSONAL = "personal"
    SECRET = "secret"


class Confidentiality(StrEnum):
    STANDARD = "standard"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class PrivacyInput(Schema):
    """reviewed is supplied by trusted policy/human review, never public callers.

    Derived data must inherit source_persistence_allowed from its source.
    """

    text: str
    reviewed: bool = False
    source_persistence_allowed: bool = True


class PrivacyDecision(Schema):
    sensitivity: Sensitivity
    confidentiality: Confidentiality
    persistence_allowed: bool
    safe_text: str | None
    reason: str


class PrivacyPolicy(Protocol):
    def classify_sensitivity(self, text: str) -> Sensitivity: ...

    def classify_confidentiality(self, value: PrivacyInput) -> Confidentiality: ...

    def redact(self, text: str) -> str: ...

    def generalize(self, text: str) -> str: ...

    def is_persistence_allowed(self, value: PrivacyInput) -> bool: ...

    def evaluate(self, value: PrivacyInput) -> PrivacyDecision: ...


EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
# ISO dates (2026-10-06) are not phone numbers; keep them intact for provenance.
PHONE = re.compile(r"(?<!\w)(?!\d{4}-\d{2}-\d{2})(?:\+?\d[\d ()-]{6,}\d)(?!\w)")
SECRET = re.compile(
    r"(?i)(?:\b(?:api[_ -]?key|token|password|passwd|secret|cookie|authorization)\b"
    r"\s*[:=]\s*[^\r\n]+|\bBearer\s+\S+|\bsk-[\w-]{8,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
CONFIDENTIAL = re.compile(
    r"保密|不要外传|不要记录|私下说|暂时不能公开|密码|验证码|"
    r"(?i:confidential|off the record|do not (?:save|record|share)|private secret)"
)
AMOUNT = re.compile(r"(?:[$¥€]|RMB\s*|USD\s*)\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*元")


class RuleBasedPrivacyPolicy:
    """Deny secret/confidential/unreviewed inputs even after redaction.

    Known contact information may be persisted only in sanitized form after
    explicit review. Names are not removed by these rules.
    """

    def classify_sensitivity(self, text: str) -> Sensitivity:
        if SECRET.search(text):
            return Sensitivity.SECRET
        if EMAIL.search(text) or PHONE.search(text):
            return Sensitivity.PERSONAL
        return Sensitivity.ORDINARY

    def classify_confidentiality(self, value: PrivacyInput) -> Confidentiality:
        if not value.source_persistence_allowed or CONFIDENTIAL.search(value.text):
            return Confidentiality.RESTRICTED
        if SECRET.search(value.text):
            return Confidentiality.RESTRICTED
        return Confidentiality.STANDARD if value.reviewed else Confidentiality.UNKNOWN

    def redact(self, text: str) -> str:
        text = SECRET.sub("[REDACTED_SECRET]", text)
        text = EMAIL.sub("[REDACTED_EMAIL]", text)
        return PHONE.sub("[REDACTED_PHONE]", text)

    def generalize(self, text: str) -> str:
        return AMOUNT.sub("[AMOUNT]", text)

    def is_persistence_allowed(self, value: PrivacyInput) -> bool:
        return self.classify_confidentiality(value) == Confidentiality.STANDARD

    def evaluate(self, value: PrivacyInput) -> PrivacyDecision:
        allowed = self.is_persistence_allowed(value)
        return PrivacyDecision(
            sensitivity=self.classify_sensitivity(value.text),
            confidentiality=self.classify_confidentiality(value),
            persistence_allowed=allowed,
            safe_text=self.redact(self.generalize(value.text)) if allowed else None,
            reason="reviewed_and_sanitized" if allowed else "persistence_denied",
        )
