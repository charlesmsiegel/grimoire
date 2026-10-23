"""Bounded static sprite decoding shared by direct uploads and pack previews."""

import io

from PIL import Image, UnidentifiedImageError

from . import contracts

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 16_000_000
FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}


def validate(data: object) -> tuple[str, int]:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise contracts.ExpressionBudgetError("Sprite must be at most 20 MiB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            ext = FORMATS.get(image.format or "")
            pixels = image.width * image.height
            if pixels > MAX_PIXELS:
                raise contracts.ExpressionBudgetError("Sprite exceeds 16 megapixels")
            if not ext or getattr(image, "is_animated", False):
                raise contracts.UnsupportedSpriteError("Only static PNG, JPEG, WebP and GIF sprites are supported", pixels)
            image.load()
            return ext, pixels
    except contracts.ExpressionInvalidError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise contracts.ExpressionInvalidError(str(exc)) from exc
