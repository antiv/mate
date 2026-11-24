"""
The Google Drive MCP server, at /gdrive/mcp: reads Drive with the installation's service account.
"""

import asyncio
import os
from typing import Any, Dict

from fastapi import FastAPI
from mcp import types
from mcp.server.lowlevel import Server

from .mcp_auth import require_drive_user
from .served import checked_arguments, serve, text_result

TOOLS = [
    types.Tool(
        name="list_files_in_folder",
        description="List all files in a Google Drive folder. If folder_id is empty, uses GOOGLE_DRIVE_FOLDER_ID environment variable.",
        input_schema={"type": "object", "properties": {
            "folder_id": {"type": "string", "description": "Google Drive folder ID (optional if GOOGLE_DRIVE_FOLDER_ID env var is set)"},
        }, "required": []}),
    types.Tool(
        name="read_google_doc",
        description="Read content from a Google Doc/PDF/text file by file ID",
        input_schema={"type": "object", "properties": {
            "doc_id": {"type": "string", "description": "Google Drive file ID"},
        }, "required": ["doc_id"]}),
    types.Tool(
        name="read_google_doc_by_name",
        description="Read content from a Google Doc by searching for its name in a folder",
        input_schema={"type": "object", "properties": {
            "doc_name": {"type": "string", "description": "Name of the document to search for"},
            "folder_id": {"type": "string", "description": "Google Drive folder ID (optional if GOOGLE_DRIVE_FOLDER_ID env var is set)"},
        }, "required": ["doc_name"]}),
    types.Tool(
        name="search_files",
        description="Search for files in a Google Drive folder using a query",
        input_schema={"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query to match against file names and content"},
            "folder_id": {"type": "string", "description": "Google Drive folder ID (optional if GOOGLE_DRIVE_FOLDER_ID env var is set)"},
        }, "required": ["query"]}),
    types.Tool(
        name="get_file_metadata",
        description="Get metadata for a specific Google Drive file",
        input_schema={"type": "object", "properties": {
            "file_id": {"type": "string", "description": "Google Drive file ID"},
        }, "required": ["file_id"]}),
    types.Tool(
        name="get_file_sharing_permissions",
        description="Get detailed sharing permissions for a file, including email addresses of users it's shared with",
        input_schema={"type": "object", "properties": {
            "file_id": {"type": "string", "description": "Google Drive file ID"},
        }, "required": ["file_id"]}),
    types.Tool(
        name="find_by_name",
        description="Find files by name. Searches for files containing the name in the filename. Returns single match with content, or list for multiple matches.",
        input_schema={"type": "object", "properties": {
            "name": {"type": "string", "description": "Name or name parts to search for in filenames"},
            "folder_id": {"type": "string", "description": "Google Drive folder ID (optional if GOOGLE_DRIVE_FOLDER_ID env var is set)"},
        }, "required": ["name"]}),
]


def format_result(result: Dict[str, Any]) -> str:
    """A tool's result as text"""
    if result.get("status") == "error":
        return f"❌ Error: {result.get('error_message', 'Unknown error')}"

    output = []
    output.append(f"✅ {result.get('message', 'Operation completed')}\n")

    # Format based on what data is available
    if "files" in result:
        files = result["files"]
        if files:
            output.append(f"Files ({len(files)}):")
            for file in files:
                output.append(f"  • {file['name']} (ID: {file['id']})")
                output.append(f"    Type: {file.get('mimeType', 'unknown')}, Size: {file.get('size', 'unknown')}, Modified: {file.get('modifiedTime', 'unknown')}")
        else:
            output.append("No files found.")

    if "content" in result:
        output.append(f"\n📄 File Content:")
        output.append(f"File: {result.get('doc_name', result.get('file_name', 'unknown'))}")
        content = result["content"]
        # Limit content display to first 2000 chars
        if len(content) > 2000:
            output.append(f"{content[:2000]}...\n[Content truncated, total length: {len(content)} characters]")
        else:
            output.append(content)

    if "metadata" in result:
        metadata = result["metadata"]
        output.append(f"\n📋 File Metadata:")
        output.append(f"Name: {metadata.get('name')}")
        output.append(f"ID: {metadata.get('id')}")
        output.append(f"Type: {metadata.get('mimeType')}")
        output.append(f"Size: {metadata.get('size')}")
        output.append(f"Created: {metadata.get('createdTime')}")
        output.append(f"Modified: {metadata.get('modifiedTime')}")
        output.append(f"Owners: {', '.join(metadata.get('owners', []))}")

    if "shared_with" in result:
        shared = result["shared_with"]
        output.append(f"\n🔗 Sharing Permissions:")
        output.append(f"File: {result.get('file_name')}")
        output.append(f"Owners: {', '.join(result.get('owners', []))}")

        if shared.get("users"):
            output.append(f"\nUsers ({len(shared['users'])}):")
            for user in shared["users"]:
                output.append(f"  • {user.get('email', user.get('displayName', 'Unknown'))} - {user['role']}")

        if shared.get("groups"):
            output.append(f"\nGroups ({len(shared['groups'])}):")
            for group in shared["groups"]:
                output.append(f"  • {group.get('email', 'Unknown')} - {group['role']}")

        if shared.get("domains"):
            output.append(f"\nDomains ({len(shared['domains'])}):")
            for domain in shared["domains"]:
                output.append(f"  • {domain.get('domain', 'Unknown')} - {domain['role']}")

        if shared.get("anyone"):
            output.append(f"\nPublic Access:")
            for anyone in shared["anyone"]:
                output.append(f"  • Anyone with link - {anyone['role']}")

    if "file" in result and result.get("status") == "single_match_found":
        file_info = result["file"]
        output.append(f"\n📄 Matched File:")
        output.append(f"Name: {file_info.get('name')}")
        output.append(f"ID: {file_info.get('id')}")
        output.append(f"Type: {file_info.get('mimeType')}")
        if "content_preview" in result:
            output.append(f"\nContent Preview:")
            output.append(result["content_preview"])

    if "matching_files" in result:
        files = result["matching_files"]
        output.append(f"\n📁 Multiple Matches ({len(files)}):")
        for file in files:
            output.append(f"  • {file['name']} (ID: {file['id']})")
            output.append(f"    Type: {file.get('mimeType', 'unknown')}")

    if "available_files" in result:
        files = result["available_files"]
        output.append(f"\n📁 Available Files ({len(files)}):")
        for file in files[:20]:  # Limit to first 20
            output.append(f"  • {file['name']} (ID: {file['id']})")
        if len(files) > 20:
            output.append(f"  ... and {len(files) - 20} more files")

    return "\n".join(output)


