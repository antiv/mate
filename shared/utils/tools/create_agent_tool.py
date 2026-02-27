"""
Tool for agents to create other agents dynamically.

This tool allows an agent to create new agents with:
- Same model as current agent (if not specified)
- Same project as current agent
- Same memory tools configuration (if current agent uses them)
- Can be root agent or subagent
"""

import json
import logging
from typing import Dict, Any, Optional
from google.adk.tools.tool_context import ToolContext
from ..database_client import get_database_client
from ..models import AgentConfig
from ..user_service import get_user_service
from ...utils.utils import reload_agent_cache

logger = logging.getLogger(__name__)


def _get_user_id_from_context(tool_context: ToolContext) -> Optional[str]:
    """Extract user_id from tool context using the standard ADK pattern."""
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
    """Check if the current user has the admin role."""
    user_id = _get_user_id_from_context(tool_context)
    if not user_id:
        return False
    user_service = get_user_service()
    roles = user_service.get_user_roles(user_id)
    return 'admin' in roles


def _get_current_agent_config(agent_name: str) -> Optional[Dict[str, Any]]:
    """
    Get current agent's configuration from database.
    
    Args:
        agent_name: Name of the current agent
        
    Returns:
        Agent configuration dict or None if not found
    """
    db_client = get_database_client()
    session = db_client.get_session()
    if not session:
        logger.error("Failed to get database session")
        return None
    
    try:
        agent_config = session.query(AgentConfig).filter(
            AgentConfig.name == agent_name
        ).first()
        
        if not agent_config:
            logger.error(f"Agent {agent_name} not found in database")
            return None
        
        return agent_config.to_dict()
    except Exception as e:
        logger.error(f"Error getting agent config: {e}")
        return None
    finally:
        session.close()


