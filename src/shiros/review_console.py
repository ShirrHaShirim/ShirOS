"""Small bilingual, local-only adapter for the explicit review workflow."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from shiros.backup import BackupManager
from shiros.i18n import Locale, default_catalog
from shiros.identity import LocalIdentity, Role
from shiros.review_workflow import ReviewWorkflow

Input = Callable[[str], str]
Output = Callable[[str], None]

_ERROR_KEYS = {
    "permission.denied": "review.error_permission",
    "identity.not_initialized": "review.error_identity",
    "identity.invalid_principal": "review.error_identity",
    "intake.invalid_path": "review.error_path",
    "intake.invalid_type": "review.error_type",
    "intake.too_large": "review.error_size",
    "intake.changed_file": "review.error_path",
    "intake.invalid_content": "review.error_content",
    "intake.empty": "review.error_content",
    "privacy.persistence_denied": "review.error_privacy",
    "intake.preview_required": "review.error_privacy",
    "candidate.not_found": "review.error_candidate",
    "candidate.closed": "review.error_candidate",
    "memory.not_found": "review.error_memory",
    "source.not_found": "review.error_source",
    "source.revoked": "review.error_source",
    "source.invalid_kind": "review.error_source",
}


def escape_terminal(value: str) -> str:
    """Render all control characters visibly, including ANSI ESC and C1 controls."""
    rendered: list[str] = []
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf"}:
            code = ord(character)
            rendered.append(f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _json(value: Any) -> str:
    # Compact JSON has no formatting control characters; escaping the result also
    # covers C1 controls that json.dumps otherwise leaves inside string values.
    rendered = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return escape_terminal(rendered)


class ReviewConsole:
    def __init__(
        self,
        workflow: ReviewWorkflow,
        identity: LocalIdentity,
        scope: UUID,
        import_root: Path,
        backup_manager: BackupManager,
        locale: Locale = Locale.ZH_CN,
        input_fn: Input = input,
        output_fn: Output = print,
    ) -> None:
        self.workflow = workflow
        self.identity = identity
        self.scope = scope
        self.import_root = import_root
        self.backup_manager = backup_manager
        self.locale = Locale(locale)
        self.input = input_fn
        self.output = output_fn
        self.catalog = default_catalog()

    def _t(self, key: str, **values: object) -> str:
        return self.catalog.translate("review", key, self.locale).format(**values)

    def _ask_uuid(self, key: str) -> UUID:
        return UUID(self.input(self._t(key)).strip())

    def _error(self, error: Exception) -> None:
        code = error.args[0] if error.args and isinstance(error.args[0], str) else ""
        key = _ERROR_KEYS.get(code)
        self.output(self._t(key or "review.error_generic"))

    def _show_record(self, key: str, value: object) -> None:
        try:
            rendered = _json(value)
        except (TypeError, ValueError):
            self.output(self._t("review.json_error"))
            return
        self.output(self._t(key, record=rendered))

    def _preview_stage(self) -> None:
        preview = self.workflow.preview(self.scope, self.input(self._t("review.filename")).strip())
        self.output(
            self._t(
                "review.preview",
                title=escape_terminal(preview.title),
                kind=escape_terminal(preview.kind),
                allowed=self._t(
                    "review.privacy.allowed"
                    if preview.privacy.persistence_allowed
                    else "review.privacy.denied"
                ),
                original=escape_terminal(preview.original_text),
                normalized=escape_terminal(preview.normalized_text),
            )
        )
        if self.input(self._t("review.confirm_stage")).strip().lower() not in {"y", "yes"}:
            self.output(self._t("review.cancelled"))
            return
        candidate = self.workflow.stage(self.scope, preview.id)
        self.output(self._t("review.confirm", id=candidate.id))

    def _queue(self) -> None:
        queue = self.workflow.queue(self.scope)
        if not queue:
            self.output(self._t("review.queue_empty"))
            return
        self.output(self._t("review.queue_header"))
        for candidate in queue:
            self.output(
                f"{candidate.id} [{self._status(candidate.status)}] "
                f"{escape_terminal(candidate.text)}"
            )

    def _inspect(self) -> None:
        record = self.workflow.inspect(self.scope, self._ask_uuid("review.candidate_id"))
        self._show_record("review.inspect", record)

    def _edit(self) -> None:
        candidate = self.workflow.edit(
            self.scope,
            self._ask_uuid("review.candidate_id"),
            self.input(self._t("review.edit_text")),
        )
        self.output(self._t("review.updated", status=self._status(candidate.status)))

    def _reject(self) -> None:
        candidate = self.workflow.reject(self.scope, self._ask_uuid("review.candidate_id"))
        self.output(self._t("review.updated", status=self._status(candidate.status)))

    def _status(self, status: str) -> str:
        key = f"review.status.{status}"
        translated = self.catalog.translate("review", key, self.locale)
        return translated if translated != key else self._t("review.error_generic")

    def _approve(self) -> None:
        memory = self.workflow.approve(self.scope, self._ask_uuid("review.candidate_id"))
        self.output(self._t("review.approved", id=memory.memory_id))

    def _memory(self) -> None:
        memory = self.workflow.memory(self.scope, self._ask_uuid("review.memory_id"))
        if memory is None:
            self.output(self._t("review.memory_missing"))
        else:
            self._show_record("review.memory_found", memory.model_dump(mode="json"))

    def _revoke(self) -> None:
        kind = self.input(self._t("review.revoke_kind")).strip()
        target = self._ask_uuid("review.target_id")
        self.workflow.revoke(self.scope, target, kind)
        self.output(self._t("review.revoked"))

    def _visibility(self) -> None:
        action = self.input(self._t("review.visibility")).strip().lower()
        if action not in {"hide", "show"}:
            self.output(self._t("review.invalid_choice"))
            return
        self.workflow.hide(
            self.scope,
            self._ask_uuid("review.memory_id"),
            hidden=action == "hide",
        )
        self.output(self._t("review.visibility_done"))

    def _language(self) -> None:
        try:
            self.locale = Locale(self.input(self._t("review.locale")).strip())
        except ValueError:
            self.output(self._t("review.locale_invalid"))

    def _grant_owner(self) -> None:
        self.workflow.require_admin(self.scope)
        principal = self.input(self._t("review.principal")).strip()
        roles = {"1": Role.OWNER, "2": Role.REVIEWER, "3": Role.READER, "4": Role.WORKER}
        role = roles.get(self.input(self._t("review.role")).strip())
        if role is None:
            self.output(self._t("review.invalid_choice"))
            return
        self.identity.grant(self.scope, principal, role)
        self.output(self._t("review.owner_granted"))

    def _backup(self) -> None:
        self.workflow.require_backup_admin(self.scope)
        archive = self.backup_manager.create_backup()
        self.output(self._t("review.backup_created", path=archive.name))

    def _restore(self) -> None:
        self.workflow.require_backup_admin(self.scope)
        filename = self.input(self._t("review.archive")).strip()
        if not filename or Path(filename).name != filename or Path(filename).suffix != ".dump":
            self.output(self._t("review.error_backup"))
            return
        archive = self.backup_manager.output_root / filename
        database_name = self.input(self._t("review.restore_name")).strip()
        self.backup_manager.restore(archive, database_name)
        self.output(self._t("review.restored", name=database_name))

    def run(self) -> None:
        self.output(self._t("review.title"))
        actions: dict[str, Callable[[], None]] = {
            "1": self._preview_stage,
            "2": self._queue,
            "3": self._inspect,
            "4": self._edit,
            "5": self._reject,
            "6": self._approve,
            "7": self._memory,
            "8": self._revoke,
            "9": self._visibility,
            "10": self._language,
            "11": self._grant_owner,
            "12": self._backup,
            "13": self._restore,
        }
        while True:
            self.output(self._t("review.menu"))
            try:
                choice = self.input(self._t("review.prompt")).strip()
            except (KeyboardInterrupt, EOFError):
                self.output(self._t("review.goodbye"))
                return
            if choice == "0":
                self.output(self._t("review.goodbye"))
                return
            action = actions.get(choice)
            if action is None:
                self.output(self._t("review.invalid_choice"))
                continue
            try:
                action()
            except (KeyboardInterrupt, EOFError):
                self.output(self._t("review.goodbye"))
                return
            except Exception as error:
                self._error(error)


def run_review_console(
    workflow: ReviewWorkflow,
    identity: LocalIdentity,
    scope: UUID,
    import_root: Path,
    backup_manager: BackupManager,
    locale: Locale = Locale.ZH_CN,
    input_fn: Input = input,
    output_fn: Output = print,
) -> None:
    ReviewConsole(
        workflow,
        identity,
        scope,
        import_root,
        backup_manager,
        locale,
        input_fn,
        output_fn,
    ).run()
