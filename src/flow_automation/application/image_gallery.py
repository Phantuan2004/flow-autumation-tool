from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flow_automation.core.models import (
    Asset,
    AssetKind,
    FileVerificationStatus,
    Scene,
)
from flow_automation.persistence.repository import FlowRepository
from flow_automation.storage.media_verifier import MediaVerificationError, MediaVerifier


@dataclass(frozen=True, slots=True)
class GalleryItem:
    scene: Scene
    asset: Asset
    path: Path
    selected: bool


class ImageGalleryService:
    """Reads verified image variants and persists a single approved variant per scene."""

    def __init__(
        self,
        repository: FlowRepository,
        *,
        verifier: MediaVerifier | None = None,
    ) -> None:
        self._repository = repository
        self._verifier = verifier or MediaVerifier()

    def list_project_images(self, project_id: str) -> list[GalleryItem]:
        project = self._repository.get_project(project_id)
        scenes = {
            scene.id: scene for scene in self._repository.list_scenes(project_id)
        }
        items: list[GalleryItem] = []
        for asset in self._repository.list_assets(
            project_id=project_id,
            kind=AssetKind.IMAGE,
        ):
            if asset.verification_status is not FileVerificationStatus.VERIFIED:
                continue
            scene = scenes.get(asset.scene_id or "")
            if scene is None:
                continue
            path = Path(project.root_path) / Path(asset.relative_path)
            items.append(
                GalleryItem(
                    scene=scene,
                    asset=asset,
                    path=path,
                    selected=scene.selected_image_asset_id == asset.id,
                )
            )
        return items

    def approve_image(self, scene_id: str, asset_id: str) -> Scene:
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        asset = self._repository.get_asset(asset_id)
        if asset.kind is not AssetKind.IMAGE or asset.scene_id != scene_id:
            raise ValueError("Only an image variant belonging to this scene can be approved.")
        path = Path(project.root_path) / Path(asset.relative_path)
        try:
            verified = self._verifier.verify_image(path)
        except MediaVerificationError:
            self._repository.update_asset_verification(
                asset.id,
                FileVerificationStatus.MISSING
                if not path.is_file()
                else FileVerificationStatus.INVALID,
            )
            raise
        if (
            asset.verification_status is not FileVerificationStatus.VERIFIED
            or asset.sha256 != verified.sha256
            or asset.size_bytes != verified.size_bytes
            or asset.mime_type != verified.mime_type
        ):
            self._repository.update_asset_verification(
                asset.id,
                FileVerificationStatus.INVALID,
            )
            raise MediaVerificationError(
                "Image metadata no longer matches the verified file; approval was refused."
            )
        return self._repository.select_image(scene_id, asset_id)
