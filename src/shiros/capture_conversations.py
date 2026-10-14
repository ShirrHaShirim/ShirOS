"""Durable, sanitized message assembly; the public record is one whole conversation."""

import json
import re
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from shiros.auto_review import review_reason
from shiros.identity import audit
from shiros.memory_limits import MAX_MEMORY_CHARS
from shiros.visibility import memory_visible

PRIVACY_OMITTED = "[本条消息原文及附件未收录:隐私检查阻止保存,需人工核对。]"


def conversation_memory_title(original: str) -> str:
    """Match the canonical title/type without guessing a domain from message text."""
    parts = [part.strip() for part in re.split(r"[｜|]", original)]
    if len(parts) == 3 and all(parts) and parts[2] in ("完整记录", "完整对话"):
        domain, topic = parts[:2]
    else:
        domain, topic = "对话", " ".join(original.replace("｜", " ").replace("|", " ").split())
    return f"{domain[:40]}｜{(topic or '未命名对话')[:150]}｜完整对话"


def assemble(c: Connection, service, grant, packet, prepared, *, title_omitted=False):
    params = {
        "scope": service.scope,
        "provider": grant["provider"],
        "conversation": grant["conversation_id"],
        "actor": service.actor,
        "consent": grant["consent_grant_id"],
    }
    row = (
        c.execute(
            text(
                "INSERT INTO capture_conversations(id,scope_id,provider,conversation_id,actor_id,"
                "consent_grant_id) VALUES(:id,:scope,:provider,:conversation,:actor,:consent) "
                "ON CONFLICT(scope_id,provider,conversation_id) DO UPDATE SET "
                "conversation_id=EXCLUDED.conversation_id RETURNING *"
            ),
            {**params, "id": uuid4()},
        )
        .mappings()
        .one()
    )
    if row["actor_id"] != service.actor:
        raise PermissionError("capture.conversation_owned_by_other")
    conversation = row["id"]
    if row["entry_id"]:
        entry = (
            c.execute(
                text(
                    "SELECT e.*,s.status,coalesce(e.memory_id,m.memory_id) AS resolved_memory,"
                    "mc.source_id "
                    "FROM capture_entries e LEFT JOIN memory_candidates mc ON mc.id=e.candidate_id "
                    "LEFT JOIN LATERAL(SELECT * FROM candidate_states "
                    "WHERE candidate_id=e.candidate_id "
                    "ORDER BY revision DESC LIMIT 1) s ON true "
                    "LEFT JOIN memories m ON m.id=s.memory_revision_id "
                    "WHERE e.id=:id"
                ),
                {"id": row["entry_id"]},
            )
            .mappings()
            .one()
        )
        inactive = entry["status"] in ("rejected", "invalidated")
        if entry["resolved_memory"]:
            revision = c.scalar(
                text("SELECT id FROM memories WHERE memory_id=:id ORDER BY revision DESC LIMIT 1"),
                {"id": entry["resolved_memory"]},
            )
            inactive |= not revision or not memory_visible(c, revision)
        if inactive and row["consent_grant_id"] != grant["consent_grant_id"]:
            # A fresh explicit grant creates a new copy; it does not unhide or approve the old one.
            messages = (
                c.execute(
                    text("SELECT * FROM capture_messages WHERE conversation_id=:id"),
                    {"id": conversation},
                )
                .mappings()
                .all()
            )
            c.execute(
                text(
                    "INSERT INTO capture_message_archives(id,conversation_id,entry_id,"
                    "consent_grant_id,"
                    "messages) VALUES(:id,:conversation,:entry,:consent,CAST(:messages AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "conversation": conversation,
                    "entry": row["entry_id"],
                    "consent": row["consent_grant_id"],
                    "messages": json.dumps(
                        [dict(m) for m in messages], default=str, ensure_ascii=False
                    ),
                },
            )
            c.execute(
                text("DELETE FROM capture_messages WHERE conversation_id=:id"), {"id": conversation}
            )
            c.execute(
                text("UPDATE capture_entries SET archived=true WHERE id=:id"),
                {"id": row["entry_id"]},
            )
            c.execute(
                text(
                    "UPDATE capture_conversations SET entry_id=NULL,consent_grant_id=:consent "
                    "WHERE id=:id"
                ),
                {"consent": grant["consent_grant_id"], "id": conversation},
            )
            row = dict(row, entry_id=None, consent_grant_id=grant["consent_grant_id"])
        elif not inactive and row["consent_grant_id"] != grant["consent_grant_id"]:
            c.execute(
                text("UPDATE capture_conversations SET consent_grant_id=:consent WHERE id=:id"),
                {"consent": grant["consent_grant_id"], "id": conversation},
            )
    rejected = set(
        c.scalars(
            text(
                "SELECT e.message_id FROM capture_entries e "
                "JOIN capture_grants g ON g.id=e.grant_id "
                "JOIN LATERAL(SELECT status FROM candidate_states "
                "WHERE candidate_id=e.candidate_id "
                "ORDER BY revision DESC LIMIT 1) s ON true WHERE g.scope_id=:scope "
                "AND g.provider=:provider AND g.conversation_id=:conversation "
                "AND s.status='rejected' AND NOT e.archived "
                "AND (g.id=:consent OR g.parent_grant_id=:consent) "
                "AND e.message_id<>'__conversation__'"
            ),
            params,
        )
    )
    legacy = []
    if row["entry_id"] is None:
        legacy = (
            c.execute(
                text(
                    "SELECT e.*,s.status,s.text AS draft,s.revision AS state_revision,"
                    "coalesce(e.memory_id,m.memory_id) AS resolved_memory, mc.source_id "
                    "FROM capture_entries e JOIN capture_grants g ON g.id=e.grant_id "
                    "LEFT JOIN memory_candidates mc ON mc.id=e.candidate_id "
                    "LEFT JOIN LATERAL (SELECT * FROM candidate_states "
                    "WHERE candidate_id=e.candidate_id "
                    "ORDER BY revision DESC LIMIT 1) s ON true "
                    "LEFT JOIN memories m ON m.id=s.memory_revision_id "
                    "WHERE g.scope_id=:scope AND g.provider=:provider "
                    "AND g.conversation_id=:conversation "
                    "AND e.message_id<>'__conversation__' AND NOT e.archived "
                    "ORDER BY e.created_at,e.message_id,e.part"
                ),
                params,
            )
            .mappings()
            .all()
        )
        groups = {}
        eligible_legacy = []
        for item in legacy:
            if item["status"] == "rejected":
                continue
            if item["source_id"] and c.scalar(
                text("SELECT 1 FROM revocations WHERE kind='intake_source' AND target_id=:id"),
                {"id": item["source_id"]},
            ):
                continue
            body = item["draft"] or ""
            if item["resolved_memory"]:
                current = (
                    c.execute(
                        text(
                            "SELECT id,text FROM memories WHERE memory_id=:id "
                            "ORDER BY revision DESC LIMIT 1"
                        ),
                        {"id": item["resolved_memory"]},
                    )
                    .mappings()
                    .first()
                )
                if not current or not memory_visible(c, current["id"]):
                    continue
                if item["status"] not in ("pending", "edited"):
                    body = current["text"]
            header, separator, content = body.partition("\n\n")
            if not separator:
                raise ValueError("capture.legacy_format")
            eligible_legacy.append(item)
            role_line = next(
                (line for line in header.splitlines() if line.startswith("【角色】")), ""
            )
            role = role_line.removeprefix("【角色】") or "conversation"
            time_line = next(
                (
                    line.removeprefix("【原始时间】")
                    for line in header.splitlines()
                    if line.startswith("【原始时间】")
                ),
                None,
            )
            names = next(
                (
                    line.removeprefix("【附件】").split("、")
                    for line in header.splitlines()
                    if line.startswith("【附件】")
                ),
                [],
            )
            group = groups.setdefault(
                item["message_id"],
                {
                    "role": role,
                    "parts": {},
                    "review": False,
                    "at": datetime.fromisoformat(time_line) if time_line else None,
                    "attachments": names,
                },
            )
            group["parts"][item["part"]] = content
            group["review"] |= item["status"] in ("pending", "edited")
        for position, (message_id, group) in enumerate(groups.items()):
            c.execute(
                text(
                    "INSERT INTO capture_messages(conversation_id,message_id,role,body,"
                    "position,needs_review,observed_at,attachments) "
                    "VALUES(:conversation,:id,:role,:body,:position,:review,:at,"
                    "CAST(:files AS jsonb)) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "conversation": conversation,
                    "id": message_id,
                    "role": group["role"],
                    "body": "".join(group["parts"][p] for p in sorted(group["parts"])),
                    "position": position,
                    "review": group["review"],
                    "at": group["at"],
                    "files": json.dumps(group["attachments"]),
                },
            )
        legacy = eligible_legacy
    next_position = c.scalar(
        text("SELECT coalesce(max(position)+1,0) FROM capture_messages WHERE conversation_id=:id"),
        {"id": conversation},
    )
    files = []
    for message, safe, attachments in prepared:
        if message.id in rejected:
            continue
        names = [name for _, name, _ in attachments]
        # Persist only sanitized text and names, never original raw text or attachment bytes here.
        c.execute(
            text(
                "INSERT INTO capture_messages(conversation_id,message_id,role,body,"
                "position,observed_at,"
                "attachments,needs_review) VALUES(:conversation,:id,:role,:body,:position,:at,"
                "CAST(:files AS jsonb),:review) ON CONFLICT(conversation_id,message_id) "
                "DO UPDATE SET "
                "role=EXCLUDED.role,body=EXCLUDED.body,observed_at=coalesce(EXCLUDED.observed_at,"
                "capture_messages.observed_at),attachments=EXCLUDED.attachments,"
                "needs_review=EXCLUDED.needs_review"
            ),
            {
                "conversation": conversation,
                "id": message.id,
                "role": message.role,
                "body": safe,
                "position": next_position,
                "at": message.observed_at,
                "files": json.dumps(names),
                "review": review_reason(message.text) != "auto_approve_ordinary_v1",
            },
        )
        next_position += 1
        files.extend(attachments)
    if packet.message_order:
        # The producer can send all known IDs while sending only changed message bodies.
        c.execute(
            text(
                "UPDATE capture_messages m SET position=o.position-1 FROM "
                "unnest(CAST(:ids AS text[])) WITH ORDINALITY o(message_id,position) "
                "WHERE m.conversation_id=:conversation AND m.message_id=o.message_id"
            ),
            {"conversation": conversation, "ids": list(dict.fromkeys(packet.message_order))},
        )
    messages = (
        c.execute(
            text(
                "SELECT * FROM capture_messages WHERE conversation_id=:id "
                "ORDER BY position,message_id"
            ),
            {"id": conversation},
        )
        .mappings()
        .all()
    )
    parts = []
    if not messages:
        raise ValueError("capture.no_messages")
    for message in messages:
        if message["role"] == "conversation":
            parts.append(message["body"])
            continue
        header = f"## {message['role']}\n消息: {message['message_id']}"
        if message["observed_at"]:
            header += f"\n原始时间: {message['observed_at'].isoformat()}"
        if message["attachments"]:
            header += "\n附件: " + "、".join(message["attachments"])
        parts.append(header + "\n\n" + message["body"])
    prefix = (
        f"【记录类型】完整对话\n【平台】{grant['provider']}\n"
        f"【来源会话】{grant['conversation_id']}\n【消息数】{len(messages)}\n"
    )
    generation = 1 + c.scalar(
        text("SELECT count(*) FROM capture_message_archives WHERE conversation_id=:id"),
        {"id": conversation},
    )
    prefix += f"【采集版本】{generation}\n"
    omissions = sum(m["body"] == PRIVACY_OMITTED for m in messages)
    if omissions:
        prefix += f"【隐私未收录】{omissions} 条消息仅保留占位，原文和附件未保存\n"
    if title_omitted:
        prefix += "【标题】原始标题因隐私检查未收录\n"
    if rejected:
        prefix += f"【未收录】{len(rejected)} 条已拒绝消息保持拒绝状态\n"
    if any(m["needs_review"] for m in messages):
        prefix += "【审核】对话含敏感或待确认内容，需人工审核\n"
    body = prefix + "\n" + "\n\n".join(parts)
    if len(body) > MAX_MEMORY_CHARS:
        raise ValueError("capture.conversation_too_large")
    return conversation, row["entry_id"], body, files, legacy, len(messages), len(rejected)


