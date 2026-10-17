from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from flow_automation.storage.media_verifier import (
    MediaVerificationError,
    MediaVerifier,
)


def test_media_verifier_validates_image_and_calculates_metadata(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "image.jpg"
    Image.new("RGB", (1376, 768), color="white").save(image_path, format="JPEG")

    verified = MediaVerifier().verify_image(image_path)

    assert verified.format == "JPEG"
    assert verified.mime_type == "image/jpeg"
    assert (verified.width, verified.height) == (1376, 768)
    assert verified.size_bytes == image_path.stat().st_size
    assert len(verified.sha256) == 64


@pytest.mark.parametrize("contents", [b"", b"not an image"])
def test_media_verifier_rejects_empty_or_invalid_files(
    tmp_path: Path,
    contents: bytes,
) -> None:
    image_path = tmp_path / "invalid.jpg"
    image_path.write_bytes(contents)

    with pytest.raises(MediaVerificationError):
        MediaVerifier().verify_image(image_path)
