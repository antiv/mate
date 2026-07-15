"""
Artifact storage for the LangGraph runtime.

Reuses the existing artifact service backends (local folder / S3 / Supabase —
selected by the same ARTIFACT_SERVICE env logic as adk_main.py). Their methods
are self-contained async functions over google.genai types.Part, so no new
interface is needed.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _create_service() -> Any:
    from shared.utils.artifacts import (
        LocalFolderArtifactService,
        S3ArtifactService,
        SupabaseArtifactService,
    )

    service_type = os.getenv("ARTIFACT_SERVICE", "none").lower()
    if service_type == "local_folder":
        artifact_dir = PROJECT_ROOT / "artifacts"
        artifact_dir.mkdir(exist_ok=True, parents=True)
        logger.info(f"Artifact service: local folder at {artifact_dir}")
        return LocalFolderArtifactService(base_path=str(artifact_dir))
    if service_type == "s3":
        logger.info("Artifact service: S3")
        return S3ArtifactService(
            bucket_name=os.getenv("DISTRIBUTION_S3_BUCKET_NAME", "test-bucket"),
            endpoint_url=os.getenv("DISTRIBUTION_S3_ENDPOINT"),
        )
    if service_type == "supabase":
        logger.info("Artifact service: Supabase")
        return SupabaseArtifactService(
            url=os.getenv("SUPABASE_URL"),
            key=os.getenv("SUPABASE_KEY"),
            bucket_name=os.getenv("SUPABASE_BUCKET", "artifacts"),
        )
    from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
    logger.info("Artifact service: in-memory (ARTIFACT_SERVICE not set)")
    return InMemoryArtifactService()


class ArtifactAdapter:

    def __init__(self):
        self._service = _create_service()

    async def save(self, app_name: str, user_id: str, session_id: str,
                   filename: str, artifact: Any) -> int:
        return await self._service.save_artifact(
            app_name=app_name, user_id=user_id, session_id=session_id,
            filename=filename, artifact=artifact)

    async def load(self, app_name: str, user_id: str, session_id: str,
                   filename: str, version: Optional[int] = None) -> Any:
        return await self._service.load_artifact(
            app_name=app_name, user_id=user_id, session_id=session_id,
            filename=filename, version=version)

    async def list_versions(self, app_name: str, user_id: str, session_id: str,
                            filename: str) -> List[int]:
        return await self._service.list_versions(
            app_name=app_name, user_id=user_id, session_id=session_id,
            filename=filename)

    async def list_keys(self, app_name: str, user_id: str, session_id: str) -> List[str]:
        return await self._service.list_artifact_keys(
            app_name=app_name, user_id=user_id, session_id=session_id)


def part_to_wire(part: Any) -> Optional[Dict[str, Any]]:
    """Serialize a types.Part to the {"inlineData": {...}} JSON the frontends parse."""
    import base64
    inline = getattr(part, "inline_data", None)
    if inline is None or inline.data is None:
        return None
    return {"inlineData": {
        "mimeType": inline.mime_type,
        "data": base64.b64encode(inline.data).decode("ascii"),
    }}


_artifact_adapter: Optional[ArtifactAdapter] = None


def get_artifact_adapter() -> ArtifactAdapter:
    global _artifact_adapter
    if _artifact_adapter is None:
        _artifact_adapter = ArtifactAdapter()
    return _artifact_adapter
