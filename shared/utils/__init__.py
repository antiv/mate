"""
Utils module for MATE (Multi-Agent Tree Engine).
"""

from .utils import create_model, MODEL
from .agent_manager import get_agent_manager

# Create a global agent_manager instance for direct import
agent_manager = get_agent_manager()

__all__ = ['create_model', 'MODEL', 'get_agent_manager', 'agent_manager']
