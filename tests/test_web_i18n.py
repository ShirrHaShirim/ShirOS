"""English messages cover system classifications and retain interpolation arguments."""

import json
import re
from pathlib import Path

from shiros.domains.music_identity_catalogue import GROUPS, PARTS
from shiros.domains.music_profiles import IDENTITIES
from shiros.domains.music_taxonomy import PRESETS

WEB = Path(__file__).parents[1] / "src/shiros/apps/web"


def catalogue():
    source = (WEB / "i18n-en.js").read_text(encoding="utf-8")
    return json.loads(source[source.index("{") : source.rindex("}") + 1])


def test_english_messages_and_interpolation_are_complete():
    for source, english in catalogue().items():
        assert english.strip(), source
        assert not re.search(r"[\u3400-\u9fff]", english) or source == "简体中文", source
        assert set(re.findall(r"\{\d+\}", source)) == set(re.findall(r"\{\d+\}", english)), source


def test_all_system_music_identities_and_genres_have_english_labels():
    names = {*PARTS, *GROUPS, *IDENTITIES, *PRESETS}
    for children in [*IDENTITIES.values(), *PRESETS.values()]:
        names.update(children)
    entries = catalogue()
    assert not {
        name for name in names if re.search(r"[\u3400-\u9fff]", name) and name not in entries
    }


def test_static_translation_precedes_user_content_modules():
    source = (WEB / "index.html").read_text(encoding="utf-8")
    assert source.index("/assets/i18n.js") < source.index("/assets/i18n-en.js")
    for module in ("app", "music", "files", "capture", "knowledge"):
        assert source.index("/assets/i18n-static.js") < source.index(f"/assets/{module}.js")
