"""S3 artifact service implementation for Google ADK."""

import logging
from typing import Optional, Dict, Any

import boto3
from google.adk.artifacts import BaseArtifactService
from google.adk.artifacts.base_artifact_service import ArtifactVersion
from google.genai import types
from typing_extensions import override

logger = logging.getLogger("adk_extra_services.artifacts.s3")


class S3ArtifactService(BaseArtifactService):
    """An artifact service implementation using AWS S3 or S3-compatible storage."""

    def __init__(
        self,
        bucket_name: str,
        endpoint_url: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: Optional[str] = None,
        **kwargs,
    ):
        """Initializes the S3ArtifactService.

        Args:
            bucket_name: The name of the S3 bucket.
            endpoint_url: Optional endpoint URL for S3-compatible storage (e.g., MinIO).
            aws_access_key_id: Optional AWS access key. Uses environment/config if not provided.
            aws_secret_access_key: Optional AWS secret key. Uses environment/config if not provided.
            region_name: Optional AWS region. Uses environment/config if not provided.
            **kwargs: Additional keyword arguments to pass to boto3.client('s3').
        """
        self.bucket_name = bucket_name

        # Prepare S3 client configuration
        client_kwargs = kwargs.copy()
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if aws_access_key_id:
            client_kwargs["aws_access_key_id"] = aws_access_key_id
        if aws_secret_access_key:
            client_kwargs["aws_secret_access_key"] = aws_secret_access_key
        if region_name:
            client_kwargs["region_name"] = region_name

        self.s3_client = boto3.client("s3", **client_kwargs)

    def _file_has_user_namespace(self, filename: str) -> bool:
        return filename.startswith("user:")

    def _get_object_key(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        filename: str,
        version: int,
    ) -> str:
        if self._file_has_user_namespace(filename):
            return f"{app_name}/{user_id}/user/{filename}/{version}"
        # Handle case where session_id might be empty string
        if session_id:
            return f"{app_name}/{user_id}/{session_id}/{filename}/{version}"
        else:
            return f"{app_name}/{user_id}/{filename}/{version}"

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

        key = self._get_object_key(app_name, user_id, session_id, filename, version)
        self.s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=artifact.inline_data.data,
            ContentType=artifact.inline_data.mime_type,
        )
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

        key = self._get_object_key(app_name, user_id, session_id, filename, version)
        try:
            resp = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
            data = resp["Body"].read()
            mime = resp.get("ContentType")
        except self.s3_client.exceptions.NoSuchKey:
            return None

        return types.Part.from_bytes(data=data, mime_type=mime)

    @override
    async def list_artifact_keys(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> list[str]:
        filenames = set()

        session_prefix = f"{app_name}/{user_id}/{session_id}/"
        paginator = self.s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=session_prefix):
            for obj in page.get("Contents", []):
                parts = obj["Key"].split("/")
                if len(parts) >= 5:
                    filenames.add(parts[3])

        user_prefix = f"{app_name}/{user_id}/user/"
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=user_prefix):
            for obj in page.get("Contents", []):
                parts = obj["Key"].split("/")
                if len(parts) >= 5:
                    filenames.add(parts[3])

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
        versions = await self.list_versions(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        for ver in versions:
            key = self._get_object_key(app_name, user_id, session_id, filename, ver)
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)

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
            prefix = f"{app_name}/{user_id}/user/{filename}/"
        else:
            prefix = f"{app_name}/{user_id}/{session_id}/{filename}/"
        versions = []
        paginator = self.s3_client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                for obj in page.get("Contents", []):
                    parts = obj["Key"].rstrip("/").split("/")
                    ver_str = parts[-1]
                    try:
                        versions.append(int(ver_str))
                    except ValueError:
                        continue
        except self.s3_client.exceptions.NoSuchKey:
            # Happens when prefix does not yet exist in the bucket. Treat as no versions.
            return []
        except self.s3_client.exceptions.NoSuchBucket:
            logger.error("Bucket %s does not exist", self.bucket_name)
            raise

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
        for version in versions:
            # Get object metadata from S3
            key = self._get_object_key(app_name, user_id, session_id or "", filename, version)
            try:
                response = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
                
                # Construct canonical URI that matches the actual S3 key structure
                if self._file_has_user_namespace(filename):
                    canonical_uri = f"s3://{self.bucket_name}/{app_name}/{user_id}/user/{filename}/versions/{version}"
                else:
                    if session_id:
                        canonical_uri = f"s3://{self.bucket_name}/{app_name}/{user_id}/{session_id}/{filename}/versions/{version}"
                    else:
                        canonical_uri = f"s3://{self.bucket_name}/{app_name}/{user_id}/{filename}/versions/{version}"
                
                artifact_version = ArtifactVersion(
                    version=version,
                    canonical_uri=canonical_uri,
                    mime_type=response.get("ContentType"),
                    create_time=response["LastModified"].timestamp(),
                )
                artifact_versions.append(artifact_version)
            except self.s3_client.exceptions.NoSuchKey:
                # Skip if object doesn't exist
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
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            
            # Construct canonical URI that matches the actual S3 key structure
            if self._file_has_user_namespace(filename):
                canonical_uri = f"s3://{self.bucket_name}/{app_name}/{user_id}/user/{filename}/versions/{version}"
            else:
                if session_id:
                    canonical_uri = f"s3://{self.bucket_name}/{app_name}/{user_id}/{session_id}/{filename}/versions/{version}"
                else:
                    canonical_uri = f"s3://{self.bucket_name}/{app_name}/{user_id}/{filename}/versions/{version}"
            
            return ArtifactVersion(
                version=version,
                canonical_uri=canonical_uri,
                mime_type=response.get("ContentType"),
                create_time=response["LastModified"].timestamp(),
            )
        except self.s3_client.exceptions.NoSuchKey:
            return None