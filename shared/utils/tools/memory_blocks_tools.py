"""
Local memory blocks tools for agents.
List/get/create/modify/delete blocks backed by DB, scoped by the agent's project_id.
"""

import logging
from typing import Dict, Any, List, Optional
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)


def _get_user_id_from_context(tool_context: ToolContext) -> Optional[str]:
    if not tool_context:
        return None
    user_id = None
    if hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
        user_id = getattr(tool_context._invocation_context, 'user_id', None)
    if not user_id:
        user_id = getattr(tool_context, 'user_id', None)
    if not user_id and hasattr(tool_context, 'session') and tool_context.session:
        user_id = getattr(tool_context.session, 'user_id', None)
    return user_id


def _is_admin_user(tool_context: ToolContext) -> bool:
    from shared.utils.user_service import get_user_service
    user_id = _get_user_id_from_context(tool_context)
    if not user_id:
        return False
    user_service = get_user_service()
    roles = user_service.get_user_roles(user_id)
    return 'admin' in roles


_ADMIN_DENIED = {
    "status": "error",
    "error_message": "Permission denied. Only admin users can modify memory blocks. Reading is allowed for all users."
}


def _substitute_user_id(text: Optional[str], tool_context: ToolContext) -> Optional[str]:
    if not text or not isinstance(text, str):
        return text
    if "{user_id}" in text or "current_user" in text:
        user_id = _get_user_id_from_context(tool_context)
        if user_id:
            text = text.replace("{user_id}", user_id).replace("current_user", user_id)
    return text


def _get_service():
    from shared.utils.database_client import get_database_client
    from shared.utils.memory_blocks_service import MemoryBlocksService
    return MemoryBlocksService(get_database_client())


def create_memory_blocks_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create memory block tools bound to the agent's project_id.
    config must have project_id (from AgentConfig).
    """
    project_id = config.get('project_id')
    if project_id is None:
        logger.warning("memory_blocks tools: project_id missing in config, using 1")
        project_id = 1

    def list_shared_blocks(
        limit: int = 100,
        label: Optional[str] = None,
        label_search: Optional[str] = None,
        value_search: Optional[str] = None,
        tool_context: ToolContext = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """List memory blocks with optional filters (by label, label prefix, or value text)."""
        label = _substitute_user_id(label, tool_context)
        label_search = _substitute_user_id(label_search, tool_context)
        svc = _get_service()
        return svc.list_blocks(
            project_id=project_id,
            limit=limit,
            label=label,
            label_search=label_search,
            value_search=value_search,
        )

    def get_shared_block(
        block_id: Optional[str] = None,
        tool_context: ToolContext = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Retrieve a memory block by block_id (numeric id or label)."""
        if block_id is None and 'id' in kwargs:
            block_id = kwargs.get('id')
        if not block_id:
            return {"status": "error", "error_message": "block_id is required"}
        svc = _get_service()
        return svc.get_block(project_id=project_id, block_id=str(block_id))

    def create_shared_block(
        label: Optional[str] = None,
        value: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tool_context: ToolContext = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new memory block. Provide label, initial value, and optionally description and metadata."""
        if not _is_admin_user(tool_context):
            return _ADMIN_DENIED
        label = _substitute_user_id(label or "", tool_context)
        if not label or not label.strip():
            return {"status": "error", "error_message": "label is required"}
        svc = _get_service()
        return svc.create_block(
            project_id=project_id,
            label=label.strip(),
            value=value or "",
            description=description,
            metadata=metadata,
        )

    def modify_shared_block(
        block_id: Optional[str] = None,
        value: Optional[str] = None,
        description: Optional[str] = None,
        tool_context: ToolContext = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Modify a memory block. Provide block_id (id or label), new value, and optionally description."""
        if not _is_admin_user(tool_context):
            return _ADMIN_DENIED
        if block_id is None and 'id' in kwargs:
            block_id = kwargs.get('id')
        if not block_id:
            return {"status": "error", "error_message": "block_id is required"}
        svc = _get_service()
        return svc.modify_block(
            project_id=project_id,
            block_id=str(block_id),
            value=value,
            description=description,
        )

    def delete_shared_block(
        block_id: Optional[str] = None,
        tool_context: ToolContext = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Delete a memory block by block_id (id or label)."""
        if not _is_admin_user(tool_context):
            return _ADMIN_DENIED
        if block_id is None and 'id' in kwargs:
            block_id = kwargs.get('id')
        if not block_id:
            return {"status": "error", "error_message": "block_id is required"}
        svc = _get_service()
        return svc.delete_block(project_id=project_id, block_id=str(block_id))

    return [
        list_shared_blocks,
        get_shared_block,
        create_shared_block,
        modify_shared_block,
        delete_shared_block,
    ]