def merge_legacy(c: Connection, actor: UUID, entry: UUID, legacy) -> None:
    for item in legacy:
        if item["status"] == "rejected":
            continue
        if item["resolved_memory"]:
            c.execute(
                text(
                    "INSERT INTO capture_merged_memories(memory_id,entry_id) VALUES(:m,:e) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"m": item["resolved_memory"], "e": entry},
            )
        if item["status"] in ("pending", "edited"):
            c.execute(
                text(
                    "INSERT INTO candidate_states(id,candidate_id,revision,status,text,"
                    "actor_id,reason_code) VALUES(:id,:candidate,:revision,'invalidated',"
                    ":body,:actor,'manual_edit')"
                ),
                {
                    "id": uuid4(),
                    "candidate": item["candidate_id"],
                    "revision": item["state_revision"] + 1,
                    "body": item["draft"],
                    "actor": actor,
                },
            )
            audit(c, actor, item["scope_id"], item["candidate_id"], "edit")
        c.execute(
            text(
                "INSERT INTO capture_file_links(id,entry_id,file_id,attachment_id,"
                "filename,reference,state) "
                "SELECT gen_random_uuid(),:new,file_id,attachment_id,filename,reference,state "
                "FROM capture_file_links WHERE entry_id=:old ON CONFLICT(entry_id,attachment_id) "
                "DO UPDATE SET file_id=coalesce(EXCLUDED.file_id,capture_file_links.file_id)"
            ),
            {"new": entry, "old": item["id"]},
        )