class GoogleDriveMCPServer:
    """The Google Drive MCP server."""

    def __init__(self, app: FastAPI, gdrive_mcp_available: bool = True):
        self.gdrive_mcp_available = gdrive_mcp_available
        server = Server("google-drive-mcp-server", version="1.0.0",
                        on_list_tools=self._list_tools, on_call_tool=self._call_tool)
        serve(app, "/gdrive/mcp", "MCP - Google Drive", server, require_drive_user, self._info, self._health,
              lambda: self.gdrive_mcp_available)

    def _info(self) -> Dict[str, Any]:
        return {
            "name": "Google Drive MCP Server",
            "version": "1.0.0",
            "description": "Google Drive file management tools via Model Context Protocol",
            "tools": len(TOOLS),
            "endpoints": {
                "health": "/gdrive/mcp/health",
                "tools_list": "/gdrive/mcp/tools/list",
                "tools_call": "/gdrive/mcp/tools/call",
                "initialize": "/gdrive/mcp/initialize",
            },
            "available_tools": [tool.name for tool in TOOLS],
        }

    def _health(self) -> Dict[str, Any]:
        health: Dict[str, Any] = {
            "status": "healthy" if self.gdrive_mcp_available else "unhealthy",
            "service": "google-drive-mcp-server",
            "tools": len(TOOLS) if self.gdrive_mcp_available else 0,
            "endpoint": "/gdrive/mcp",
            "protocol": "MCP",
        }
        if not self.gdrive_mcp_available:
            health["error"] = "Google Drive credentials not configured (GOOGLE_SERVICE_ACCOUNT_INFO)"
            health["details"] = {"has_credentials": bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_INFO"))}
        return health

    async def _list_tools(self, context: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=TOOLS)

    async def _call_tool(self, context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = checked_arguments(TOOLS, params)
        from ..tools import google_drive_tools

        function = getattr(google_drive_tools, params.name)
        # The tool's own parameters, in order; a folder left out is the default one. They
        # call Drive and read files, so off the event loop.
        schema = next(tool.input_schema for tool in TOOLS if tool.name == params.name)
        given = [arguments.get(name, "") for name in schema["properties"]]
        result = await asyncio.to_thread(function, *given, None)
        return text_result(format_result(result), is_error=result.get("status") == "error", structured=result)

    def check_gdrive_mcp_availability(self) -> bool:
        """Whether the credentials are configured; the server answers 503 without them."""
        self.gdrive_mcp_available = bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_INFO"))
        if self.gdrive_mcp_available:
            print("✅ Google Drive MCP tools initialized successfully")
            print("   Credentials: GOOGLE_SERVICE_ACCOUNT_INFO environment variable")
        else:
            print("⚠️  Google Drive MCP tools not available: No credentials configured")
            print("   Set GOOGLE_SERVICE_ACCOUNT_INFO environment variable")
        return self.gdrive_mcp_available
