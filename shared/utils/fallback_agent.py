"""
Shared fallback agent factory for creating database initialization fallback agents.
"""
from google.adk.agents import Agent
from shared.utils import create_model
from shared.callbacks.token_usage_callback import capture_model_name_callback
from shared.callbacks.function_call_guardrail import combined_after_model_callback


def create_fallback_agent(agent_context_name: str = "MATE", error_message: str = None) -> Agent:
    """
    Creates a fallback agent that informs users about missing database configuration.
    
    Args:
        agent_context_name: The name of the agent context (e.g., "MATE", "Creative Agent")
        error_message: The detailed error message to show to the user.
    
    Returns:
        Agent: A configured fallback agent
    """
    instruction_text = (
        f"You are a fallback {agent_context_name}. The main {agent_context_name} system is currently unavailable due to a configuration or initialization issue.\n\n"
        "IMPORTANT: For any user query, respond with the following message:\n\n"
        "🚨 **System Configuration Required**\n\n"
        f"I apologize, but the {agent_context_name} system is currently not fully configured or failed to initialize.\n\n"
    )
    if error_message:
        instruction_text += (
            "**Detailed Error Message:**\n"
            f"```\n{error_message}\n```\n\n"
        )
    instruction_text += (
        "**What this means:**\n"
        "- Agent configurations could not be loaded properly\n"
        "- Specialized sub-agents cannot be initialized\n"
        "- Full functionality is temporarily unavailable\n\n"
        "**Next Steps:**\n"
        "Please resolve the configuration issue or contact your system administrator:\n"
        "1. Check the database configuration and agent table records\n"
        "2. Ensure all referenced sub-agents exist and are correctly linked\n"
        "3. Review the detailed error message above to correct the configuration\n\n"
        "Thank you for your patience while we resolve this configuration issue."
    )

    return Agent(
        name="mate_fallback_agent",
        model=create_model(
            model_name="openrouter/google/gemini-2.5-flash-lite",
        ),
        description="Fallback agent that informs users about missing database configuration.",
        instruction=instruction_text,
        sub_agents=[],  # No sub-agents in fallback mode
        output_key="mate_fallback_output",
        before_model_callback=capture_model_name_callback,
        after_model_callback=combined_after_model_callback,        
    )

