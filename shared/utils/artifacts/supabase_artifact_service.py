"""
Supabase Artifact Service for ADK.

Implements artifact storage using Supabase Storage, similar to S3ArtifactService.
"""

import os
import base64
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
from google.adk.artifacts.base_artifact_service import BaseArtifactService, ArtifactVersion
from google.genai import types
from typing_extensions import override

logger = logging.getLogger(__name__)


class SupabaseArtifactService(BaseArtifactService):
    """
    Artifact service that stores artifacts in Supabase Storage.
    
    Environment variables required:
    - SUPABASE_URL: Supabase project URL
    - SUPABASE_KEY: Supabase API key
    - SUPABASE_BUCKET: Supabase storage bucket name (optional, defaults to 'artifacts')
    """
    
    def __init__(
        self,
        url: Optional[str] = None,
        key: Optional[str] = None,
        bucket_name: Optional[str] = None
    ):
        """
        Initialize SupabaseArtifactService.
        
        Args:
            url: Supabase URL (defaults to SUPABASE_URL env var)
            key: Supabase key (defaults to SUPABASE_KEY env var)
            bucket_name: Storage bucket name (defaults to SUPABASE_BUCKET env var or 'artifacts')
        """
        self.url = url or os.getenv("SUPABASE_URL")
        self.key = key or os.getenv("SUPABASE_KEY")
        self.bucket_name = bucket_name or os.getenv("SUPABASE_BUCKET", "artifacts")
        
        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be provided or set in environment")
        
        self._client = None
        logger.info(f"Initialized SupabaseArtifactService with bucket: {self.bucket_name}")
    
    def _get_client(self):
        """Get or create Supabase client (lazy initialization)."""
        if self._client is None:
            try:
                from supabase import create_client, ClientOptions
            except ImportError:
                raise RuntimeError(
                    "supabase-py is not installed. Please run: pip install supabase"
                )
            
            # Create client with proper options
            options = ClientOptions(
                auto_refresh_token=False,
                persist_session=False,
            )
            self._client = create_client(self.url, self.key, options=options)
        return self._client
    
    def _get_storage(self):
        """Get storage bucket instance."""
        client = self._get_client()
        return client.storage.from_(self.bucket_name)
    
    def _file_has_user_namespace(self, filename: str) -> bool:
        """Check if filename has user namespace prefix."""
        return filename.startswith("user:")
    
    def _get_object_key(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
        version: int,
    ) -> str:
        """Construct the storage key/path for an artifact."""
        # Prepend 'public' to match RLS policy requirement for anon access
        if self._file_has_user_namespace(filename):
            return f"public/{app_name}/{user_id}/user/{filename}/{version}"
        
        # Handle case where session_id might be empty string
        if session_id:
            return f"public/{app_name}/{user_id}/{session_id}/{filename}/{version}"
        else:
            return f"public/{app_name}/{user_id}/{filename}/{version}"
    
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
        """Save an artifact to Supabase Storage."""
        versions = await self.list_versions(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        version = 0 if not versions else max(versions) + 1

        key = self._get_object_key(app_name, user_id, session_id, filename, version)
        
        # Extract data from artifact
        file_bytes = artifact.inline_data.data
        mime_type = artifact.inline_data.mime_type
        
        # Upload to Supabase
        storage = self._get_storage()
        file_options = {"content-type": mime_type}
        
        try:
            storage.upload(key, file_bytes, file_options=file_options)
        except Exception as upload_error:
            # If file exists, try update
            if "already exists" in str(upload_error).lower():
                storage.update(key, file_bytes, file_options=file_options)
            else:
                raise
        
        logger.info(f"Saved artifact to Supabase: {key} (version {version})")
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
        """Load an artifact from Supabase Storage."""
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

        key = self._get_object_key(app_name, user_id, session_id, filename, version)
        
        try:
            storage = self._get_storage()
            file_bytes = storage.download(key)
            
            # Try to infer mime type from filename
            mime_type = "application/octet-stream"
            if filename.endswith('.png'):
                mime_type = "image/png"
            elif filename.endswith('.jpg') or filename.endswith('.jpeg'):
                mime_type = "image/jpeg"
            elif filename.endswith('.txt'):
                mime_type = "text/plain"
            
            return types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
            
        except Exception as e:
            logger.warning(f"Failed to load artifact from Supabase: {e}")
            return None
    
    @override
    async def list_artifact_keys(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> list[str]:
        """List all artifact filenames for a session."""
        filenames = set()
        storage = self._get_storage()

        # List session artifacts (with 'public' prefix for RLS)
        session_prefix = f"public/{app_name}/{user_id}/{session_id}/"
        try:
            files = storage.list(session_prefix)
            for file in files:
                key = file.get("name", "")
                parts = key.split("/")
                if len(parts) >= 6:  # public/app/user/session/filename/version
                    filenames.add(parts[4])
        except Exception as e:
            logger.warning(f"Failed to list session artifacts: {e}")

        # List user-namespaced artifacts (with 'public' prefix for RLS)
        user_prefix = f"public/{app_name}/{user_id}/user/"
        try:
            files = storage.list(user_prefix)
            for file in files:
                key = file.get("name", "")
                parts = key.split("/")
                if len(parts) >= 6:  # public/app/user/user/filename/version
                    filenames.add(parts[4])
        except Exception as e:
            logger.warning(f"Failed to list user artifacts: {e}")

        return sorted(filenames)
    
    @override
    async def delete_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
    ) -> None:
        """Delete all versions of an artifact."""
        versions = await self.list_versions(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        storage = self._get_storage()
        for ver in versions:
            key = self._get_object_key(app_name, user_id, session_id, filename, ver)
            try:
                storage.remove([key])
                logger.info(f"Deleted artifact from Supabase: {key}")
            except Exception as e:
                logger.error(f"Failed to delete artifact {key}: {e}")
    
    @override
    async def list_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
    ) -> list[int]:
        """List all versions of an artifact."""
        # Prepend 'public' to match RLS policy requirement
        if self._file_has_user_namespace(filename):
            prefix = f"public/{app_name}/{user_id}/user/{filename}/"
        else:
            prefix = f"public/{app_name}/{user_id}/{session_id}/{filename}/"
        
        versions = []
        storage = self._get_storage()
        
        try:
            files = storage.list(prefix)
            for file in files:
                key = file.get("name", "").rstrip("/")
                parts = key.split("/")
                ver_str = parts[-1]
                try:
                    versions.append(int(ver_str))
                except ValueError:
                    continue
        except Exception as e:
            logger.warning(f"Failed to list versions for {filename}: {e}")
            return []

        return versions

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
        storage = self._get_storage()
        
        for version in versions:
            key = self._get_object_key(app_name, user_id, session_id or "", filename, version)
            try:
                # Get file metadata from Supabase
                files = storage.list(key)
                if files:
                    file_info = files[0]  # Get the first (and should be only) file
                    
                    # Construct canonical URI that matches the actual storage key structure
                    if self._file_has_user_namespace(filename):
                        canonical_uri = f"supabase://{self.bucket_name}/public/{app_name}/{user_id}/user/{filename}/versions/{version}"
                    else:
                        if session_id:
                            canonical_uri = f"supabase://{self.bucket_name}/public/{app_name}/{user_id}/{session_id}/{filename}/versions/{version}"
                        else:
                            canonical_uri = f"supabase://{self.bucket_name}/public/{app_name}/{user_id}/{filename}/versions/{version}"
                    
                    # Extract metadata from file info
                    mime_type = file_info.get("metadata", {}).get("mimetype", "application/octet-stream")
                    created_at = file_info.get("created_at", "")
                    
                    # Convert created_at to timestamp if available
                    create_time = 0.0
                    if created_at:
                        try:
                            from datetime import datetime
                            # Parse ISO format timestamp
                            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            create_time = dt.timestamp()
                        except:
                            create_time = 0.0
                    
                    artifact_version = ArtifactVersion(
                        version=version,
                        canonical_uri=canonical_uri,
                        mime_type=mime_type,
                        create_time=create_time,
                    )
                    artifact_versions.append(artifact_version)
            except Exception as e:
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
        
        key = self._get_object_key(app_name, user_id, session_id or "", filename, version)
        storage = self._get_storage()
        
        try:
            # Get file metadata from Supabase
            files = storage.list(key)
            if not files:
                return None
                
            file_info = files[0]  # Get the first (and should be only) file
            
            # Construct canonical URI that matches the actual storage key structure
            if self._file_has_user_namespace(filename):
                canonical_uri = f"supabase://{self.bucket_name}/public/{app_name}/{user_id}/user/{filename}/versions/{version}"
            else:
                if session_id:
                    canonical_uri = f"supabase://{self.bucket_name}/public/{app_name}/{user_id}/{session_id}/{filename}/versions/{version}"
                else:
                    canonical_uri = f"supabase://{self.bucket_name}/public/{app_name}/{user_id}/{filename}/versions/{version}"
            
            # Extract metadata from file info
            mime_type = file_info.get("metadata", {}).get("mimetype", "application/octet-stream")
            created_at = file_info.get("created_at", "")
            
            # Convert created_at to timestamp if available
            create_time = 0.0
            if created_at:
                try:
                    from datetime import datetime
                    # Parse ISO format timestamp
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    create_time = dt.timestamp()
                except:
                    create_time = 0.0
            
            return ArtifactVersion(
                version=version,
                canonical_uri=canonical_uri,
                mime_type=mime_type,
                create_time=create_time,
            )
        except Exception as e:
            logger.warning(f"Failed to get metadata for version {version} of {filename}: {e}")
            return None

