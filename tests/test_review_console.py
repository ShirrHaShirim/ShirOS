from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

from shiros.backup import BackupManager
from shiros.i18n import Locale, default_catalog
from shiros.identity import LocalIdentity
from shiros.review_console import ReviewConsole, escape_terminal
from shiros.review_workflow import ReviewWorkflow


class FakeWorkflow:
    def __init__(self) -> None:
        self.preview_id = uuid4()
        self.staged: list[tuple[UUID, UUID]] = []

    def preview(self, scope: UUID, filename: str) -> SimpleNamespace:
        assert filename == "note.txt"
        return SimpleNamespace(
            id=self.preview_id,
            title="note.txt",
            kind="text",
            original_text="raw\x1b[31m",
            normalized_text="safe\u202e text",
            privacy=SimpleNamespace(persistence_allowed=True),
        )

    def stage(self, scope: UUID, preview_id: UUID) -> SimpleNamespace:
        self.staged.append((scope, preview_id))
        return SimpleNamespace(id=uuid4())


def test_review_catalog_has_matching_zh_and_en_review_keys() -> None:
    resources = default_catalog()._resources["review"]
    assert set(resources[Locale.ZH_CN]) == set(resources[Locale.EN_US])
    assert resources[Locale.ZH_CN]


def test_preview_requires_confirmation_and_escapes_terminal_controls(tmp_path: Path) -> None:
    workflow = FakeWorkflow()
    inputs = iter(["1", "note.txt", "y", "0"])
    output: list[str] = []
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return next(inputs)

    console = ReviewConsole(
        cast(ReviewWorkflow, workflow),
        cast(LocalIdentity, SimpleNamespace()),
        uuid4(),
        tmp_path,
        cast(BackupManager, SimpleNamespace(output_root=tmp_path)),
        Locale.EN_US,
        ask,
        output.append,
    )

    console.run()

    assert workflow.staged[0][1] == workflow.preview_id
    rendered = "\n".join(output)
    assert "\\x1b[31m" in rendered
    assert "\\u202e" in rendered
    assert any("Stage this preview" in prompt for prompt in prompts)


def test_declined_preview_is_not_staged(tmp_path: Path) -> None:
    workflow = FakeWorkflow()
    inputs = iter(["1", "note.txt", "n", "0"])
    output: list[str] = []
    ReviewConsole(
        cast(ReviewWorkflow, workflow),
        cast(LocalIdentity, SimpleNamespace()),
        uuid4(),
        tmp_path,
        cast(BackupManager, SimpleNamespace(output_root=tmp_path)),
        Locale.ZH_CN,
        lambda _prompt: next(inputs),
        output.append,
    ).run()
    assert workflow.staged == []
    assert any("已取消" in message for message in output)


def test_unknown_errors_are_not_printed(tmp_path: Path) -> None:
    class BrokenWorkflow(FakeWorkflow):
        def queue(self, scope: UUID) -> None:
            raise RuntimeError("secret database connection details")

    inputs = iter(["2", "0"])
    output: list[str] = []
    ReviewConsole(
        cast(ReviewWorkflow, BrokenWorkflow()),
        cast(LocalIdentity, SimpleNamespace()),
        uuid4(),
        tmp_path,
        cast(BackupManager, SimpleNamespace(output_root=tmp_path)),
        Locale.EN_US,
        lambda _prompt: next(inputs),
        output.append,
    ).run()
    assert "secret database" not in "\n".join(output)
    assert any("Operation failed" in message for message in output)


def test_escape_terminal_escapes_controls_and_bidi_formatting() -> None:
    assert escape_terminal("a\x1b[2J\u202e") == "a\\x1b[2J\\u202e"
