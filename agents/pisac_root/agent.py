# @title Import necessary libraries
import os

from shared.utils.agent_manager import get_agent_manager
from shared.utils.fallback_agent import create_fallback_agent
from shared.utils.utils import create_app_with_context_caching, print_context_features_status

# Get root agent name from folder name (used for both initialization and app naming)
root_agent_name = os.path.basename(os.path.dirname(__file__))

# Try to initialize from database configuration, fallback to hardcoded agent
try:
    agent_manager = get_agent_manager()
    
    # Import hardcoded agents that should be included
    hardcoded_agents = []
    
    # Initialize with database agents + hardcoded agents
    # Note: RBAC is enforced when session_id is provided during initialization
    # For now, initialize without RBAC to maintain backward compatibility
    # RBAC will be enforced when agents are accessed with session information
    root_agent = agent_manager.initialize_agent_hierarchy(root_agent_name, hardcoded_agents)
    if root_agent is None:
        raise Exception("Database returned None for root agent")

    hardcoded_count = len(hardcoded_agents)
    total_subagents = len(root_agent.sub_agents) if hasattr(root_agent, 'sub_agents') else 0
    print(f"✅ Root agent {root_agent.name} initialized from database configuration")
    print(f"   Database subagents + {hardcoded_count} hardcoded agents = {total_subagents} total subagents")
except Exception as e:
    print(f"⚠️ Failed to initialize from database, using simple fallback agent: {e}")
    root_agent = create_fallback_agent("Creative Agent")

# Wrap root_agent in App with context caching configuration
# The AdkWebServer will detect and use this App instance
app = create_app_with_context_caching(
    root_agent=root_agent,
    app_name=root_agent_name
)

# Print context features status on startup (can be disabled via env var)
if os.getenv("PRINT_CONTEXT_FEATURES_STATUS", "true").lower() == "true":
    print_context_features_status(app, root_agent_name)

# Export both root_agent (for backward compatibility) and app (for context caching)
__all__ = ['root_agent', 'app']