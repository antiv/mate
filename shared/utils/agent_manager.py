"""
Agent Manager for initializing and managing agents from database configuration.

This module handles:
- Loading agent configurations from the database
- Initializing root agents and their subagents recursively
- Managing agent hierarchies
"""

import json
import logging
from typing import Dict, List, Optional, Any, Type
from google.adk.planners import BuiltInPlanner, PlanReActPlanner
from google.genai import types
from sqlalchemy.orm import Session
from pydantic import BaseModel, create_model
from .database_client import get_database_client
from .models import AgentConfig, Project

logger = logging.getLogger(__name__)


def json_schema_to_pydantic_model(schema: dict, model_name: str) -> Type[BaseModel]:
    """
    Convert a JSON schema to a Pydantic model dynamically.
    
    Args:
        schema: JSON schema dictionary
        model_name: Name for the generated Pydantic model
        
    Returns:
        A Pydantic model class
    """
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise ValueError(f"Schema must be an object type, got: {schema.get('type', 'unknown')}")
    
    fields = {}
    properties = schema.get('properties', {})
    required_fields = set(schema.get('required', []))
    
    for field_name, field_schema in properties.items():
        field_type = _get_pydantic_field_type(field_schema)
        is_required = field_name in required_fields
        
        if is_required:
            fields[field_name] = (field_type, ...)
        else:
            fields[field_name] = (Optional[field_type], None)
    
    return create_model(model_name, **fields)


def _get_pydantic_field_type(field_schema: dict) -> Type:
    """Convert JSON schema field type to Pydantic type."""
    field_type = field_schema.get('type', 'string')
    
    if field_type == 'string':
        return str
    elif field_type == 'integer':
        return int
    elif field_type == 'number':
        return float
    elif field_type == 'boolean':
        return bool
    elif field_type == 'array':
        items_schema = field_schema.get('items', {})
        item_type = _get_pydantic_field_type(items_schema)
        return List[item_type]
    elif field_type == 'object':
        # For nested objects, we could recursively create models, but for simplicity,
        # we'll use Dict[str, Any] for now
        return Dict[str, Any]
    else:
        # Default to Any for unknown types
        return Any


