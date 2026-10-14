"""Catalogue plans can edit a recording without echoing its inherited Work class."""

import importlib.util
from pathlib import Path
from uuid import uuid4

from shiros.domains.music import ObjectInput


def test_editable_recording_preserves_credits_without_derived_classification() -> None:
    spec = importlib.util.spec_from_file_location(
        "catalogue_plan", Path(__file__).resolve().parents[1] / "scripts/enrich_music_catalogue.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    performer = str(uuid4())
    obj = module.editable(
        {
            "kind": "recording",
            "title": "Synthetic recording",
            "music_class": "",
            "music_classes": ["classical"],
            "is_album_recording": True,
            "personal_notes": "Preserve private notes",
            "relations": [
                {
                    "object_id": performer,
                    "role": "performer",
                    "performance_role": "小提琴",
                    "source_text": "Synthetic credit source",
                }
            ],
            "tags": [],
            "identities": [],
        }
    )
    saved = ObjectInput.model_validate(obj)
    assert saved.music_classes == [] and saved.music_class == ""
    assert saved.personal_notes == "Preserve private notes"
    assert "is_album_recording" not in obj
    assert saved.relations[0].performance_role == "小提琴"
    assert str(saved.relations[0].object_id) == performer
