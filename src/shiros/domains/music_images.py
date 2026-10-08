"""Validate imported bitmap images, strip metadata, normalize and bound storage."""

import base64
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 20_000_000


def normalize(encoded: str) -> tuple[bytes, int, int]:
    try:
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_BYTES:
            raise ValueError("music.image_too_large")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("music.image_format")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("music.image_too_large")
                image.load()
                result = ImageOps.exif_transpose(image).convert("RGBA")
                result.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                result.save(output, format="WEBP", quality=90, method=4)
                return output.getvalue(), result.width, result.height
    except (
        ValueError,
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        code = str(exc)
        raise ValueError(code if code.startswith("music.") else "music.invalid_image") from None