class AgentManager:
    """Manages agent initialization and configuration from database."""
    
    def __init__(self):
        self.db_client = get_database_client()
        self.initialized_agents: Dict[str, Any] = {}
    
    def get_session(self) -> Optional[Session]:
        """Get database session."""
        return self.db_client.get_session()
    
    def get_projects(self) -> List[Project]:
        """Return all projects."""
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return []
        
        try:
            return session.query(Project).order_by(Project.name.asc()).all()
        except Exception as err:
            logger.error(f"Error fetching projects: {err}")
            return []
        finally:
            session.close()
    
    def get_agents_by_project(self, project_id: int, include_disabled: bool = False) -> List[AgentConfig]:
        """Return agents scoped to a project."""
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return []
        
        try:
            query = session.query(AgentConfig).filter(AgentConfig.project_id == project_id)
            if not include_disabled:
                query = query.filter(AgentConfig.disabled.is_(False))
            return query.all()
        except Exception as err:
            logger.error(f"Error fetching agents for project {project_id}: {err}")
            return []
        finally:
            session.close()
    
    def get_root_agent_by_name(self, name: str, project_id: Optional[int] = None) -> Optional[AgentConfig]:
        """Get agent configuration by name (any type can be used as root)."""
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return None
        
        try:
            query = session.query(AgentConfig).filter(
                AgentConfig.name == name,
                AgentConfig.disabled.is_(False)
            )
            if project_id is not None:
                query = query.filter(AgentConfig.project_id == project_id)
            return query.first()
        except Exception as e:
            logger.error(f"Error fetching agent {name}: {e}")
            return None
        finally:
            session.close()
    
    def get_subagents(self, parent_name: str) -> List[AgentConfig]:
        """Get all subagents for a given parent agent."""
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return []
        
        try:
            # Query for agents that have the parent_name in their parent_agents JSON array
            # This works for SQLite, PostgreSQL, and MySQL with JSON support
            subagents = session.query(AgentConfig).filter(
                AgentConfig.parent_agents.like(f'%"{parent_name}"%'),
                AgentConfig.disabled.is_(False)
            ).all()
            
            # Filter to ensure exact matches (in case of partial string matches)
            exact_matches = []
            for agent in subagents:
                if parent_name in agent.get_parent_agents():
                    exact_matches.append(agent)
            
            return exact_matches
        except Exception as e:
            logger.error(f"Error fetching subagents for {parent_name}: {e}")
            return []
        finally:
            session.close()
    
    def get_parent_agents(self, agent_name: str) -> List[str]:
        """Get all parent agents for a given agent."""
        session = self.get_session()
        if not session:
            logger.error("Failed to get database session")
            return []
        
        try:
            agent = session.query(AgentConfig).filter(
                AgentConfig.name == agent_name,
                AgentConfig.disabled.is_(False)
            ).first()
            
            if agent:
                return agent.get_parent_agents()
            return []
        except Exception as e:
            logger.error(f"Error fetching parent agents for {agent_name}: {e}")
            return []
        finally:
            session.close()
    
    def get_agents_by_parent(self, parent_name: str) -> List[AgentConfig]:
        """Get all agents that have a specific parent agent."""
        return self.get_subagents(parent_name)
    
    def initialize_agent_from_config(self, config: AgentConfig, sub_agents: List[Any] = None, parent_agent_type: str = None) -> Any:
        """Initialize an agent from its configuration."""
        print(f"[AGENT_MANAGER] initialize_agent_from_config called for agent: {config.name}")
        logger.info(f"initialize_agent_from_config called for agent: {config.name}")
        try:
            # Parse allowed roles if present
            allowed_roles = []
            if config.allowed_for_roles:
                try:
                    allowed_roles = json.loads(config.allowed_for_roles)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in allowed_for_roles for agent {config.name}")
            
            # Auto-configure File Search stores from database
            # Check if agent has any File Search stores assigned
            tool_config = config.tool_config
            try:
                from .file_search_service import FileSearchService
                file_search_service = FileSearchService(self.db_client)
                assigned_stores = file_search_service.get_stores_for_agent(config.name)
                
                if assigned_stores:
                    # Parse existing tool_config or create new one
                    if tool_config:
                        try:
                            tool_config_dict = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
                        except (json.JSONDecodeError, TypeError):
                            tool_config_dict = {}
                    else:
                        tool_config_dict = {}
                    
                    # Get store names
                    store_names = [store['store_name'] for store in assigned_stores if store.get('store_name')]
                    
                    if store_names:
                        # Add or update file_search config
                        tool_config_dict['file_search'] = {
                            'enabled': True,
                            'store_names': store_names
                        }
                        
                        # Update tool_config string
                        tool_config = json.dumps(tool_config_dict)
                        logger.info(f"✅ Auto-configured File Search for agent {config.name} with stores: {store_names}")
            except Exception as e:
                logger.warning(f"Failed to auto-configure File Search for agent {config.name}: {e}")
            
            # Create agent configuration dictionary
            agent_config = {
                'name': config.name,
                'type': config.type,
                'model_name': config.model_name,
                'description': config.description,
                'instruction': config.instruction,
                'mcp_servers_config': config.mcp_servers_config,
                'tool_config': tool_config,  # Use auto-configured tool_config
                'max_iterations': config.max_iterations,
                'parent_agents': config.get_parent_agents(),
                'planner_config': config.planner_config,
                'generate_content_config': config.generate_content_config,
                'input_schema': config.input_schema,
                'output_schema': config.output_schema,
                'include_contents': config.include_contents,
                'guardrail_config': config.guardrail_config,
                'allowed_for_roles': allowed_roles,
                'project_id': config.project_id
            }
            
            # Initialize agent based on type
            if config.type in ["llm", "sequential", "parallel", "loop"]:
                agent = self._initialize_agent(agent_config, sub_agents, parent_agent_type)
            else:
                logger.error(f"Unknown agent type: {config.type}")
                return None
            
            if agent:
                self.initialized_agents[config.name] = agent
                logger.info(f"Successfully initialized agent: {config.name}")
            
            return agent
            
        except Exception as e:
            logger.error(f"Error initializing agent {config.name}: {e}")
            return None
    
    def _create_planner(self, planner_config: dict) -> Optional[Any]:
        """
        Create a planner instance based on configuration.
        
        Args:
            planner_config: Dictionary containing planner type and parameters
                Example for BuiltInPlanner:
                {
                    "type": "BuiltInPlanner",
                    "thinking_config": {
                        "include_thoughts": true,
                        "thinking_budget": 1024
                    }
                }
                Example for PlanReActPlanner:
                {
                    "type": "PlanReActPlanner"
                }
        
        Returns:
            Planner instance or None
        """
        if not planner_config:
            return None
        
        planner_type = planner_config.get('type')
        
        try:
            if planner_type == 'BuiltInPlanner':
                thinking_config_data = planner_config.get('thinking_config', {})
                thinking_config = types.ThinkingConfig(
                    include_thoughts=thinking_config_data.get('include_thoughts', True),
                    thinking_budget=thinking_config_data.get('thinking_budget', 1024)
                )
                return BuiltInPlanner(thinking_config=thinking_config)
            
            elif planner_type == 'PlanReActPlanner':
                return PlanReActPlanner()
            
            else:
                logger.warning(f"Unknown planner type: {planner_type}")
                return None
        except Exception as e:
            logger.error(f"Error creating planner: {e}")
            return None
    
    def _initialize_agent(self, config: Dict[str, Any], sub_agents: List[Any] = None, parent_agent_type: str = None) -> Any:
        """Initialize an agent of any type."""
        # Import here to avoid circular imports
        from google.adk.agents import Agent, SequentialAgent, ParallelAgent, LoopAgent
        from .utils import create_model
        from ..callbacks.token_usage_callback import capture_model_name_callback, log_token_usage_callback
        from ..callbacks.rbac_callback import combined_rbac_and_token_callback
        from ..callbacks.user_profile_callback import combined_user_profile_and_rbac_callback
        from ..callbacks.guardrail_callback import guardrail_after_model_callback
        
        try:
            agent_type = config.get('type', 'llm').lower()
            agent_name = config['name']
            description = config.get('description', '')
            instruction = config.get('instruction', '')
            sub_agents = sub_agents or []
            
            print(f"[AGENT_MANAGER] Initializing agent {agent_name} (type: {agent_type})")
            logger.info(f"Initializing agent {agent_name} (type: {agent_type})")
            logger.debug(f"Original instruction length: {len(instruction)}")
            
            # Initialize tools using the tools module
            from .tools import ToolFactory
            tool_factory = ToolFactory()
            tools = tool_factory.create_tools(config)
            logger.info(f"Created {len(tools)} tools for agent {agent_name}")
            
            # Create agent based on type
            if agent_type in ['sequential']:
                agent = SequentialAgent(
                    name=agent_name,
                    sub_agents=sub_agents or [],
                    description=description
                )
            elif agent_type in ['parallel']:
                agent = ParallelAgent(
                    name=agent_name,
                    sub_agents=sub_agents or [],
                    description=description
                )
            elif agent_type in ['loop']:
                max_iterations = config.get('max_iterations')  # Use None as default (Google ADK default)
                agent = LoopAgent(
                    name=agent_name,
                    sub_agents=sub_agents or [],
                    description=description,
                    max_iterations=max_iterations
                )
            else:
                # Default to standard Agent for llm type
                # For sub-agents of parallel agents, use simpler callbacks to avoid TaskGroup issues
                if parent_agent_type == 'parallel':
                    before_callback = capture_model_name_callback
                    after_callback = log_token_usage_callback
                else:
                    # Full callbacks: user profile + RBAC + guardrails (before) and guardrails + token logging (after)
                    before_callback = combined_user_profile_and_rbac_callback

                    def _combined_after_with_guardrails(callback_context, llm_response):
                        guardrail_result = guardrail_after_model_callback(callback_context, llm_response)
                        if guardrail_result is not None:
                            log_token_usage_callback(callback_context, guardrail_result)
                            return guardrail_result
                        return log_token_usage_callback(callback_context, llm_response)

                    after_callback = _combined_after_with_guardrails
                
                # Initialize planner from configuration if present
                planner = None
                planner_config = config.get('planner_config', {})
                if planner_config:
                    if isinstance(planner_config, str):
                        # If planner_config is a JSON string, parse it
                        try:
                            planner_config = json.loads(planner_config)
                        except json.JSONDecodeError:
                            logger.error(f"Failed to parse planner_config JSON for {agent_name}: {planner_config}")
                            planner_config = {}
                    if planner_config:  # Check again after parsing
                        planner = self._create_planner(planner_config)
                        logger.info(f"Applied planner to agent {agent_name}: {planner_config.get('type', 'unknown')}")
                
                # Parse generate_content_config if present
                generate_config = None
                generate_content_config = config.get('generate_content_config')
                if generate_content_config:
                    if isinstance(generate_content_config, str):
                        try:
                            generate_config = json.loads(generate_content_config)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in generate_content_config for agent {agent_name}")
                    else:
                        generate_config = generate_content_config
                
                # Remove any tools from generate_config to comply with ADK requirements
                # ADK requires all tools to be set via Agent.tools, not generate_content_config.tools
                if generate_config and 'tools' in generate_config:
                    tools_in_config = generate_config.get('tools', [])
                    if tools_in_config:
                        logger.warning(f"Removing {len(tools_in_config) if isinstance(tools_in_config, list) else 1} tool(s) from generate_content_config for agent {agent_name} (ADK requires tools via Agent.tools)")
                        generate_config.pop('tools')
                
                # Parse input_schema if present and convert to Pydantic model
                input_schema = None
                input_schema_config = config.get('input_schema')
                if input_schema_config:
                    if isinstance(input_schema_config, str):
                        try:
                            schema_dict = json.loads(input_schema_config)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in input_schema for agent {agent_name}")
                            schema_dict = None
                    else:
                        schema_dict = input_schema_config
                    
                    if schema_dict:
                        try:
                            # Convert JSON schema to Pydantic model
                            model_name = f"{agent_name.replace('_', '').title()}InputModel"
                            input_schema = json_schema_to_pydantic_model(schema_dict, model_name)
                            logger.debug(f"Converted input_schema to Pydantic model {model_name} for agent {agent_name}")
                        except Exception as e:
                            logger.warning(f"Failed to convert input_schema to Pydantic model for agent {agent_name}: {e}")
                            input_schema = None
                
                # Parse output_schema if present and convert to Pydantic model
                output_schema = None
                output_schema_config = config.get('output_schema')
                if output_schema_config:
                    if isinstance(output_schema_config, str):
                        try:
                            schema_dict = json.loads(output_schema_config)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in output_schema for agent {agent_name}")
                            schema_dict = None
                    else:
                        schema_dict = output_schema_config
                    
                    if schema_dict:
                        try:
                            # Convert JSON schema to Pydantic model
                            model_name = f"{agent_name.replace('_', '').title()}OutputModel"
                            output_schema = json_schema_to_pydantic_model(schema_dict, model_name)
                            logger.debug(f"Converted output_schema to Pydantic model {model_name} for agent {agent_name}")
                        except Exception as e:
                            logger.warning(f"Failed to convert output_schema to Pydantic model for agent {agent_name}: {e}")
                            output_schema = None
                
                # Generate output_key from agent name
                output_key = f"{agent_name}_output"
                
                # Get include_contents if present (simple string: 'default' or 'none')
                include_contents = config.get('include_contents')
                if include_contents and not include_contents.strip():
                    include_contents = None  # Treat empty string as None
                
                # Build agent parameters
                agent_params = {
                    'name': agent_name,
                    'model': create_model(model_name=config.get('model_name')),
                    'description': description,
                    'instruction': instruction,
                    'tools': tools,
                    'sub_agents': sub_agents or [],
                    'output_key': output_key,
                    'before_model_callback': before_callback,
                    'after_model_callback': after_callback
                }
                
                # Add optional parameters if present
                if planner:
                    agent_params['planner'] = planner
                if generate_config:
                    # Tools were already removed from generate_config above
                    # Convert dict to Pydantic model if possible
                    generate_config_for_agent = generate_config.copy()
                    try:
                        from google.genai import types
                        # Only convert if there's something left after removing tools
                        if generate_config_for_agent:
                            generate_content_config_model = types.GenerateContentConfig(**generate_config_for_agent)
                            agent_params['generate_content_config'] = generate_content_config_model
                        # If empty after removing tools, don't pass generate_content_config
                    except ImportError:
                        logger.warning(f"google.genai.types not available, passing generate_config as dict")
                        if generate_config_for_agent:
                            agent_params['generate_content_config'] = generate_config_for_agent
                    except Exception as e:
                        logger.error(f"Failed to convert generate_content_config to Pydantic model: {e}", exc_info=True)
                        # Fallback: pass as dict if not empty
                        if generate_config_for_agent:
                            agent_params['generate_content_config'] = generate_config_for_agent
                if input_schema:
                    agent_params['input_schema'] = input_schema
                if output_schema:
                    agent_params['output_schema'] = output_schema
                if include_contents:
                    agent_params['include_contents'] = include_contents
                
                agent = Agent(**agent_params)
            
            logger.info(f"{agent_type.title()} agent {agent_name} initialized")
            return agent
        except Exception as e:
            logger.error(f"Error creating {config.get('type', 'unknown')} agent {config.get('name', 'unknown')}: {e}")
            return None
    
    def initialize_agent_hierarchy(self, root_agent_name: str, hardcoded_agents: List[Any] = None) -> Optional[Any]:
        """
        Initialize a complete agent hierarchy starting from a root agent.
        
        Args:
            root_agent_name: Name of the root agent to initialize
            hardcoded_agents: Optional list of hardcoded agent instances to include as subagents
            
        Returns:
            Initialized root agent with all subagents (DB + hardcoded)
        """
        logger.info(f"Initializing agent hierarchy for root agent: {root_agent_name}")
        
        # Get root agent configuration
        root_config = self.get_root_agent_by_name(root_agent_name)
        if not root_config:
            logger.error(f"Root agent {root_agent_name} not found")
            return None
        
        # Initialize root agent with its database subagents
        root_agent = self._initialize_agent_with_subagents(root_config)
        if not root_agent:
            logger.error(f"Failed to initialize root agent {root_agent_name}")
            return None
        
        # Add hardcoded agents as additional subagents
        if hardcoded_agents:
            self._add_hardcoded_subagents(root_agent, hardcoded_agents)
        
        return root_agent
    
    def _add_hardcoded_subagents(self, parent_agent: Any, hardcoded_agents: List[Any]) -> None:
        """
        Add hardcoded agents as subagents to the parent agent.
        
        Args:
            parent_agent: The parent agent to add subagents to
            hardcoded_agents: List of hardcoded agent instances to add
        """
        if not hasattr(parent_agent, 'sub_agents'):
            logger.warning(f"Agent {parent_agent.name} does not have sub_agents attribute")
            return
        
        # Get current subagents list
        current_subagents = list(parent_agent.sub_agents) if parent_agent.sub_agents else []
        
        # Add hardcoded agents
        for hardcoded_agent in hardcoded_agents:
            if hardcoded_agent is not None:
                current_subagents.append(hardcoded_agent)
                logger.info(f"Added hardcoded subagent: {hardcoded_agent.name} to {parent_agent.name}")
        
        # Update the parent agent's subagents
        parent_agent.sub_agents = current_subagents
        logger.info(f"Total subagents for {parent_agent.name}: {len(current_subagents)}")
    
    def _initialize_agent_with_subagents(self, config: AgentConfig) -> Optional[Any]:
        """Initialize an agent with all its subagents recursively."""
        # Get all subagents for this agent
        subagent_configs = self.get_subagents(config.name)
        
        # Initialize all subagents recursively
        initialized_subagents = []
        for subagent_config in subagent_configs:
            logger.info(f"Initializing subagent: {subagent_config.name} under parent: {config.name}")
            
            # Recursively initialize the subagent with its own subagents
            subagent = self._initialize_agent_with_subagents(subagent_config)
            if subagent:
                initialized_subagents.append(subagent)
                # Only store in initialized_agents if not already there
                if subagent_config.name not in self.initialized_agents:
                    self.initialized_agents[subagent_config.name] = subagent
            else:
                logger.error(f"Failed to initialize subagent {subagent_config.name}")
        
        # Initialize the current agent with its subagents
        agent = self.initialize_agent_from_config(config, initialized_subagents, config.type)
        if agent:
            self.initialized_agents[config.name] = agent
            logger.info(f"Successfully initialized agent: {config.name} with {len(initialized_subagents)} subagents")
        
        return agent
    
    def build_agent_tree(self, root_agent_name: str, hardcoded_agents: List[Any] = None) -> Optional[Any]:
        """
        Build a complete agent tree starting from a root agent, handling multiple parent relationships.
        
        This method first creates all agents independently, then builds the tree relationships
        to handle multi-parent scenarios properly.
        
        Args:
            root_agent_name: Name of the root agent to start the tree from
            hardcoded_agents: Optional list of hardcoded agent instances to include
            
        Returns:
            Initialized root agent with complete tree structure
        """
        logger.info(f"Building agent tree for root agent: {root_agent_name}")
        
        # Clear any existing initialized agents to start fresh
        self.clear_initialized_agents()
        
        
        # Get root agent configuration
        root_config = self.get_root_agent_by_name(root_agent_name)
        if not root_config:
            logger.error(f"Root agent {root_agent_name} not found")
            return None
        
        # Step 1: Create all agents independently (without subagents)
        self._create_all_agents_independently(root_agent_name)
        
        # Step 2: Build the tree relationships
        root_agent = self._build_tree_relationships(root_config)
        if not root_agent:
            logger.error(f"Failed to build agent tree for root agent {root_agent_name}")
            return None
        
        # Add hardcoded agents as additional subagents to root
        if hardcoded_agents:
            self._add_hardcoded_subagents(root_agent, hardcoded_agents)
        
        logger.info(f"Successfully built agent tree with {len(self.initialized_agents)} total agents")
        return root_agent
    
    def _build_agent_tree_recursive(self, config: AgentConfig, parent_name: str = None) -> Optional[Any]:
        """
        Recursively build agent tree, handling multiple parent relationships.
        
        This method allows the same agent instance to be a subagent of multiple parents,
        similar to how hardcoded agents work in the system.
        """
        # Check if this agent is already initialized
        if config.name in self.initialized_agents:
            logger.info(f"Agent {config.name} already initialized, returning existing instance")
            return self.initialized_agents[config.name]
        
        # Get all subagents for this agent
        subagent_configs = self.get_subagents(config.name)
        
        # Initialize all subagents recursively
        initialized_subagents = []
        for subagent_config in subagent_configs:
            logger.info(f"Building subagent: {subagent_config.name} under parent: {config.name}")
            
            # Recursively build the subagent
            subagent = self._build_agent_tree_recursive(subagent_config, config.name)
            if subagent:
                initialized_subagents.append(subagent)
            else:
                logger.error(f"Failed to build subagent {subagent_config.name}")
        
        # Initialize the current agent with its subagents
        agent = self.initialize_agent_from_config(config, initialized_subagents, config.type)
        if agent:
            self.initialized_agents[config.name] = agent
            logger.info(f"Successfully built agent: {config.name} with {len(initialized_subagents)} subagents")
        
        return agent
    
    def _create_all_agents_independently(self, root_agent_name: str):
        """Create all agents independently without subagent relationships."""
        session = self.db_client.get_session()
        if not session:
            return
        
        try:
            # Get all agent configurations
            from shared.utils.models import AgentConfig
            all_configs = session.query(AgentConfig).filter_by(disabled=False).all()
            
            # Create each agent independently (without subagents)
            for config in all_configs:
                if config.name not in self.initialized_agents:
                    logger.info(f"Creating agent independently: {config.name}")
                    agent = self.initialize_agent_from_config(config, [], config.type)
                    if agent:
                        self.initialized_agents[config.name] = agent
                        logger.info(f"Successfully created independent agent: {config.name}")
                    else:
                        logger.error(f"Failed to create independent agent: {config.name}")
        finally:
            session.close()
    
    def _build_tree_relationships(self, root_config: AgentConfig) -> Optional[Any]:
        """Build the tree relationships between already created agents."""
        # Get the root agent
        root_agent = self.initialized_agents.get(root_config.name)
        if not root_agent:
            logger.error(f"Root agent {root_config.name} not found in initialized agents")
            return None
        
        # Build relationships recursively
        self._build_relationships_recursive(root_config)
        
        return root_agent
    
    def _build_relationships_recursive(self, config: AgentConfig):
        """Recursively build relationships for an agent and its subagents."""
        agent = self.initialized_agents.get(config.name)
        if not agent:
            return
        
        # Get subagents for this agent
        subagent_configs = self.get_subagents(config.name)
        
        # Add subagents to this agent
        for subagent_config in subagent_configs:
            subagent = self._get_or_create_subagent_instance(subagent_config, config.name)
            if subagent and hasattr(agent, 'sub_agents'):
                # Add the subagent to this parent
                if not agent.sub_agents:
                    agent.sub_agents = []
                if subagent not in agent.sub_agents:
                    agent.sub_agents.append(subagent)
                    logger.info(f"Added {subagent.name} as subagent to {agent.name}")
                
                # Recursively build relationships for the subagent
                self._build_relationships_recursive(subagent_config)
    
    def _get_or_create_subagent_instance(self, subagent_config: AgentConfig, parent_name: str):
        """Get existing subagent or create new instance for multi-parent agents."""
        # Check if the subagent is already initialized
        existing_agent = self.initialized_agents.get(subagent_config.name)
        
        if existing_agent:
            # Check if this agent has multiple parents
            parent_agents = subagent_config.get_parent_agents()
            has_multiple_parents = len(parent_agents) > 1
            
            if has_multiple_parents:
                # For multi-parent agents, create a new instance with a unique name
                instance_name = f"{subagent_config.name}_{parent_name}"
                if instance_name not in self.initialized_agents:
                    logger.info(f"Creating new instance {instance_name} for multi-parent agent {subagent_config.name}")
                    
                    # Create new agent instance with modified name
                    agent_config_dict = {
                        'name': instance_name,
                        'type': subagent_config.type,
                        'model_name': subagent_config.model_name,
                        'description': subagent_config.description,
                        'instruction': subagent_config.instruction,
                        'mcp_servers_config': subagent_config.mcp_servers_config,
                        'tool_config': subagent_config.tool_config,
                        'max_iterations': subagent_config.max_iterations,
                        'parent_agents': subagent_config.get_parent_agents(),
                        'planner_config': subagent_config.planner_config,
                        'generate_content_config': subagent_config.generate_content_config,
                        'input_schema': subagent_config.input_schema,
                        'output_schema': subagent_config.output_schema,
                        'include_contents': subagent_config.include_contents,
                        'guardrail_config': subagent_config.guardrail_config,
                        'allowed_for_roles': subagent_config.allowed_for_roles
                    }
                    
                    new_agent = self._initialize_agent(agent_config_dict, [], subagent_config.type)
                    if new_agent:
                        self.initialized_agents[instance_name] = new_agent
                        logger.info(f"Successfully created new instance: {instance_name}")
                        return new_agent
                    else:
                        logger.error(f"Failed to create new instance: {instance_name}")
                        return None
                else:
                    # Return existing instance
                    return self.initialized_agents[instance_name]
            else:
                # Single-parent agent, use the existing instance
                logger.info(f"Using existing instance for single-parent agent {subagent_config.name}")
                return existing_agent
        else:
            # Agent not initialized yet, this shouldn't happen in our two-phase approach
            logger.error(f"Agent {subagent_config.name} not found in initialized agents")
            return None
    
    
    def _agent_has_parent(self, agent_name: str) -> bool:
        """Check if an agent already has a parent by looking at the database configuration."""
        session = self.db_client.get_session()
        if not session:
            return False
        
        try:
            from shared.utils.models import AgentConfig
            agent_config = session.query(AgentConfig).filter_by(name=agent_name).first()
            if agent_config:
                parent_agents = agent_config.get_parent_agents()
                return len(parent_agents) > 0
            return False
        finally:
            session.close()
    
    def get_initialized_agent(self, name: str) -> Optional[Any]:
        """Get an already initialized agent by name."""
        return self.initialized_agents.get(name)
    
    def get_all_initialized_agents(self) -> Dict[str, Any]:
        """Get all initialized agents."""
        return self.initialized_agents.copy()
    
    def clear_initialized_agents(self):
        """Clear all initialized agents."""
        self.initialized_agents.clear()


# Global instance
_agent_manager = None

def get_agent_manager() -> AgentManager:
    """Get the global agent manager instance."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = AgentManager()
    return _agent_manager
