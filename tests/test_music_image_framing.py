"""Framing changes presentation, preserving original images and catalogue revisions."""
import base64
import io
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import Engine
from test_music import run, service

from shiros.domains.music import MusicRequest


def image_data(color: str) -> str:
    output = io.BytesIO()
    Image.new('RGB', (80, 50), color).save(output, format='PNG')
    return base64.b64encode(output.getvalue()).decode()


@pytest.mark.integration
def test_image_framing_is_revision_guarded_and_retains_original_pixels(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    person = run(s, 'save', object={'kind': 'person', 'title': 'Synthetic Musician'})['item']
    with pytest.raises(ValueError, match='music.image_required'):
        run(s, 'image-frame', id=person['id'], revision=person['revision'], image_frame={'zoom': 2})
    person = run(
        s, 'image-upload', id=person['id'], revision=person['revision'],
        filename='synthetic.png', image_data=image_data('red'),
        image_source_url='https://example.test/portrait', image_credit='Synthetic image'
    )['item']
    image_id = person['image_id']
    original = run(s, 'image-read', id=image_id)['data']
    frame = {'x': 30, 'y': 20, 'zoom': 2.25}
    framed = run(
        s, 'image-frame', id=person['id'], revision=person['revision'], image_frame=frame
    )['item']
    assert framed['image_frame'] == frame
    assert framed['image_info'] == person['image_info']
    assert run(s, 'image-read', id=image_id)['data'] == original
    listed = run(s, 'list', kind='person')['items'][0]
    assert listed['image_frame'] == frame
    with pytest.raises(ValueError, match='music.revision_conflict'):
        run(s, 'image-frame', id=person['id'], revision=person['revision'], image_frame=frame)
    edited = run(
        s, 'save', id=framed['id'], revision=framed['revision'],
        object={'kind': 'person', 'title': 'Synthetic Musician', 'description': 'Public biography'}
    )['item']
    assert edited['image_frame'] == frame
    replaced = run(
        s, 'image-upload', id=edited['id'], revision=edited['revision'],
        filename='synthetic-new.png', image_data=image_data('blue')
    )['item']
    assert replaced['image_frame'] is None
    assert replaced['image_frames'][str(image_id)] == frame
    assert run(s, 'image-read', id=image_id)['data'] == original
    for invalid in [{'zoom': .9}, {'zoom': 4.1}, {'x': -1}, {'y': 101}]:
        with pytest.raises(ValidationError):
            MusicRequest.model_validate({'image_frame': invalid})
