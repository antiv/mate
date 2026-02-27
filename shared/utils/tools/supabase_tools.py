"""
Supabase Storage tools: upload, get, and delete files as binary data.

Environment variables required:
- SUPABASE_URL
- SUPABASE_KEY

Optional tool_config to enable via ToolFactory:
  {"supabase_storage": true}

Each tool accepts a ToolContext for consistency with other tools, but it is optional.
"""

import base64
import logging
import os
from typing import Any, Dict, List, Optional

from google.adk.tools.tool_context import ToolContext


logger = logging.getLogger(__name__)


def _get_supabase_client():
    """
    Lazy import and initialize the Supabase client using environment variables.
    Returns the client instance or raises a descriptive exception.
    """
    try:
        from supabase import create_client  # type: ignore
    except Exception as import_error:  # pragma: no cover - dependency optional at runtime
        raise RuntimeError(
            "supabase-py is not installed. Please `pip install supabase`"
        ) from import_error

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in environment")

    return create_client(url, key)


def supabase_upload_file(
        path: str,
        file_bytes_b64: str,
        content_type: Optional[str] = None,
        upsert: bool = True,
) -> Dict[str, Any]:
    """
    Upload a file to Supabase Storage.

    Args:
        bucket: Supabase storage bucket name
        path: File path/key within the bucket
        file_bytes_b64: Base64-encoded bytes of the file to upload
        content_type: Optional MIME type
        upsert: Whether to overwrite if the file exists

    Returns:
        Dict with status and metadata.
    """
    try:
        client = _get_supabase_client()
        bucket = os.getenv("SUPABASE_BUCKET", "public-bucket")
        storage = client.storage.from_(bucket)

        file_bytes = base64.b64decode(file_bytes_b64)

        # Build file_options dictionary with proper string values
        file_options: Dict[str, str] = {}
        if content_type:
            file_options["content-type"] = content_type

        # For upsert, we need to handle it differently based on Supabase client version
        # Try uploading with file_options only (no upsert in options)
        try:
            if file_options:
                storage.upload(path, file_bytes, file_options=file_options)
            else:
                storage.upload(path, file_bytes)
        except Exception as upload_error:
            # If upload fails because file exists and upsert is True, try update
            if upsert and "already exists" in str(upload_error).lower():
                storage.update(path, file_bytes, file_options=file_options if file_options else None)
            else:
                raise

        public_url = None
        try:
            # Attempt to get a public URL if bucket is public
            public_url = storage.get_public_url(path)
        except Exception:
            public_url = None

        return {
            "status": "success",
            "bucket": bucket,
            "path": path,
            "content_type": content_type,
            "public_url": public_url,
        }
    except Exception as e:
        logger.error(f"Supabase upload failed: {e}")
        return {"status": "error", "error_message": str(e)}


def supabase_get_file(
    path: str,
    as_base64: bool = True,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """
    Get a file from Supabase Storage.

    Args:
        bucket: Supabase storage bucket name
        path: File path/key within the bucket
        as_base64: If True, returns file content base64-encoded
        tool_context: ADK ToolContext (unused, for parity)

    Returns:
        Dict with status and file data. If as_base64 is True, returns 'file_bytes_b64'.
    """
    try:
        client = _get_supabase_client()
        bucket = os.getenv("SUPABASE_BUCKET", "public-bucket")
        storage = client.storage.from_(bucket)
        file_bytes: bytes = storage.download(path)

        if as_base64:
            return {
                "status": "success",
                "bucket": bucket,
                "path": path,
                "file_bytes_b64": base64.b64encode(file_bytes).decode("utf-8"),
            }
        else:
            # Return raw bytes as base64 anyway to keep JSON contract, but mark it
            return {
                "status": "success",
                "bucket": bucket,
                "path": path,
                "file_bytes_b64": base64.b64encode(file_bytes).decode("utf-8"),
                "note": "Returned as base64 for JSON transport",
            }
    except Exception as e:
        logger.error(f"Supabase get failed: {e}")
        return {"status": "error", "error_message": str(e)}


def supabase_delete_file(
    path: str,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """
    Delete a file from Supabase Storage.

    Args:
        bucket: Supabase storage bucket name
        path: File path/key within the bucket
        tool_context: ADK ToolContext (unused, for parity)

    Returns:
        Dict with status and deletion result.
    """
    try:
        client = _get_supabase_client()
        bucket = os.getenv("SUPABASE_BUCKET", "public-bucket")
        storage = client.storage.from_(bucket)
        storage.remove([path])
        return {"status": "success", "bucket": bucket, "path": path}
    except Exception as e:
        logger.error(f"Supabase delete failed: {e}")
        return {"status": "error", "error_message": str(e)}


def create_supabase_storage_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create Supabase storage tools if enabled in configuration.

    Config expects tool_config to contain: {"supabase_storage": true}
    Returns the callable tool functions list.
    """
    tools: List[Any] = []
    try:
        tool_config = config.get("tool_config")
        if not tool_config:
            return tools

        import json
        config_dict = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
        if not config_dict.get("supabase_storage"):
            return tools

        # Validate environment presence early for clearer logs, but do not hard fail
        if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
            logger.warning("SUPABASE_URL/SUPABASE_KEY not set; Supabase tools may fail at runtime.")

        tools.extend([
            supabase_upload_file,
            supabase_get_file,
            supabase_delete_file,
        ])

        logger.info("Created Supabase storage tools")
    except Exception as e:
        logger.error(f"Failed to create Supabase storage tools: {e}")

    return tools


