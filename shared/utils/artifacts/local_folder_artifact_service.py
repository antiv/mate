"""Local folder artifact service implementation for Google ADK."""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Optional, Dict, Any

from google.adk.artifacts import BaseArtifactService
from google.adk.artifacts.base_artifact_service import ArtifactVersion
from google.genai import types
from typing_extensions import override

logger = logging.getLogger("adk_extra_services.artifacts.local_folder")


class LocalFolderArtifactService(BaseArtifactService):
    """Stores artifacts in the local filesystem."""

    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path).expanduser().resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _file_has_user_namespace(self, filename: str) -> bool:
        return filename.startswith("user:")

    def _strip_user_prefix(self, filename: str) -> str:
        """Remove the leading ``user:`` namespace marker, if present."""
        return filename[len("user:") :] if self._file_has_user_namespace(filename) else filename

    def _get_file_path(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
        version: int,
    ) -> Path:
        """Translate a logical filename into an on-disk path."""
        if self._file_has_user_namespace(filename):
            clean_name = self._strip_user_prefix(filename)
            parts = [app_name, user_id, "user", clean_name, str(version)]
        else:
            parts = [app_name, user_id, session_id, filename, str(version)]
        return self.base_path.joinpath(*parts)

    @override
    async def save_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
        artifact: types.Part,
        custom_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        versions = await self.list_versions(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        version = 0 if not versions else max(versions) + 1

        path = self._get_file_path(app_name, user_id, session_id, filename, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(artifact.inline_data.data)
        return version

    @override
    async def load_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
        version: Optional[int] = None,
    ) -> Optional[types.Part]:
        if version is None:
            versions = await self.list_versions(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                filename=filename,
            )
            if not versions:
                return None
            version = max(versions)

        path = self._get_file_path(app_name, user_id, session_id, filename, version)
        if not path.exists():
            return None

        data = path.read_bytes()
        mime, _ = mimetypes.guess_type(filename)
        if mime is None:
            mime = "text/plain"
        return types.Part.from_bytes(data=data, mime_type=mime)

    @override
    async def list_artifact_keys(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> list[str]:
        """Return all artifact filenames visible to the caller.

        This walks the on-disk directory tree and extracts the *logical*
        filenames by stripping the version component from the stored path and,
        for user-scoped artefacts, prepending the ``user:`` namespace.
        """
        keys: set[str] = set()

        def _collect(prefix: Path, add_user_prefix: bool = False) -> None:
            if not prefix.exists():
                return
            # Every stored artefact is saved under:
            #   <prefix>/<filename_path_components...>/<version>
            # where <version> is an integer file (not directory).  We therefore
            # look for *files* directly under any depth whose names are ints.
            for version_file in prefix.rglob("*"):
                if not version_file.is_file():
                    continue
                try:
                    int(version_file.name)
                except ValueError:
                    # Not a version file → skip
                    continue
                rel_parts = version_file.relative_to(prefix).parts[:-1]
                if not rel_parts:
                    # Malformed – skip
                    continue
                if rel_parts[0].startswith("user:"):
                    rel_parts = (rel_parts[0][len("user:"):],) + rel_parts[1:]
                logical_name = "/".join(rel_parts)
                if add_user_prefix:
                    logical_name = f"user:{logical_name}"
                keys.add(logical_name)

        # Session-scoped artefacts
        _collect(self.base_path / app_name / user_id / session_id)
        # User-scoped artefacts – prepend namespace
        _collect(self.base_path / app_name / user_id / "user", add_user_prefix=True)

        for user_key in {k for k in keys if k.startswith("user:")}:  # noqa: C401 – set comprehension fine here
            plain_key = user_key[len("user:"):]
            keys.discard(plain_key)

        return sorted(keys)

    @override
    async def delete_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
    ) -> None:
        versions = await self.list_versions(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        for ver in versions:
            path = self._get_file_path(app_name, user_id, session_id, filename, ver)
            if path.exists():
                try:
                    path.unlink()
                except OSError as exc:
                    logger.warning("Failed to delete %s: %s", path, exc)

    @override
    async def list_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
    ) -> list[int]:
        if self._file_has_user_namespace(filename):
            dir_path = self.base_path / app_name / user_id / "user" / self._strip_user_prefix(filename)
        else:
            dir_path = self.base_path / app_name / user_id / session_id / filename
        if not dir_path.exists():
            return []
        versions: list[int] = []
        for item in dir_path.iterdir():
            if item.is_file():
                try:
                    versions.append(int(item.name))
                except ValueError:
                    continue
        return sorted(versions)

    @override
    async def list_artifact_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> list[ArtifactVersion]:
        """Lists all versions and their metadata for a specific artifact."""
        versions = await self.list_versions(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        
        artifact_versions = []
        for version in versions:
            path = self._get_file_path(app_name, user_id, session_id or "", filename, version)
            if not path.exists():
                continue
                
            try:
                # Get file stats for metadata
                stat = path.stat()
                
                # Construct canonical URI that matches the local file structure
                if self._file_has_user_namespace(filename):
                    canonical_uri = f"file://{path.absolute()}"
                else:
                    canonical_uri = f"file://{path.absolute()}"
                
                # Get MIME type
                mime_type, _ = mimetypes.guess_type(filename)
                if mime_type is None:
                    mime_type = "application/octet-stream"
                
                artifact_version = ArtifactVersion(
                    version=version,
                    canonical_uri=canonical_uri,
                    mime_type=mime_type,
                    create_time=stat.st_mtime,
                )
                artifact_versions.append(artifact_version)
            except OSError as e:
                logger.warning(f"Failed to get metadata for version {version} of {filename}: {e}")
                continue
                
        return artifact_versions

    @override
    async def get_artifact_version(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> Optional[ArtifactVersion]:
        """Gets the metadata for a specific version of an artifact."""
        if version is None:
            versions = await self.list_versions(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                filename=filename,
            )
            if not versions:
                return None
            version = max(versions)
        
        path = self._get_file_path(app_name, user_id, session_id or "", filename, version)
        if not path.exists():
            return None
            
        try:
            # Get file stats for metadata
            stat = path.stat()
            
            # Construct canonical URI that matches the local file structure
            if self._file_has_user_namespace(filename):
                canonical_uri = f"file://{path.absolute()}"
            else:
                canonical_uri = f"file://{path.absolute()}"
            
            # Get MIME type
            mime_type, _ = mimetypes.guess_type(filename)
            if mime_type is None:
                mime_type = "application/octet-stream"
            
            return ArtifactVersion(
                version=version,
                canonical_uri=canonical_uri,
                mime_type=mime_type,
                create_time=stat.st_mtime,
            )
        except OSError as e:
            logger.warning(f"Failed to get metadata for version {version} of {filename}: {e}")
            return None