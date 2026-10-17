from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError


class MediaVerificationError(ValueError):
    """Raised when a downloaded media file cannot be verified."""


@dataclass(frozen=True, slots=True)
class VerifiedImage:
    format: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    sha256: str


class MediaVerifier:
    """Verifies image bytes and calculates metadata without trusting the filename."""

    def verify_image(self, path: Path) -> VerifiedImage:
        if not path.is_file():
            raise MediaVerificationError("Downloaded image file does not exist.")

        size_bytes = path.stat().st_size
        if size_bytes == 0:
            raise MediaVerificationError("Downloaded image file is empty.")

        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image_format = image.format
                width, height = image.size
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            raise MediaVerificationError(
                "Downloaded file is not a valid, readable image."
            ) from error

        if image_format is None or width <= 0 or height <= 0:
            raise MediaVerificationError(
                "Downloaded image has no recognized format or dimensions."
            )
        mime_type = Image.MIME.get(image_format)
        if mime_type is None:
            raise MediaVerificationError(
                "Downloaded image format does not have a supported MIME type."
            )

        digest = hashlib.sha256()
        with path.open("rb") as image_file:
            for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                digest.update(chunk)

        return VerifiedImage(
            format=image_format,
            mime_type=mime_type,
            width=width,
            height=height,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
        )