def _extract_memory_tools_config(tool_config_str: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Extract memory tools configuration from tool_config.
    
    Args:
        tool_config_str: JSON string of tool_config
        
    Returns:
        Dict with memory tools config or None
    """
    if not tool_config_str:
        return None
    
    try:
        tool_config = json.loads(tool_config_str) if isinstance(tool_config_str, str) else tool_config_str
        
        # Check for memory tools
        memory_config = {}
        if tool_config.get('memory_blocks'):
            memory_config['memory_blocks'] = tool_config['memory_blocks']
        
        return memory_config if memory_config else None
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Failed to parse tool_config: {e}")
        return None


def create_agent(
    name: str,
    description: str,
    instruction: str,
    model: Optional[str] = None,
    parent_agent: Optional[str] = None,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Create a new LLM agent. The new agent will:
    - Use the same model as the current agent (if model not specified)
    - Be in the same project as the current agent
    - Inherit the same memory tools configuration (memory_blocks) if the current agent uses them
    - Be a root agent (if parent_agent not specified) or a subagent of the specified parent
    
    Required fields: name, description, instruction
    Type is always set to 'llm'
    
    Args:
        name: Name of the new agent (required, must be unique)
        description: Description of the new agent (required)
        instruction: Instruction/prompt for the new agent (required)
        model: Model name for the new agent (optional, uses current agent's model if not specified)
        parent_agent: Name of parent agent (optional, creates root agent if not specified)
        tool_context: Tool context to get current agent information (automatically provided)
        
    Returns:
        Dict with status, message, and agent details:
        - status: "success" or "error"
        - message: Human-readable message
        - agent_name: Name of created agent (on success)
        - model: Model used for the agent
        - project_id: Project ID
        - is_root_agent: Whether agent is a root agent
        - parent_agent: Parent agent name if subagent
        - has_memory_tools: Whether memory tools were inherited
    """
    try:
        # Admin-only check
        if not _is_admin_user(tool_context):
            user_id = _get_user_id_from_context(tool_context)
            logger.warning(f"Non-admin user '{user_id}' attempted to create agent '{name}'")
            return {
                "status": "error",
                "error_message": "Permission denied. Only admin users can create agents. Please contact an administrator if you need changes made."
            }

        # Get current agent name from tool context
        if not tool_context:
            return {
                "status": "error",
                "error_message": "Tool context is required"
            }
        
        current_agent_name = getattr(tool_context, 'agent_name', None)
        if not current_agent_name:
            return {
                "status": "error",
                "error_message": "Could not determine current agent name"
            }
        
        # Get current agent's configuration
        current_config = _get_current_agent_config(current_agent_name)
        if not current_config:
            return {
                "status": "error",
                "error_message": f"Could not find configuration for current agent: {current_agent_name}"
            }
        
        # Validate required fields
        if not name or not name.strip():
            return {
                "status": "error",
                "error_message": "Agent name is required"
            }
        
        if not description or not description.strip():
            return {
                "status": "error",
                "error_message": "Agent description is required"
            }
        
        if not instruction or not instruction.strip():
            return {
                "status": "error",
                "error_message": "Agent instruction is required"
            }
        
        # Check if agent with this name already exists
        db_client = get_database_client()
        session = db_client.get_session()
        if not session:
            return {
                "status": "error",
                "error_message": "Failed to get database session"
            }
        
        try:
            existing_agent = session.query(AgentConfig).filter(
                AgentConfig.name == name.strip()
            ).first()
            
            if existing_agent:
                return {
                    "status": "error",
                    "error_message": f"Agent with name '{name}' already exists"
                }
            
            # Determine model (use current agent's model if not specified)
            agent_model = model.strip() if model and model.strip() else current_config.get('model_name')
            if not agent_model:
                return {
                    "status": "error",
                    "error_message": "Model is required (not specified and current agent has no model)"
                }
            
            # Get project_id from current agent
            project_id = current_config.get('project_id', 1)
            
            # Extract memory tools config from current agent
            current_tool_config_str = current_config.get('tool_config')
            memory_tools_config = _extract_memory_tools_config(current_tool_config_str)
            
            # Build tool_config for new agent
            tool_config = {}
            if memory_tools_config:
                tool_config.update(memory_tools_config)
            
            # Determine parent agents
            parent_agents = []
            if parent_agent and parent_agent.strip():
                parent_agent_name = parent_agent.strip()
                # Verify parent agent exists
                parent_exists = session.query(AgentConfig).filter(
                    AgentConfig.name == parent_agent_name
                ).first()
                
                if not parent_exists:
                    return {
                        "status": "error",
                        "error_message": f"Parent agent '{parent_agent_name}' does not exist"
                    }
                
                parent_agents = [parent_agent_name]
            
            # Create agent configuration
            config_data = {
                "name": name.strip(),
                "type": "llm",  # Always LLM as per requirements
                "project_id": project_id,
                "model_name": agent_model,
                "description": description.strip(),
                "instruction": instruction.strip(),
                "parent_agents": parent_agents,
                "tool_config": json.dumps(tool_config) if tool_config else None,
                "disabled": False,
                "hardcoded": False
            }
            
            # Create agent using same logic as dashboard
            processed_data = config_data.copy()
            if 'parent_agents' in processed_data and isinstance(processed_data['parent_agents'], list):
                processed_data['parent_agents'] = json.dumps(processed_data['parent_agents']) if processed_data['parent_agents'] else None
            
            new_agent = AgentConfig(**processed_data)
            session.add(new_agent)
            session.commit()
            
            logger.info(f"Created new agent '{name}' by agent '{current_agent_name}'")
            
            # Reload parent agent(s) if this is a subagent
            reload_success = True
            if parent_agents:
                for parent_agent_name in parent_agents:
                    reload_result = reload_agent_cache(parent_agent_name)
                    reload_success = reload_result.get("success", False)
                    if not reload_success:
                        logger.warning(f"Failed to reload parent agent '{parent_agent_name}' after creating subagent '{name}': {reload_result.get('message', 'Unknown error')}")
            
            return {
                "status": "success",
                "message": f"Agent '{name}' created successfully" + (f". Parent agent(s) reloaded." if parent_agents and reload_success else ""),
                "agent_name": name,
                "model": agent_model,
                "project_id": project_id,
                "is_root_agent": len(parent_agents) == 0,
                "parent_agent": parent_agents[0] if parent_agents else None,
                "has_memory_tools": bool(memory_tools_config),
                "parent_reloaded": reload_success if parent_agents else None
            }
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error creating agent: {e}")
            return {
                "status": "error",
                "error_message": f"Failed to create agent: {str(e)}"
            }
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Unexpected error in create_agent: {e}")
        return {
            "status": "error",
            "error_message": f"Unexpected error: {str(e)}"
        }


def delete_agent(
    agent_name: str,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Delete an agent. If the agent is a subagent, parent agents will be reloaded.
    
    Args:
        agent_name: Name of the agent to delete (required)
        tool_context: Tool context to get current agent information (automatically provided)
        
    Returns:
        Dict with status, message, and details:
        - status: "success" or "error"
        - message: Human-readable message
        - agent_name: Name of deleted agent (on success)
        - parents_reloaded: List of parent agent names that were reloaded
    """
    try:
        # Admin-only check
        if not _is_admin_user(tool_context):
            user_id = _get_user_id_from_context(tool_context)
            logger.warning(f"Non-admin user '{user_id}' attempted to delete agent '{agent_name}'")
            return {
                "status": "error",
                "error_message": "Permission denied. Only admin users can delete agents. Please contact an administrator if you need changes made."
            }

        # Get current agent name from tool context (for logging)
        current_agent_name = None
        if tool_context:
            current_agent_name = getattr(tool_context, 'agent_name', None)
        
        # Validate required fields
        if not agent_name or not agent_name.strip():
            return {
                "status": "error",
                "error_message": "Agent name is required"
            }
        
        agent_name = agent_name.strip()
        
        # Get database session
        db_client = get_database_client()
        session = db_client.get_session()
        if not session:
            return {
                "status": "error",
                "error_message": "Failed to get database session"
            }
        
        try:
            # Get agent configuration to find parent agents before deletion
            agent_config = session.query(AgentConfig).filter(
                AgentConfig.name == agent_name
            ).first()
            
            if not agent_config:
                return {
                    "status": "error",
                    "error_message": f"Agent '{agent_name}' not found"
                }
            
            # Get parent agents before deletion
            parent_agents = agent_config.get_parent_agents() if hasattr(agent_config, 'get_parent_agents') else []
            
            # Delete the agent
            session.delete(agent_config)
            session.commit()
            
            logger.info(f"Deleted agent '{agent_name}' by agent '{current_agent_name or 'unknown'}'")
            
            # Reload parent agent(s) if this was a subagent
            parents_reloaded = []
            if parent_agents:
                for parent_agent_name in parent_agents:
                    reload_result = reload_agent_cache(parent_agent_name)
                    if reload_result.get("success", False):
                        parents_reloaded.append(parent_agent_name)
                    else:
                        logger.warning(f"Failed to reload parent agent '{parent_agent_name}' after deleting subagent '{agent_name}': {reload_result.get('message', 'Unknown error')}")
            
            return {
                "status": "success",
                "message": f"Agent '{agent_name}' deleted successfully" + (f". Parent agent(s) reloaded." if parents_reloaded else ""),
                "agent_name": agent_name,
                "parents_reloaded": parents_reloaded
            }
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error deleting agent: {e}")
            return {
                "status": "error",
                "error_message": f"Failed to delete agent: {str(e)}"
            }
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Unexpected error in delete_agent: {e}")
        return {
            "status": "error",
            "error_message": f"Unexpected error: {str(e)}"
        }


def update_agent(
    agent_name: str,
    description: Optional[str] = None,
    instruction: Optional[str] = None,
    model: Optional[str] = None,
    parent_agent: Optional[str] = None,
    tool_config: Optional[str] = None,
    mcp_servers_config: Optional[str] = None,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Update an agent configuration. The agent itself and parent agents will be reloaded after update.
    
    Args:
        agent_name: Name of the agent to update (required)
        description: New description (optional)
        instruction: New instruction/prompt (optional)
        model: New model name (optional)
        parent_agent: New parent agent name (optional, use empty string to remove parent)
        tool_config: New tool configuration as JSON string (optional, e.g., '{"google_drive": true, "image_tools": true}')
        mcp_servers_config: New MCP servers configuration as JSON string (optional, e.g., '{"mcpServers": {"server1": {"command": "npx", "args": ["mcp-server"]}}}')
        tool_context: Tool context to get current agent information (automatically provided)
        
    Returns:
        Dict with status, message, and agent details:
        - status: "success" or "error"
        - message: Human-readable message
        - agent_name: Name of updated agent
        - updated_fields: List of fields that were updated
        - agent_reloaded: Whether the agent itself was reloaded
        - parents_reloaded: List of parent agent names that were reloaded
    """
    try:
        # Admin-only check
        if not _is_admin_user(tool_context):
            user_id = _get_user_id_from_context(tool_context)
            logger.warning(f"Non-admin user '{user_id}' attempted to update agent '{agent_name}'")
            return {
                "status": "error",
                "error_message": "Permission denied. Only admin users can update agent configurations. Please contact an administrator if you need changes made."
            }

        # Get current agent name from tool context (for logging)
        current_agent_name = None
        if tool_context:
            current_agent_name = getattr(tool_context, 'agent_name', None)
        
        # Validate required fields
        if not agent_name or not agent_name.strip():
            return {
                "status": "error",
                "error_message": "Agent name is required"
            }
        
        agent_name = agent_name.strip()
        
        # Get database session
        db_client = get_database_client()
        session = db_client.get_session()
        if not session:
            return {
                "status": "error",
                "error_message": "Failed to get database session"
            }
        
        try:
            # Get agent configuration
            agent_config = session.query(AgentConfig).filter(
                AgentConfig.name == agent_name
            ).first()
            
            if not agent_config:
                return {
                    "status": "error",
                    "error_message": f"Agent '{agent_name}' not found"
                }
            
            # Get original parent agents before update
            original_parents = agent_config.get_parent_agents() if hasattr(agent_config, 'get_parent_agents') else []
            
            # Build update data
            updated_fields = []
            config_data = {}
            
            if description is not None:
                config_data['description'] = description.strip() if description.strip() else None
                updated_fields.append('description')
            
            if instruction is not None:
                config_data['instruction'] = instruction.strip() if instruction.strip() else None
                updated_fields.append('instruction')
            
            if model is not None:
                if model.strip():
                    config_data['model_name'] = model.strip()
                    updated_fields.append('model')
                else:
                    return {
                        "status": "error",
                        "error_message": "Model cannot be empty"
                    }
            
            if parent_agent is not None:
                if parent_agent.strip():
                    # Verify parent agent exists
                    parent_exists = session.query(AgentConfig).filter(
                        AgentConfig.name == parent_agent.strip()
                    ).first()
                    
                    if not parent_exists:
                        return {
                            "status": "error",
                            "error_message": f"Parent agent '{parent_agent}' does not exist"
                        }
                    
                    config_data['parent_agents'] = [parent_agent.strip()]
                    updated_fields.append('parent_agent')
                else:
                    # Remove parent (make it a root agent)
                    config_data['parent_agents'] = []
                    updated_fields.append('parent_agent')
            
            if tool_config is not None:
                # Validate and parse tool_config JSON
                try:
                    if tool_config.strip():
                        # Try to parse as JSON to validate
                        parsed_tool_config = json.loads(tool_config.strip())
                        # Store as JSON string
                        config_data['tool_config'] = json.dumps(parsed_tool_config) if parsed_tool_config else None
                        updated_fields.append('tool_config')
                    else:
                        # Empty string means clear tool_config
                        config_data['tool_config'] = None
                        updated_fields.append('tool_config')
                except json.JSONDecodeError as e:
                    return {
                        "status": "error",
                        "error_message": f"Invalid JSON in tool_config: {str(e)}"
                    }
            
            if mcp_servers_config is not None:
                # Validate and parse mcp_servers_config JSON
                try:
                    if mcp_servers_config.strip():
                        # Try to parse as JSON to validate
                        parsed_mcp_config = json.loads(mcp_servers_config.strip())
                        # Store as JSON string
                        config_data['mcp_servers_config'] = json.dumps(parsed_mcp_config) if parsed_mcp_config else None
                        updated_fields.append('mcp_servers_config')
                    else:
                        # Empty string means clear mcp_servers_config
                        config_data['mcp_servers_config'] = None
                        updated_fields.append('mcp_servers_config')
                except json.JSONDecodeError as e:
                    return {
                        "status": "error",
                        "error_message": f"Invalid JSON in mcp_servers_config: {str(e)}"
                    }
            
            if not config_data:
                return {
                    "status": "error",
                    "error_message": "No fields to update. Provide at least one field: description, instruction, model, parent_agent, tool_config, or mcp_servers_config"
                }
            
            # Update agent configuration
            for key, value in config_data.items():
                if hasattr(agent_config, key):
                    if key == 'parent_agents' and isinstance(value, list):
                        agent_config.set_parent_agents(value)
                    elif key in ['tool_config', 'mcp_servers_config']:
                        # These are already JSON strings, set directly
                        setattr(agent_config, key, value)
                    else:
                        setattr(agent_config, key, value)
            
            session.commit()
            
            logger.info(f"Updated agent '{agent_name}' by agent '{current_agent_name or 'unknown'}'. Updated fields: {', '.join(updated_fields)}")
            
            # Get updated parent agents
            updated_parents = agent_config.get_parent_agents() if hasattr(agent_config, 'get_parent_agents') else []
            
            # Collect all parent agents that need reloading (original + updated)
            parents_to_reload = set(original_parents + updated_parents)
            
            # Reload the agent itself (to pick up tool_config, mcp_servers_config, and other changes)
            agent_reloaded = False
            reload_result = reload_agent_cache(agent_name)
            if reload_result.get("success", False):
                agent_reloaded = True
            else:
                logger.warning(f"Failed to reload agent '{agent_name}' after update: {reload_result.get('message', 'Unknown error')}")
            
            # Reload parent agent(s)
            parents_reloaded = []
            if parents_to_reload:
                for parent_agent_name in parents_to_reload:
                    reload_result = reload_agent_cache(parent_agent_name)
                    if reload_result.get("success", False):
                        parents_reloaded.append(parent_agent_name)
                    else:
                        logger.warning(f"Failed to reload parent agent '{parent_agent_name}' after updating agent '{agent_name}': {reload_result.get('message', 'Unknown error')}")
            
            reload_message_parts = []
            if agent_reloaded:
                reload_message_parts.append("agent reloaded")
            if parents_reloaded:
                reload_message_parts.append(f"{len(parents_reloaded)} parent agent(s) reloaded")
            
            reload_message = ". " + ". ".join(reload_message_parts) + "." if reload_message_parts else ""
            
            return {
                "status": "success",
                "message": f"Agent '{agent_name}' updated successfully{reload_message}",
                "agent_name": agent_name,
                "updated_fields": updated_fields,
                "agent_reloaded": agent_reloaded,
                "parents_reloaded": parents_reloaded
            }
            
        except Exception as e:
            session.rollback()
            logger.error(f"Error updating agent: {e}")
            return {
                "status": "error",
                "error_message": f"Failed to update agent: {str(e)}"
            }
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Unexpected error in update_agent: {e}")
        return {
            "status": "error",
            "error_message": f"Unexpected error: {str(e)}"
        }


def read_agent(
    agent_name: Optional[str] = None,
    tool_context: ToolContext = None
) -> Dict[str, Any]:
    """
    Read an agent's configuration from the database. When called without agent_name,
    returns the calling agent's own configuration. When agent_name is provided, returns
    that agent's configuration only if it is a direct subagent of the calling agent.

    Args:
        agent_name: Name of a subagent to read (optional, defaults to the calling agent itself)
        tool_context: Tool context to get current agent information (automatically provided)

    Returns:
        Dict with status and agent configuration:
        - status: "success" or "error"
        - agent: Agent configuration dictionary (on success)
    """
    try:
        # Admin-only check
        if not _is_admin_user(tool_context):
            user_id = _get_user_id_from_context(tool_context)
            logger.warning(f"Non-admin user '{user_id}' attempted to read agent '{agent_name or getattr(tool_context, 'agent_name', None)}'")
            return {
                "status": "error",
                "error_message": "Permission denied. Only admin users can read agent configurations. Please contact an administrator if you need this information."
            }

        if not tool_context:
            return {
                "status": "error",
                "error_message": "Tool context is required"
            }

        current_agent_name = getattr(tool_context, 'agent_name', None)
        if not current_agent_name:
            return {
                "status": "error",
                "error_message": "Could not determine current agent name"
            }

        target_name = agent_name.strip() if agent_name and agent_name.strip() else current_agent_name

        if target_name != current_agent_name:
            target_config = _get_current_agent_config(target_name)
            if not target_config:
                return {
                    "status": "error",
                    "error_message": f"Agent '{target_name}' not found in database"
                }

            parent_agents = target_config.get('parent_agents', [])
            if current_agent_name not in parent_agents:
                return {
                    "status": "error",
                    "error_message": f"Access denied. Agent '{target_name}' is not a subagent of '{current_agent_name}'. You can only read your own configuration or your direct subagents."
                }

            return {
                "status": "success",
                "agent": target_config
            }

        config = _get_current_agent_config(current_agent_name)
        if not config:
            return {
                "status": "error",
                "error_message": f"Agent '{current_agent_name}' not found in database"
            }

        return {
            "status": "success",
            "agent": config
        }

    except Exception as e:
        logger.error(f"Unexpected error in read_agent: {e}")
        return {
            "status": "error",
            "error_message": f"Unexpected error: {str(e)}"
        }
