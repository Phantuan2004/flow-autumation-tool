from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FlowCapabilities:
    """Verified UI capabilities; unknown values are deliberately left empty."""

    verified: bool = False
    image_generation: bool = False
    image_collection: bool = False
    video_generation: bool = False
    asset_download: bool = False
    models: tuple[str, ...] = ()
    aspect_ratios: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    variant_counts: tuple[int, ...] = ()
    max_concurrency: int | None = None
    observations: tuple[str, ...] = ()

    @classmethod
    def unverified(cls, reason: str) -> FlowCapabilities:
        return cls(observations=(reason,))
