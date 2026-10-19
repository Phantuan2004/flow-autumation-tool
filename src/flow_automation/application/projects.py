from __future__ import annotations

from pathlib import Path

from flow_automation.core.models import Project, Scene, SceneDraft, new_id
from flow_automation.persistence.repository import FlowRepository


class ProjectService:
    """Application operations for selecting projects and adding scene prompts."""

    def __init__(self, repository: FlowRepository, assets_root: Path) -> None:
        self._repository = repository
        self._assets_root = assets_root

    def list_projects(self) -> list[Project]:
        return self._repository.list_projects(include_archived=False)

    def create_project(self, name: str) -> Project:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Project name must not be empty.")
        project_id = new_id()
        root_path = self._assets_root / "projects" / project_id
        root_path.mkdir(parents=True, exist_ok=True)
        return self._repository.create_project(
            clean_name,
            str(root_path),
            project_id=project_id,
        )

    def add_scene(self, project_id: str, image_prompt: str) -> Scene:
        scenes = self._repository.list_scenes(project_id)
        scene = self._repository.create_scenes(
            project_id,
            [SceneDraft(len(scenes) + 1, image_prompt)],
        )[0]
        return scene
