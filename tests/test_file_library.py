"""Archive byte integrity, access boundaries and memory export visibility."""

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_review_workflow import setup

from shiros.apps.workbench import create_workbench
from shiros.conversation_import import conversation_files
from shiros.file_library import FileLibrary, FileRequest, safe_filename
from shiros.markdown_export import memory_markdown


def exported_chat() -> bytes:
    return json.dumps(
        [
            {
                "id": "synthetic",
                "title": "合成对话",
                "current_node": "b",
                "mapping": {
                    "a": {
                        "parent": None,
                        "children": ["b", "c"],
                        "message": {"author": {"role": "user"}, "content": {"parts": ["你好"]}},
                    },
                    "b": {
                        "parent": "a",
                        "children": [],
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["回复甲"]},
                        },
                    },
                    "c": {
                        "parent": "a",
                        "children": [],
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["回复乙", {"image": "ref"}]},
                        },
                    },
                },
            }
        ],
        ensure_ascii=False,
    ).encode("utf-8")


def test_conversation_branches_and_zip() -> None:
    data = exported_chat()
    files = conversation_files(data, "conversations.json")
    markdown = files[0][1].decode("utf-8")
    assert all(
        x in markdown for x in ["你好", "回复甲", "回复乙", '"parent": "a"', '"image": "ref"']
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("conversations.json", data)
        z.writestr("../ignored", "not extracted")
    assert conversation_files(archive.getvalue(), "export.zip") == files
    with pytest.raises(ValueError, match="files.invalid_conversations"):
        conversation_files(b'[{"title":"no messages"}]', "conversations.json")
    with pytest.raises(ValueError):
        conversation_files(b"invalid zip", "export.zip")


def test_markdown_utf8_and_filename() -> None:
    result = memory_markdown(
        {
            "title": "记忆\n标题",
            "text": "完整正文\n# 原标题",
            "revision": 2,
            "source_records": [{"title": "来源", "text": "证据"}],
        }
    )
    assert "# 记忆 标题" in result and "完整正文\n# 原标题" in result and "证据" in result
    assert "/" not in safe_filename("../../bad/file.txt")


@pytest.mark.integration
def test_file_roundtrip_scope_permissions_and_sync(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    with db_engine.connect() as c:
        actor = workflow.identity.current(c)
    files = FileLibrary(db_engine, actor, scope)
    raw = b"\x00\xff<html>not rendered</html>"
    value = FileRequest(
        filename="原件.bin", data=base64.b64encode(raw).decode(), external_key="test:key"
    )
    first = files.run("upload", value)
    assert files.run("read", FileRequest(id=first["id"]))["content"] == raw
    second = files.run(
        "upload", value.model_copy(update={"data": base64.b64encode(b"updated").decode()})
    )
    assert first["id"] == second["id"]
    assert files.run("list", FileRequest())["total"] == 1
    other, other_scope = setup(db_engine, tmp_path / "other")
    with db_engine.connect() as c:
        other_actor = other.identity.current(c)
    with pytest.raises(ValueError, match="files.not_found"):
        FileLibrary(db_engine, other_actor, other_scope).run("read", FileRequest(id=first["id"]))
    with pytest.raises(ValueError):
        files.run("upload", FileRequest(data="invalid base64!"))
    before = files.run("list", FileRequest())["total"]
    with pytest.raises(ValueError):
        files.run("conversation-import", FileRequest(data=base64.b64encode(b"invalid").decode()))
    assert files.run("list", FileRequest())["total"] == before
    result = files.run(
        "conversation-import",
        FileRequest(filename="conversations.json", data=base64.b64encode(exported_chat()).decode()),
    )
    assert result == {"conversations": 1, "files": 2}
    with db_engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM memories WHERE scope_id=:s"), {"s": scope}) == 0
    files.run("delete", FileRequest(id=first["id"]))
    reader, reader_scope = setup(db_engine, tmp_path / "reader", "reader")
    with db_engine.connect() as c:
        reader_actor = reader.identity.current(c)
    with pytest.raises(PermissionError):
        FileLibrary(db_engine, reader_actor, reader_scope).run("upload", value)


@pytest.mark.integration
def test_http_export_visibility_download_and_core(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    token = "synthetic-token"
    app = create_workbench(db_engine, workflow.identity, scope, token=token, music_enabled=False)
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        assert client.post("/ui-api/export-md", json={}).status_code == 403
        client.headers["X-Shiros-Token"] = token
        preview = workflow.preview_text(scope, "Synthetic memory.md", "Synthetic full export text")
        candidate = workflow.stage(scope, preview.id)
        memory = workflow.approve(scope, candidate.id, candidate.revision)
        response = client.post("/ui-api/export-md", json={})
        assert response.status_code == 200 and "Synthetic full export text" in response.text
        single = client.post("/ui-api/export-md", json={"id": str(memory.memory_id)})
        assert (
            str(memory.memory_id) in single.text
            and "text/markdown" in single.headers["content-type"]
        )
        workflow.hide(scope, memory.memory_id, True)
        assert "Synthetic full export text" not in client.post("/ui-api/export-md", json={}).text
        assert (
            client.post("/ui-api/export-md", json={"id": str(memory.memory_id)}).status_code == 400
        )
        result = client.post(
            "/ui-api/files/upload",
            json={
                "filename": "test.html",
                "data": base64.b64encode(b"<script>alert(1)</script>").decode(),
            },
        )
        response = client.post("/ui-api/files/read", json={"id": result.json()["id"]})
        assert response.content == b"<script>alert(1)</script>"
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert client.post("/ui-api/music/list", json={}).status_code == 404
        assert '<script src="/assets/music.js"' not in client.get("/").text
