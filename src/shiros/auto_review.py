"""Server-owned ordinary-content policy; not a semantic privacy guarantee."""

import asyncio
import re
import unicodedata
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from shiros.core.ingestion import MemoryWrite
from shiros.core.permissions import ExplicitGrantPermissions
from shiros.core.privacy import AMOUNT, PrivacyInput, RuleBasedPrivacyPolicy, Sensitivity
from shiros.identity import DatabasePermissions, audit
from shiros.shared_memory import build_shared_memory

POLICY_PRINCIPAL = "system:auto-review:ordinary-v1"
SENSITIVE = re.compile(
    r"(?i)\b(?:health|medical|diagnos\w*|medication|therapy|depress\w*|anxiety|"
    r"suicid\w*|pregnan\w*|sexual\w*|religio\w*|politic\w*|salary|income|debt|"
    r"bank\w*|passport|identity|address|location|relationship|divorc\w*|"
    r"partner|spouse|family|child\w*|trauma|abuse|legal|lawsuit|criminal|"
    r"sensitive|personal|private|third.party|uncertain|unknown|"
    r"christian\w*|muslim|islam\w*|jewish|judaism|buddhis\w*|hindu\w*|atheis\w*|"
    r"cancer|hiv|aids|diabet\w*|disabil\w*|ethnic\w*|race|nationality|"
    r"mother|father|parent\w*|sibling\w*|brother|sister|boyfriend|girlfriend)\b|"
    r"(?i:\b(?:i live|i reside|my home|my friend|my colleague|social security)\b)|"
    r"健康|医疗|病|药|心理|抑郁|焦虑|自杀|怀孕|性取向|性生活|宗教|政治|"
    r"工资|薪水|收入|债务|存款|银行|财务|身份证|护照|住址|地址|定位|"
    r"感情|婚|伴侣|家人|家庭|孩子|创伤|虐待|法律|诉讼|犯罪|敏感|隐私|"
    r"第三方|不确定|未知|待确认|基督|天主教|伊斯兰|穆斯林|犹太|佛教|印度教|"
    r"无神论|艾滋|肿瘤|癌|残疾|民族|种族|国籍|我住|我居住|我家在|"
    r"母亲|父亲|妈妈|爸爸|兄弟|姐妹|哥哥|弟弟|姐姐|妹妹|男友|女友|"
    r"我朋友|我的朋友|我同事|我的同事|\[REDACTED_|\[AMOUNT\]"
)


def review_reason(*values: str) -> str:
    """Classify originals before redaction; callers cannot supply this decision."""
    policy = RuleBasedPrivacyPolicy()
    for value in values:
        normalized = unicodedata.normalize("NFKC", value)
        for content in (value, normalized):
            decision = policy.evaluate(PrivacyInput(text=content, reviewed=True))
            if not decision.persistence_allowed:
                return "blocked_privacy"
            if (
                decision.sensitivity != Sensitivity.ORDINARY
                or AMOUNT.search(content)
                or SENSITIVE.search(content)
            ):
                return "requires_sensitive_review"
            if len(content) > 6000 or any(
                unicodedata.category(char) in {"Cf", "Co", "Cs"} for char in content
            ):
                return "requires_uncertain_review"
    return "auto_approve_ordinary_v1"


def approve_new_candidate(
    engine: Engine,
    connection: Connection,
    actor: UUID,
    scope: UUID,
    candidate: UUID,
    *,
    originals: tuple[str, ...],
) -> None:
    """Only called in the transaction that just created a pending candidate."""
    permissions = DatabasePermissions(connection)
    if not (
        permissions.has(actor, scope, "propose")
        or (permissions.has(actor, scope, "write") and permissions.has(actor, scope, "review"))
    ):
        raise PermissionError("permission.denied")
    row = (
        connection.execute(
            text(
                "SELECT c.*,r.title,r.text AS source_text,r.observed_at,s.revision,s.status "
                "FROM memory_candidates c JOIN review_sources r ON r.id=c.source_id "
                "JOIN LATERAL (SELECT revision,status FROM candidate_states "
                "WHERE candidate_id=c.id "
                "ORDER BY revision DESC LIMIT 1) s ON true WHERE c.id=:c AND c.scope_id=:s"
            ),
            {"c": candidate, "s": scope},
        )
        .mappings()
        .one()
    )
    reason = review_reason(*originals, row["title"], row["source_text"], row["original_text"])
    if reason != "auto_approve_ordinary_v1":
        return
    if row["status"] != "pending" or row["revision"] != 1 or row["created_by"] != actor:
        raise ValueError("candidate.auto_review_invalid")
    reviewer: UUID = connection.execute(
        text(
            "INSERT INTO local_identities(id,principal) VALUES (:id,:p) "
            "ON CONFLICT(principal) DO UPDATE SET principal=EXCLUDED.principal RETURNING id"
        ),
        {"id": uuid4(), "p": POLICY_PRINCIPAL},
    ).scalar_one()
    # These two ephemeral grants are scoped to this service instance, never persisted.
    services = build_shared_memory(
        engine,
        ExplicitGrantPermissions(
            frozenset(
                {
                    (actor, "persist", scope),
                    (reviewer, "review", scope),
                }
            )
        ),
    )
    request = MemoryWrite(
        scope_id=scope,
        idempotency_key=candidate,
        source_kind="note",
        source_title=row["title"],
        source_text=row["source_text"],
        text=row["original_text"],
        entity_title=row["title"],
        observed_at=row["observed_at"],
        confidence=row["confidence"],
        level=row["fact_level"],
    )
    approval = services.reviews.approve(reviewer, actor, request)
    memory = asyncio.run(services.memory._ingest(actor, request, approval, connection))
    connection.execute(
        text("INSERT INTO review_source_links VALUES (:intake,:source) ON CONFLICT DO NOTHING"),
        {"intake": row["source_id"], "source": memory.provenance.source_id},
    )
    connection.execute(
        text(
            "INSERT INTO candidate_states(id,candidate_id,revision,status,text,actor_id,"
            "reason_code,memory_revision_id) VALUES (:id,:c,2,'approved',:t,:a,:reason,:m)"
        ),
        {
            "id": uuid4(),
            "c": candidate,
            "t": row["original_text"],
            "a": reviewer,
            "reason": reason,
            "m": memory.id,
        },
    )
    audit(connection, reviewer, scope, candidate, "approve")
