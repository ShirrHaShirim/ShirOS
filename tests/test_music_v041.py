"""Genre isolation, hierarchical filters, optional tags and real bitmap import."""

import base64
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import Engine
from test_music import service
from test_review_workflow import setup

from shiros.apps.workbench import create_workbench
from shiros.domains.music import MusicRequest, MusicService
from shiros.domains.music_images import normalize
from shiros.tags import create_tag, list_tags


def bitmap(color: str = "red") -> str:
    stream = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private-test-metadata", "should-not-survive")
    Image.new("RGB", (80, 40), color).save(stream, format="PNG", pnginfo=metadata)
    return base64.b64encode(stream.getvalue()).decode("ascii")


def test_image_decode_normalization_and_metadata_removal() -> None:
    content, width, height = normalize(bitmap())
    with Image.open(io.BytesIO(content)) as image:
        assert image.format == "WEBP" and (width, height) == (80, 40)
        assert "private-test-metadata" not in image.info
    for invalid in ["invalid!", base64.b64encode(b"<svg></svg>").decode("ascii")]:
        with pytest.raises(ValueError, match="music.invalid_image"):
            normalize(invalid)


@pytest.mark.integration
def test_music_taxonomy_independence_subtree_and_untagged(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    memory_tag = create_tag(db_engine, s.actor, s.scope, "音乐")
    assert s.run("taxonomy", MusicRequest())["items"] == []
    root = s.run("taxonomy-save", MusicRequest(name="音乐", namespace="tag"))["node"]
    child = s.run("taxonomy-save", MusicRequest(name="合成收藏", parent_id=root["id"]))["node"]
    genre = s.run("taxonomy-save", MusicRequest(name="音乐", namespace="genre"))["node"]
    item = s.run("save", MusicRequest(object={"title": "合成有标签", "tag_ids": [child["id"]]}))[
        "item"
    ]
    genre_only = s.run(
        "save", MusicRequest(object={"title": "合成只有风格", "genre_ids": [genre["id"]]})
    )["item"]
    assert genre_only["tags"] == [] and len(genre_only["genres"]) == 1
    assert s.run("list", MusicRequest(untagged=True))["total"] == 1
    assert s.run("list", MusicRequest(tag_id=root["id"]))["total"] == 1
    assert s.run("list", MusicRequest(genre_id=genre["id"], untagged=True))["total"] == 1
    for obj in [
        {"title": "不应接受记忆标签", "tag_ids": [memory_tag["id"]]},
        {"title": "不应把 Genre 当私人标签", "tag_ids": [genre["id"]]},
    ]:
        with pytest.raises(ValueError):
            s.run("save", MusicRequest(object=obj))
    with pytest.raises(ValueError, match="music.taxonomy_cycle"):
        s.run(
            "taxonomy-save",
            MusicRequest(id=root["id"], revision=1, name="音乐", parent_id=child["id"]),
        )
    with pytest.raises(ValueError, match="music.taxonomy_namespace"):
        s.run(
            "taxonomy-save",
            MusicRequest(name="错误父节点", namespace="genre", parent_id=child["id"]),
        )
    s.run("taxonomy-save", MusicRequest(id=root["id"], revision=1, name="新私人目录"))
    assert list_tags(db_engine, s.actor, s.scope)[0]["name"] == "音乐"
    item = s.run(
        "save",
        MusicRequest(
            id=item["id"], revision=item["revision"], object={"title": item["title"], "tag_ids": []}
        ),
    )["item"]
    assert item["tags"] == [] and s.run("list", MusicRequest(untagged=True))["total"] == 2


@pytest.mark.integration
def test_genre_bootstrap_is_once_and_preserves_edits(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    root = s.run("taxonomy-save", MusicRequest(name="古典", namespace="genre"))["node"]
    initial = s.run("taxonomy-seed", MusicRequest())["items"]
    assert all(node["namespace"] == "genre" for node in initial)
    s.run(
        "taxonomy-save", MusicRequest(id=root["id"], revision=1, name="古典音乐", namespace="genre")
    )
    again = s.run("taxonomy-seed", MusicRequest())["items"]
    assert len(again) == len(initial)
    assert any(n["name"] == "古典音乐" for n in again)
    assert not any(n["name"] == "古典" for n in again)


@pytest.mark.integration
def test_image_import_scope_revision_replacement_and_export(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    item = s.run("save", MusicRequest(object={"title": "合成头像", "kind": "person"}))["item"]
    item = s.run(
        "image-upload",
        MusicRequest(
            id=item["id"], revision=item["revision"], image_data=bitmap(), filename="synthetic.png"
        ),
    )["item"]
    original = item["image_id"]
    assert s.run("image-read", MusicRequest(id=original))["media_type"] == "image/webp"
    with pytest.raises(ValueError, match="music.revision_conflict"):
        s.run("image-upload", MusicRequest(id=item["id"], revision=1, image_data=bitmap()))
    other = service(db_engine, tmp_path)
    with pytest.raises(ValueError, match="music.image_not_found"):
        other.run("image-read", MusicRequest(id=original))
    item = s.run(
        "image-upload",
        MusicRequest(id=item["id"], revision=item["revision"], image_data=bitmap("blue")),
    )["item"]
    assert item["image_id"] != original
    assert s.run("image-read", MusicRequest(id=original))["data"]
    item = s.run("image-remove", MusicRequest(id=item["id"], revision=item["revision"]))["item"]
    assert item["image_id"] is None
    exported = s.run("export", MusicRequest())["tables"]["music_images"]
    assert len(exported) == 2 and all(base64.b64decode(a["content"]) for a in exported)


@pytest.mark.integration
def test_image_http_boundary_and_reader_denial(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    with db_engine.connect() as c:
        actor = workflow.identity.current(c)
    s = MusicService(db_engine, actor, scope)
    item = s.run("save", MusicRequest(object={"title": "合成封面"}))["item"]
    app = create_workbench(db_engine, workflow.identity, scope, token="synthetic-image-token")
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        client.headers["X-Shiros-Token"] = "synthetic-image-token"
        response = client.post(
            "/ui-api/music/image-upload",
            json={"id": str(item["id"]), "revision": item["revision"], "image_data": bitmap()},
        )
        assert response.status_code == 200, response.text
        image_id = response.json()["item"]["image_id"]
        image = client.post("/ui-api/music/image-read", json={"id": image_id})
        assert image.status_code == 200 and image.headers["content-type"] == "image/webp"
        assert image.content.startswith(b"RIFF")
        client.headers.pop("X-Shiros-Token")
        assert client.post("/ui-api/music/image-read", json={"id": image_id}).status_code == 403
    reader = service(db_engine, tmp_path, "reader")
    with pytest.raises(PermissionError):
        reader.run("image-upload", MusicRequest(id=item["id"], revision=1, image_data=bitmap()))
