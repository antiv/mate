"""
SQLAlchemy models for the application.
"""

from sqlalchemy import Column, Integer, String, DateTime, create_engine, UUID, Text, ForeignKey, Boolean, Date, JSON, Float
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from datetime import datetime, timezone
from typing import Optional
import json

Base = declarative_base()


class User(Base):
    """Model for user management and role-based access control."""
    
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=False, unique=True)  # Primary user identifier
    roles = Column(Text, nullable=False, default='["user"]')  # JSON array of roles
    profile_data = Column(Text, nullable=True)  # User profile data for agent personalization (text format)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), 
                       onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    def get_roles(self) -> list:
        """Get user roles as a list."""
        try:
            return json.loads(self.roles) if self.roles else ["user"]
        except json.JSONDecodeError:
            return ["user"]
    
    def set_roles(self, roles: list):
        """Set user roles from a list."""
        self.roles = json.dumps(roles)
    
    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in self.get_roles()
    
    def add_role(self, role: str):
        """Add a role to the user."""
        current_roles = self.get_roles()
        if role not in current_roles:
            current_roles.append(role)
            self.set_roles(current_roles)
    
    def remove_role(self, role: str):
        """Remove a role from the user."""
        current_roles = self.get_roles()
        if role in current_roles:
            current_roles.remove(role)
            self.set_roles(current_roles)
    
    def get_profile_data(self) -> Optional[str]:
        """Get user profile data as text."""
        return self.profile_data if self.profile_data else None
    
    def set_profile_data(self, profile_data: Optional[str]):
        """Set user profile data from text."""
        self.profile_data = profile_data
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'roles': self.get_roles(),
            'profile_data': self.get_profile_data(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class TokenUsageLog(Base):
    """Model for token usage logs."""
    
    __tablename__ = 'token_usage_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(255), nullable=False)  # Required field based on error
    session_id = Column(String(255), nullable=True)
    user_id = Column(String(255), nullable=True)
    agent_name = Column(String(255), nullable=True)
    model_name = Column(String(255), nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    response_tokens = Column(Integer, nullable=True)
    thoughts_tokens = Column(Integer, nullable=True)
    tool_use_tokens = Column(Integer, nullable=True)
    status = Column(String(50), nullable=True, default='SUCCESS')  # SUCCESS, ERROR, ACCESS_DENIED, etc.
    error_description = Column(Text, nullable=True)  # Description of error if status is not SUCCESS
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'request_id': self.request_id,
            'session_id': self.session_id,
            'user_id': self.user_id,
            'agent_name': self.agent_name,
            'model_name': self.model_name,
            'prompt_tokens': self.prompt_tokens,
            'response_tokens': self.response_tokens,
            'thoughts_tokens': self.thoughts_tokens,
            'tool_use_tokens': self.tool_use_tokens,
            'status': self.status,
            'error_description': self.error_description,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


class GuardrailLog(Base):
    """Model for guardrail trigger logs."""

    __tablename__ = 'guardrail_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(255), nullable=False)
    session_id = Column(String(255), nullable=True)
    user_id = Column(String(255), nullable=True)
    agent_name = Column(String(255), nullable=True)
    guardrail_type = Column(String(100), nullable=False)
    phase = Column(String(20), nullable=False, default='input')  # input | output
    action_taken = Column(String(50), nullable=False)  # block | warn | log | redact
    matched_content = Column(Text, nullable=True)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'request_id': self.request_id,
            'session_id': self.session_id,
            'user_id': self.user_id,
            'agent_name': self.agent_name,
            'guardrail_type': self.guardrail_type,
            'phase': self.phase,
            'action_taken': self.action_taken,
            'matched_content': self.matched_content,
            'details': self.details,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }


class Project(Base):
    """Model for grouping agents under projects."""
    
    __tablename__ = 'projects'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    template_id = Column(String(255), nullable=True)  # Source template ID (e.g., "jira-worklog-assistant")
    template_version = Column(String(50), nullable=True)  # Template version at import/sync (e.g., "3.0")
    template_prefix = Column(String(255), nullable=True)  # Template agent_prefix for name mapping
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    agents = relationship("AgentConfig", back_populates="project", cascade="all, delete-orphan")
    
    def to_dict(self) -> dict:
        """Convert project instance to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'template_id': self.template_id,
            'template_version': self.template_version,
            'template_prefix': self.template_prefix,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AgentConfig(Base):
    """Model for agent configuration."""
    
    __tablename__ = 'agents_config'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    type = Column(String(50), nullable=False)  # llm, sequential, parallel, loop
    model_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    instruction = Column(Text, nullable=True)
    mcp_servers_config = Column(Text, nullable=True)  # JSON object containing multiple MCP server configurations
    parent_agents = Column(Text, nullable=True)  # JSON array of parent agent names
    allowed_for_roles = Column(Text, nullable=True)  # JSON string of roles
    tool_config = Column(Text, nullable=True)  # JSON string for tool configuration
    max_iterations = Column(Integer, nullable=True)  # For LoopAgent - maximum number of iterations
    planner_config = Column(Text, nullable=True)  # JSON string for planner configuration
    generate_content_config = Column(Text, nullable=True)  # JSON string for LLM generation config (temperature, top_p, etc.)
    input_schema = Column(Text, nullable=True)  # JSON string for input validation schema
    output_schema = Column(Text, nullable=True)  # JSON string for output structure schema
    include_contents = Column(Text, nullable=True)  # JSON array for managing context/history
    guardrail_config = Column(Text, nullable=True)  # JSON config for safety guardrails (PII, injection, content policy, etc.)
    disabled = Column(Boolean, nullable=False, default=False)  # Flag to disable agent
    hardcoded = Column(Boolean, nullable=False, default=False)  # Flag to mark agent as hardcoded (skip folder creation)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, default=1)
    
    project = relationship("Project", back_populates="agents")
    
    # Note: Relationships are handled in the AgentManager class
    # to avoid complex self-referential relationship issues
    
    def get_parent_agents(self) -> list:
        """Get parent agents as a list."""
        try:
            return json.loads(self.parent_agents) if self.parent_agents else []
        except json.JSONDecodeError:
            return []
    
    def set_parent_agents(self, parent_agents: list):
        """Set parent agents from a list."""
        self.parent_agents = json.dumps(parent_agents) if parent_agents else None
    
    def add_parent_agent(self, parent_agent: str):
        """Add a parent agent to the list."""
        current_parents = self.get_parent_agents()
        if parent_agent not in current_parents:
            current_parents.append(parent_agent)
            self.set_parent_agents(current_parents)
    
    def remove_parent_agent(self, parent_agent: str):
        """Remove a parent agent from the list."""
        current_parents = self.get_parent_agents()
        if parent_agent in current_parents:
            current_parents.remove(parent_agent)
            self.set_parent_agents(current_parents)
    
    def get_mcp_servers_config(self) -> dict:
        """Get MCP servers configuration as a dictionary."""
        try:
            return json.loads(self.mcp_servers_config) if self.mcp_servers_config else {}
        except json.JSONDecodeError:
            return {}
    
    def set_mcp_servers_config(self, config: dict):
        """Set MCP servers configuration from a dictionary."""
        self.mcp_servers_config = json.dumps(config) if config else None
    
    def get_planner_config(self) -> dict:
        """Get planner configuration as a dictionary."""
        try:
            return json.loads(self.planner_config) if self.planner_config else {}
        except json.JSONDecodeError:
            return {}
    
    def set_planner_config(self, config: dict):
        """Set planner configuration from a dictionary."""
        self.planner_config = json.dumps(config) if config else None
    
    def get_guardrail_config(self) -> dict:
        """Get guardrail configuration as a dictionary."""
        try:
            return json.loads(self.guardrail_config) if self.guardrail_config else {}
        except json.JSONDecodeError:
            return {}

    def set_guardrail_config(self, config: dict):
        """Set guardrail configuration from a dictionary."""
        self.guardrail_config = json.dumps(config) if config else None

    def has_parent_agent(self, parent_agent: str) -> bool:
        """Check if agent has a specific parent agent."""
        return parent_agent in self.get_parent_agents()
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'model_name': self.model_name,
            'description': self.description,
            'instruction': self.instruction,
            'mcp_servers_config': self.mcp_servers_config,
            'parent_agents': self.get_parent_agents(),
            'allowed_for_roles': self.allowed_for_roles,
            'tool_config': self.tool_config,
            'planner_config': self.planner_config,
            'max_iterations': self.max_iterations,
            'generate_content_config': self.generate_content_config,
            'input_schema': self.input_schema,
            'output_schema': self.output_schema,
            'include_contents': self.include_contents,
            'guardrail_config': self.guardrail_config,
            'disabled': self.disabled,
            'hardcoded': self.hardcoded,
            'project_id': self.project_id,
            'project': self.project.to_dict() if self.project else None
        }


class AgentConfigVersion(Base):
    """Versioned snapshots of agent configurations for history, diff, and rollback."""

    __tablename__ = 'agent_config_versions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_config_id = Column(Integer, ForeignKey('agents_config.id', ondelete='CASCADE'), nullable=False)
    version_number = Column(Integer, nullable=False)
    config_snapshot = Column(Text, nullable=False)
    changed_by = Column(String(255), nullable=True)
    change_type = Column(String(50), nullable=False, default='update')
    tag = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    agent_config = relationship("AgentConfig", backref="versions")

    def get_snapshot(self) -> dict:
        try:
            return json.loads(self.config_snapshot) if self.config_snapshot else {}
        except json.JSONDecodeError:
            return {}

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'agent_config_id': self.agent_config_id,
            'version_number': self.version_number,
            'config_snapshot': self.get_snapshot(),
            'changed_by': self.changed_by,
            'change_type': self.change_type,
            'tag': self.tag,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Credential(Base):
    """Model for storing tool credentials."""
    
    __tablename__ = 'credentials'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    app_name = Column(String(255), nullable=False)
    user_id = Column(String(255), nullable=False)
    credential_key = Column(String(500), nullable=False)
    credential_data = Column(Text, nullable=False)  # JSON serialized credential
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), 
                       onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Create composite unique index for app_name + user_id + credential_key
    __table_args__ = (
        {'extend_existing': True}
    )
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'app_name': self.app_name,
            'user_id': self.user_id,
            'credential_key': self.credential_key,
            'credential_data': self.credential_data,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


# Memory Models for DBMemoryService

class MemorySession(Base):
    """Model for storing memory sessions."""
    
    __tablename__ = 'memory_sessions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), nullable=False, unique=True)
    app_name = Column(String(255), nullable=False)
    user_id = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), 
                       onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationship to memory events
    events = relationship("MemoryEvent", back_populates="session", cascade="all, delete-orphan")
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'app_name': self.app_name,
            'user_id': self.user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class MemoryEvent(Base):
    """Model for storing memory events."""
    
    __tablename__ = 'memory_events'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), ForeignKey('memory_sessions.session_id'), nullable=False)
    event_id = Column(String(255), nullable=False)  # Original event ID from session
    content = Column(JSON, nullable=True)  # Store event content as JSON
    author = Column(String(255), nullable=True)
    timestamp = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Relationship to memory session
    session = relationship("MemorySession", back_populates="events")
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'event_id': self.event_id,
            'content': self.content,
            'author': self.author,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# File Search Models for RAG functionality

class FileSearchStore(Base):
    """Model for Gemini File Search stores."""
    
    __tablename__ = 'file_search_stores'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    store_name = Column(String(500), nullable=False, unique=True)  # Full store name from Gemini API
    display_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    created_by_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    project = relationship("Project")
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'store_name': self.store_name,
            'display_name': self.display_name,
            'description': self.description,
            'project_id': self.project_id,
            'created_by_agent': self.created_by_agent,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AgentFileSearchStore(Base):
    """Many-to-many relationship between agents and file search stores."""
    
    __tablename__ = 'agent_file_search_stores'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String(255), nullable=False)
    store_id = Column(Integer, ForeignKey('file_search_stores.id'), nullable=False)
    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    
    store = relationship("FileSearchStore")
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'agent_name': self.agent_name,
            'store_id': self.store_id,
            'is_primary': self.is_primary,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'store': self.store.to_dict() if self.store else None
        }


class MemoryBlock(Base):
    """Model for local memory blocks (dynamic instructions, user facts, etc.)."""

    __tablename__ = 'memory_blocks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    label = Column(String(500), nullable=False)
    value = Column(Text, nullable=False, default='')
    description = Column(Text, nullable=True)
    block_metadata = Column('metadata', Text, nullable=True)  # JSON string; 'metadata' reserved in Declarative
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    project = relationship("Project")

    def get_metadata(self) -> Optional[dict]:
        """Get metadata as dict."""
        if not self.block_metadata:
            return None
        try:
            return json.loads(self.block_metadata)
        except json.JSONDecodeError:
            return None

    def set_metadata(self, data: Optional[dict]):
        """Set metadata from dict."""
        self.block_metadata = json.dumps(data) if data else None

    def to_dict(self) -> dict:
        """Convert to dict compatible with block API (block_id, label, value, etc.)."""
        return {
            'block_id': str(self.id),
            'label': self.label,
            'value': self.value,
            'description': self.description,
            'metadata': self.get_metadata(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class AuditLog(Base):
    """Append-only audit log for compliance (EU AI Act). No UPDATE/DELETE on application side."""

    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    actor = Column(String(255), nullable=False)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(String(500), nullable=True)
    details = Column(Text, nullable=True)  # JSON string for portability (SQLite/MySQL/PostgreSQL)
    ip_address = Column(String(45), nullable=True)

    def get_details(self) -> Optional[dict]:
        if not self.details:
            return None
        try:
            return json.loads(self.details)
        except json.JSONDecodeError:
            return None

    def set_details(self, data: Optional[dict]):
        self.details = json.dumps(data) if data else None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'actor': self.actor,
            'action': self.action,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'details': self.get_details(),
            'ip_address': self.ip_address,
        }


class WidgetApiKey(Base):
    """Model for embeddable chat widget API keys, scoped to a project and root agent."""

    __tablename__ = 'widget_api_keys'

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_key = Column(String(255), unique=True, nullable=False, index=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False)
    agent_name = Column(String(255), nullable=False)
    label = Column(String(255), nullable=True)
    allowed_origins = Column(Text, nullable=True)  # JSON array of allowed origins, null = all
    is_active = Column(Boolean, default=True, nullable=False)
    widget_config = Column(Text, nullable=True)  # JSON: greeting, theme, button color, etc.
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    project = relationship("Project")

    def get_allowed_origins(self) -> Optional[list]:
        if not self.allowed_origins:
            return None
        try:
            return json.loads(self.allowed_origins)
        except json.JSONDecodeError:
            return None

    def set_allowed_origins(self, origins: Optional[list]):
        self.allowed_origins = json.dumps(origins) if origins else None

    def get_widget_config(self) -> dict:
        if not self.widget_config:
            return {}
        try:
            return json.loads(self.widget_config)
        except json.JSONDecodeError:
            return {}

    def set_widget_config(self, config: dict):
        self.widget_config = json.dumps(config) if config else None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'api_key': self.api_key,
            'project_id': self.project_id,
            'agent_name': self.agent_name,
            'label': self.label,
            'allowed_origins': self.get_allowed_origins(),
            'is_active': self.is_active,
            'widget_config': self.get_widget_config(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class RateLimitConfig(Base):
    """Model for rate limit and budget configuration per user, agent, or project."""

    __tablename__ = 'rate_limit_config'

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(String(20), nullable=False)  # user, agent, project
    scope_id = Column(String(500), nullable=False)
    requests_per_minute = Column(Integer, nullable=True)
    tokens_per_hour = Column(Integer, nullable=True)
    tokens_per_day = Column(Integer, nullable=True)
    tokens_per_month = Column(Integer, nullable=True)
    max_tokens_per_request = Column(Integer, nullable=True)
    action_on_limit = Column(String(20), nullable=False, default='block')  # warn, throttle, block
    alert_thresholds = Column(Text, nullable=True)  # JSON array e.g. [80, 90, 100]
    alert_webhook_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    def get_alert_thresholds(self) -> list:
        if not self.alert_thresholds:
            return [80, 90, 100]
        try:
            return json.loads(self.alert_thresholds)
        except json.JSONDecodeError:
            return [80, 90, 100]

    def set_alert_thresholds(self, thresholds: list):
        self.alert_thresholds = json.dumps(thresholds) if thresholds else None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'scope': self.scope,
            'scope_id': self.scope_id,
            'requests_per_minute': self.requests_per_minute,
            'tokens_per_hour': self.tokens_per_hour,
            'tokens_per_day': self.tokens_per_day,
            'tokens_per_month': self.tokens_per_month,
            'max_tokens_per_request': self.max_tokens_per_request,
            'action_on_limit': self.action_on_limit,
            'alert_thresholds': self.get_alert_thresholds(),
            'alert_webhook_url': self.alert_webhook_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class FileSearchDocument(Base):
    """Model for documents/files in file search stores."""

    __tablename__ = 'file_search_documents'

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(Integer, ForeignKey('file_search_stores.id'), nullable=False)
    document_name = Column(String(500), nullable=False)  # Full document name from Gemini API
    display_name = Column(String(255), nullable=True)
    file_path = Column(String(1000), nullable=True)  # Original file path if available
    file_size = Column(Integer, nullable=True)  # Use Integer for SQLite compatibility
    mime_type = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default='processing')  # processing, completed, failed
    uploaded_by_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    store = relationship("FileSearchStore")
    
    def to_dict(self) -> dict:
        """Convert model instance to dictionary."""
        return {
            'id': self.id,
            'store_id': self.store_id,
            'document_name': self.document_name,
            'display_name': self.display_name,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'mime_type': self.mime_type,
            'status': self.status,
            'uploaded_by_agent': self.uploaded_by_agent,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class TestCase(Base):
    """Eval test case: an input/expected-output pair used to score an agent version."""

    __tablename__ = 'test_cases'

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String(255), nullable=False)
    version_id = Column(Integer, ForeignKey('agent_config_versions.id', ondelete='SET NULL'), nullable=True)
    input = Column(Text, nullable=False)
    expected_output = Column(Text, nullable=False)
    eval_method = Column(String(50), nullable=False, default='exact_match')
    judge_model = Column(String(255), nullable=True)
    threshold = Column(Float, nullable=False, default=0.7)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_by = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    version = relationship("AgentConfigVersion")
    results = relationship("EvalResult", back_populates="test_case", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'agent_name': self.agent_name,
            'version_id': self.version_id,
            'input': self.input,
            'expected_output': self.expected_output,
            'eval_method': self.eval_method,
            'judge_model': self.judge_model,
            'threshold': self.threshold,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_by': self.created_by,
            'is_active': self.is_active,
        }


class EvalResult(Base):
    """Result of running a single test case against a specific agent version."""

    __tablename__ = 'eval_results'

    id = Column(Integer, primary_key=True, autoincrement=True)
    test_case_id = Column(Integer, ForeignKey('test_cases.id', ondelete='CASCADE'), nullable=False)
    version_id = Column(Integer, ForeignKey('agent_config_versions.id', ondelete='CASCADE'), nullable=False)
    actual_output = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    passed = Column(Boolean, nullable=True)
    eval_method = Column(String(50), nullable=False)
    details = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    run_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    test_case = relationship("TestCase", back_populates="results")
    version = relationship("AgentConfigVersion")

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'test_case_id': self.test_case_id,
            'version_id': self.version_id,
            'actual_output': self.actual_output,
            'score': self.score,
            'passed': self.passed,
            'eval_method': self.eval_method,
            'details': self.details,
            'error': self.error,
            'run_at': self.run_at.isoformat() if self.run_at else None,
        }


class AgentTrigger(Base):
    """Autonomous trigger for an agent: cron, webhook, file_watch, or event_bus."""

    __tablename__ = 'agent_triggers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    trigger_type = Column(String(50), nullable=False, default='cron')
    agent_name = Column(String(255), nullable=False)
    project_id = Column(Integer, ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    prompt = Column(Text, nullable=False, default='')
    cron_expression = Column(String(100), nullable=True)
    webhook_path = Column(String(255), nullable=True, unique=True)
    fire_key_hash = Column(String(255), nullable=True)
    output_type = Column(String(50), nullable=False, default='memory_block')
    output_config = Column(Text, nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    last_fired_at = Column(DateTime, nullable=True)
    last_result = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    project = relationship("Project")

    def get_output_config(self) -> dict:
        try:
            return json.loads(self.output_config) if self.output_config else {}
        except json.JSONDecodeError:
            return {}

    def set_output_config(self, config: dict) -> None:
        self.output_config = json.dumps(config) if config else None

    def get_last_result(self) -> Optional[dict]:
        try:
            return json.loads(self.last_result) if self.last_result else None
        except json.JSONDecodeError:
            return None

    def set_last_result(self, result: Optional[dict]) -> None:
        self.last_result = json.dumps(result) if result else None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'trigger_type': self.trigger_type,
            'agent_name': self.agent_name,
            'project_id': self.project_id,
            'prompt': self.prompt,
            'cron_expression': self.cron_expression,
            'webhook_path': self.webhook_path,
            'output_type': self.output_type,
            'output_config': self.get_output_config(),
            'is_enabled': self.is_enabled,
            'last_fired_at': self.last_fired_at.isoformat() if self.last_fired_at else None,
            'last_result': self.get_last_result(),
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
