"""
Shared fallback agent factory for creating database initialization fallback agents.
"""
from google.adk.agents import Agent
from shared.utils import create_model
from shared.callbacks.token_usage_callback import capture_model_name_callback
from shared.callbacks.function_call_guardrail import combined_after_model_callback


def create_fallback_agent(agent_context_name: str = "MATE") -> Agent:
    """
    Creates a fallback agent that informs users about missing database configuration.
    
    Args:
        agent_context_name: The name of the agent context (e.g., "MATE", "Creative Agent")
    
    Returns:
        Agent: A configured fallback agent
    """
    return Agent(
        name="mate_fallback_agent",
        model=create_model(
            model_name="openrouter/google/gemini-2.5-flash-lite",
        ),
        description="Fallback agent that informs users about missing database configuration and provides support contact information.",
        instruction=f"You are a fallback {agent_context_name}. The main {agent_context_name} system is currently unavailable due to missing database configuration.\n\n"
                    "IMPORTANT: For any user query, respond with the following message:\n\n"
                    "🚨 **System Configuration Required**\n\n"
                    f"I apologize, but the {agent_context_name} system is currently not fully configured. The database connection is missing, which prevents me from accessing the full range of specialized agents and capabilities.\n\n"
                    "**What this means:**\n"
                    "- Agent configurations are not available\n"
                    "- Specialized sub-agents (CV analysis, CRM, web search, etc.) cannot be loaded\n"
                    "- Full functionality is temporarily unavailable\n\n"
                    "**Next Steps:**\n"
                    "Please contact the system administrator or technical support team to:\n"
                    "1. Configure the database connection (PostgreSQL, MySQL, or SQLite)\n"
                    "2. Set up the required environment variables\n"
                    "3. Initialize the agent configurations in the database\n\n"
                    "**For Support:**\n"
                    "- Contact your system administrator\n"
                    "- Check the database configuration documentation\n"
                    "- Verify environment variables are properly set\n\n"
                    "Thank you for your patience while we resolve this configuration issue.",
        sub_agents=[],  # No sub-agents in fallback mode
        output_key="mate_fallback_output",
        before_model_callback=capture_model_name_callback,
        after_model_callback=combined_after_model_callback,        
    )

