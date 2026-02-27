"""
Utility functions for the MATE (Multi-Agent Tree Engine) system.
"""

import os
import logging
from google.adk.models.lite_llm import LiteLlm
from google.adk.models import Gemini
from typing import Dict, Any, Optional, Any as AnyType

logger = logging.getLogger(__name__)

try:
    from google.adk.apps.app import App
    from google.adk.agents.context_cache_config import ContextCacheConfig
    CONTEXT_CACHING_AVAILABLE = True
except ImportError:
    CONTEXT_CACHING_AVAILABLE = False
    App = None
    ContextCacheConfig = None

try:
    from google.adk.apps.app import EventsCompactionConfig
    from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
    CONTEXT_COMPACTION_AVAILABLE = True
    CONTEXT_COMPACTION_ERROR = None
except ImportError as e:
    CONTEXT_COMPACTION_AVAILABLE = False
    EventsCompactionConfig = None
    LlmEventSummarizer = None
    CONTEXT_COMPACTION_ERROR = str(e)
except Exception as e:
    # Handle other exceptions (e.g., if ADK version is too old)
    CONTEXT_COMPACTION_AVAILABLE = False
    EventsCompactionConfig = None
    LlmEventSummarizer = None
    CONTEXT_COMPACTION_ERROR = f"{type(e).__name__}: {str(e)}"

# Model constants
MODEL = "gemini-2.5-flash"


def _detect_provider(model_name: str) -> str:
    """Extract the provider prefix from a model name (e.g. 'openai' from 'openai/gpt-4o')."""
    if '/' in model_name:
        return model_name.split('/')[0]
    return ''


def _is_gemini_model(model_name: str) -> bool:
    """Check if a model name should route through the native Gemini backend."""
    provider = _detect_provider(model_name)
    if not provider:
        return True
    return model_name.startswith(('gemini-', 'models/'))


def create_model(model_name: str = None, api_key: str = None, base_url: str = None):
    """
    Create model based on provider prefix auto-detection.
    
    Routing logic:
    - No prefix or 'gemini-*' / 'models/*' → native Gemini backend
    - 'openrouter/*' → LiteLlm with OpenRouter config
    - 'ollama_chat/*' / 'ollama/*' → LiteLlm pointed at local Ollama
    - 'openai/*' → LiteLlm (reads OPENAI_API_KEY)
    - 'anthropic/*' → LiteLlm (reads ANTHROPIC_API_KEY)
    - 'deepseek/*' → LiteLlm (reads DEEPSEEK_API_KEY)
    - Any other 'provider/*' → LiteLlm (provider env vars auto-read by litellm)
    
    Args:
        model_name: Model string, e.g. 'ollama_chat/llama3.2', 'openai/gpt-4o',
                    'anthropic/claude-sonnet-4-20250514', 'deepseek/deepseek-chat',
                    'openrouter/deepseek/deepseek-chat-v3.1', 'gemini-2.5-flash'.
                    Falls back to GEMINI_MODEL env var, then 'gemini-2.5-flash'.
        api_key: Optional API key override (otherwise read from provider env var)
        base_url: Optional base URL override (otherwise read from provider env var)
    
    Returns:
        Model instance (LiteLlm or Gemini)
    """
    effective_model_name = (model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")).strip()

    if _is_gemini_model(effective_model_name):
        logger.info(f"Creating Gemini model: {effective_model_name}")
        return Gemini(model=effective_model_name)

    provider = _detect_provider(effective_model_name)
    litellm_kwargs = {'timeout': 1200}

    if provider == 'openrouter':
        litellm_kwargs['api_key'] = api_key or os.getenv("OPENROUTER_API_KEY")
        litellm_kwargs['base_url'] = base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        litellm_kwargs['transforms'] = ["middle-out"]

    elif provider in ('ollama_chat', 'ollama'):
        if provider == 'ollama':
            logger.warning(
                f"Using 'ollama/' prefix — consider 'ollama_chat/' instead to avoid "
                "infinite tool-call loops and context issues (per ADK docs)."
            )
        if base_url:
            litellm_kwargs['api_base'] = base_url

    else:
        if api_key:
            litellm_kwargs['api_key'] = api_key
        if base_url:
            litellm_kwargs['base_url'] = base_url

    logger.info(f"Creating LiteLLM model: {effective_model_name} (provider: {provider})")
    return LiteLlm(effective_model_name, **litellm_kwargs)


def get_database_config() -> Dict[str, Any]:
    """
    Get database configuration from environment variables.
    
    Returns:
        Dictionary containing database configuration values
    """
    return {
        "db_type": os.getenv("DB_TYPE", "sqlite"),
        "db_path": os.getenv("DB_PATH", "my_agent_data.db"),
        "db_user": os.getenv("DB_USER", "postgres"),
        "db_password": os.getenv("DB_PASSWORD", ""),
        "db_host": os.getenv("DB_HOST", "localhost"),
        "db_port": os.getenv("DB_PORT", "5432"),
        "db_name": os.getenv("DB_NAME", "mate_agent"),
    }


def build_session_service_uri() -> str:
    """
    Build session service URI based on database configuration.
    
    Returns:
        Properly formatted database URL for the session service
    """
    config = get_database_config()
    
    db_type = config["db_type"]
    db_path = config["db_path"]
    db_user = config["db_user"]
    db_password = config["db_password"]
    db_host = config["db_host"]
    db_port = config["db_port"]
    db_name = config["db_name"]
    
    if db_type == "sqlite":
        # Use absolute path to ensure consistency regardless of working directory
        from pathlib import Path
        # Get project root (2 levels up from this file: shared/utils/utils.py -> project_root)
        project_root = Path(__file__).parent.parent.parent
        absolute_db_path = project_root / db_path
        return f"sqlite:///{absolute_db_path}"
    elif db_type == "postgresql":
        return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    elif db_type == "mysql":
        return f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    else:
        raise ValueError(f"Unsupported database type: {db_type}")


def get_adk_config() -> Dict[str, Any]:
    """
    Get ADK server configuration from environment variables.
    
    Returns:
        Dictionary containing ADK configuration values
    """
    return {
        "adk_host": os.getenv("ADK_HOST", "127.0.0.1"),
        "adk_port": int(os.getenv("ADK_PORT", "8001")),
        "session_service_uri": build_session_service_uri(),
    }


def reload_agent_cache(
    agent_name: str,
    agent_loader: Optional[AnyType] = None,
    adk_web_server: Optional[AnyType] = None
) -> Dict[str, Any]:
    """
    Reload an agent by clearing all caches, including all parent agents.
    This ensures the agent will reload with fresh configuration on next request.
    
    Args:
        agent_name: Name of the agent to reload
        agent_loader: Optional AgentLoader instance (will try to get from adk_main if not provided)
        adk_web_server: Optional AdkWebServer instance (will try to get from adk_main if not provided)
        
    Returns:
        Dict with success status, message, and reloaded agent names
    """
    from shared.utils.agent_manager import get_agent_manager
    
    try:
        agent_manager = get_agent_manager()
        candidate_names = set([agent_name])
        
        # Collect all parent agents recursively
        def collect_parents(name: str, collected: set):
            """Recursively collect all parent agents."""
            try:
                parents = agent_manager.get_parent_agents(name)
                for parent in parents:
                    if parent not in collected:
                        collected.add(parent)
                        collect_parents(parent, collected)  # Recursively get grandparents, etc.
            except Exception as e:
                logger.warning(f"Error collecting parents for {name}: {e}")
        
        # Collect all parent agents
        collect_parents(agent_name, candidate_names)
        
        if len(candidate_names) > 1:
            logger.info(f"Reloading {agent_name} and {len(candidate_names) - 1} parent agent(s): {sorted(candidate_names - {agent_name})}")
        
        # Scan AgentManager cache for all agents (original + parents) to find additional cache keys
        try:
            for key, agent in list(agent_manager.initialized_agents.items()):
                if key in candidate_names:
                    continue
                try:
                    a_name = getattr(agent, "name", None)
                except Exception:
                    a_name = None
                try:
                    origin_app = getattr(agent, "_adk_origin_app_name", None)
                except Exception:
                    origin_app = None
                # Check if this agent matches any of the agents we want to reload
                if a_name in candidate_names or origin_app in candidate_names:
                    candidate_names.add(key)
        except Exception as e:
            logger.warning(f"Error scanning AgentManager cache: {e}")
        
        # Try to get agent_loader from adk_main if not provided
        if agent_loader is None:
            try:
                import sys
                adk_main = sys.modules.get('adk_main')
                if adk_main and hasattr(adk_main, 'agent_loader'):
                    agent_loader = adk_main.agent_loader
            except Exception:
                pass
        
        # Also scan AgentLoader cache to collect keys that should be cleared
        if agent_loader:
            try:
                if hasattr(agent_loader, "_agent_cache"):
                    for key, obj in list(agent_loader._agent_cache.items()):
                        if key in candidate_names:
                            continue
                        try:
                            obj_name = getattr(obj, "name", None)
                            if obj_name in candidate_names:
                                candidate_names.add(key)
                                continue
                        except Exception:
                            pass
                        try:
                            origin_app = getattr(obj, "_adk_origin_app_name", None)
                            if origin_app in candidate_names:
                                candidate_names.add(key)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Error scanning AgentLoader cache: {e}")
        
        # Clear AgentManager cache for all candidates
        for name in list(candidate_names):
            if name in agent_manager.initialized_agents:
                del agent_manager.initialized_agents[name]
                logger.debug(f"Cleared AgentManager cache for '{name}'")
        
        # Clear AgentLoader cache for all candidates
        if agent_loader:
            for name in list(candidate_names):
                try:
                    agent_loader.remove_agent_from_cache(name)
                    logger.debug(f"Cleared AgentLoader cache for '{name}'")
                except Exception as e:
                    logger.debug(f"Could not clear AgentLoader cache for '{name}': {e}")
        
        # Try to get adk_web_server from adk_main if not provided
        if adk_web_server is None:
            try:
                import sys
                adk_main = sys.modules.get('adk_main')
                if adk_main and hasattr(adk_main, 'adk_web_server'):
                    adk_web_server = adk_main.adk_web_server
            except Exception:
                pass
        
        # Clear runner cache for all candidates
        if adk_web_server:
            any_runner = False
            for name in list(candidate_names):
                if hasattr(adk_web_server, 'runner_dict') and name in adk_web_server.runner_dict:
                    if hasattr(adk_web_server, 'runners_to_clean'):
                        adk_web_server.runners_to_clean.add(name)
                        any_runner = True
                        logger.debug(f"Marked '{name}' runner for cleanup")
            if not any_runner and len(candidate_names) > 0:
                logger.debug(f"None of {sorted(candidate_names)} found in runner_dict")
        
        agents_to_reload = sorted(candidate_names)
        if len(agents_to_reload) > 1:
            message = f"Agent '{agent_name}' and {len(agents_to_reload) - 1} parent agent(s) cache cleared (agents: {', '.join(agents_to_reload)}). They will reload on next request."
            logger.info(f"Agent '{agent_name}' and {len(agents_to_reload) - 1} parent(s) will reload with fresh config on next request")
        else:
            message = f"Agent '{agent_name}' cache cleared. It will reload on next request."
            logger.info(f"Agent '{agent_name}' will reload with fresh config on next request")
        
        return {
            "success": True,
            "message": message,
            "agent_name": agent_name,
            "reloaded_agents": agents_to_reload
        }
    except Exception as e:
        logger.error(f"Error reloading agent {agent_name}: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }


def create_app_with_context_caching(
    root_agent: Any,
    app_name: Optional[str] = None,
    min_tokens: Optional[int] = None,
    ttl_seconds: Optional[int] = None,
    cache_intervals: Optional[int] = None,
    compaction_interval: Optional[int] = None,
    compaction_overlap_size: Optional[int] = None,
    compaction_summarizer_model: Optional[str] = None,
    enable_compaction: Optional[bool] = None
) -> Any:
    """
    Create an App instance with context caching and/or compaction configuration.
    
    This wraps the root_agent in an App with context caching and/or compaction enabled.
    The AdkWebServer will detect and use this App instance if exported from agent.py.
    
    Args:
        root_agent: The root agent to wrap
        app_name: Name for the app (defaults to agent name)
        min_tokens: Minimum tokens to trigger caching (defaults to env var or 2048)
        ttl_seconds: Cache TTL in seconds (defaults to env var or 600)
        cache_intervals: Max uses before refresh (defaults to env var or 5)
        compaction_interval: Number of events before compaction triggers (defaults to env var or None/disabled)
        compaction_overlap_size: Overlap size for compaction (defaults to env var or 1)
        compaction_summarizer_model: Model name for summarizer (defaults to env var or None/uses default)
        enable_compaction: Enable compaction if available (defaults to env var CONTEXT_COMPACTION_ENABLED or False)
    
    Returns:
        App instance with context caching/compaction configured, or root_agent if unavailable
    """
    if not CONTEXT_CACHING_AVAILABLE and not CONTEXT_COMPACTION_AVAILABLE:
        logger.warning("Context caching and compaction not available (ADK version < 1.15.0/1.16.0). Returning root_agent without App wrapper.")
        return root_agent
    
    effective_app_name = app_name or getattr(root_agent, 'name', 'agent_app')
    
    # Build App constructor arguments
    app_kwargs = {
        'name': effective_app_name,
        'root_agent': root_agent,
    }
    
    # Configure context caching if available
    if CONTEXT_CACHING_AVAILABLE:
        effective_min_tokens = min_tokens or int(os.getenv("CONTEXT_CACHE_MIN_TOKENS", "2048"))
        effective_ttl = ttl_seconds or int(os.getenv("CONTEXT_CACHE_TTL_SECONDS", "600"))
        effective_intervals = cache_intervals or int(os.getenv("CONTEXT_CACHE_INTERVALS", "5"))
        
        context_cache_config = ContextCacheConfig(
            min_tokens=effective_min_tokens,
            ttl_seconds=effective_ttl,
            cache_intervals=effective_intervals,
        )
        app_kwargs['context_cache_config'] = context_cache_config
        logger.info(f"App '{effective_app_name}' context caching: "
                    f"min_tokens={effective_min_tokens}, ttl={effective_ttl}s, intervals={effective_intervals}")
    
    # Configure context compaction if available
    # Try module-level import first, fallback to runtime import if needed
    compaction_available = CONTEXT_COMPACTION_AVAILABLE
    EventsCompactionConfig_runtime = EventsCompactionConfig
    LlmEventSummarizer_runtime = LlmEventSummarizer
    
    if not compaction_available:
        # Try runtime import as fallback (in case module-level import failed due to timing)
        runtime_check = check_compaction_availability_runtime()
        if runtime_check['available']:
            compaction_available = True
            EventsCompactionConfig_runtime = runtime_check['EventsCompactionConfig']
            LlmEventSummarizer_runtime = runtime_check['LlmEventSummarizer']
            logger.info("Context compaction available via runtime import (module-level import may have failed)")
    
    if compaction_available:
        # Check if compaction should be enabled
        if enable_compaction is None:
            enable_compaction = os.getenv("CONTEXT_COMPACTION_ENABLED", "false").lower() == "true"
        
        if enable_compaction:
            effective_interval = compaction_interval or int(os.getenv("CONTEXT_COMPACTION_INTERVAL", "3"))
            effective_overlap = compaction_overlap_size or int(os.getenv("CONTEXT_COMPACTION_OVERLAP_SIZE", "1"))
            effective_summarizer_model = compaction_summarizer_model or os.getenv("CONTEXT_COMPACTION_SUMMARIZER_MODEL")
            
            compaction_config_kwargs = {
                'compaction_interval': effective_interval,
                'overlap_size': effective_overlap,
            }
            
            # Create custom summarizer if model specified
            if effective_summarizer_model:
                try:
                    summarizer_llm = create_model(effective_summarizer_model)
                    summarizer = LlmEventSummarizer_runtime(llm=summarizer_llm)
                    compaction_config_kwargs['summarizer'] = summarizer
                    logger.info(f"Using custom summarizer model: {effective_summarizer_model}")
                except Exception as e:
                    logger.warning(f"Failed to create custom summarizer with model {effective_summarizer_model}: {e}")
                    logger.info("Using default summarizer")
            
            events_compaction_config = EventsCompactionConfig_runtime(**compaction_config_kwargs)
            app_kwargs['events_compaction_config'] = events_compaction_config
            logger.info(f"App '{effective_app_name}' context compaction: "
                        f"interval={effective_interval}, overlap={effective_overlap}")
    
    # Create and return App instance
    app = App(**app_kwargs)
    
    return app


def fix_event_compaction_deserialization(event) -> None:
    """
    Workaround for ADK bug #3633: Fix EventCompaction deserialization.
    
    When using DatabaseSessionService, EventCompaction objects are incorrectly
    deserialized as dicts instead of Pydantic models. This function reconstructs
    them properly.
    
    Args:
        event: Event object to fix (modified in-place)
    """
    try:
        # Only try to import if compaction is available
        if not CONTEXT_COMPACTION_AVAILABLE:
            # Try runtime import
            runtime_check = check_compaction_availability_runtime()
            if not runtime_check['available']:
                return  # Compaction not available, nothing to fix
        
        from google.adk.events.event_actions import EventCompaction
        
        # Check if event has actions with compaction
        if hasattr(event, 'actions') and event.actions:
            if hasattr(event.actions, 'compaction') and event.actions.compaction:
                compaction = event.actions.compaction
                
                # Fix if it's a dict instead of EventCompaction object
                if isinstance(compaction, dict):
                    try:
                        event.actions.compaction = EventCompaction.model_validate(compaction)
                        logger.debug(f"Fixed EventCompaction deserialization for event {event.id}")
                    except Exception as e:
                        logger.warning(f"Failed to reconstruct EventCompaction for event {event.id}: {e}")
    except ImportError:
        # EventCompaction not available, skip
        pass
    except Exception as e:
        logger.debug(f"Error fixing EventCompaction deserialization: {e}")


def fix_session_events_compaction(session) -> None:
    """
    Workaround for ADK bug #3633: Fix EventCompaction deserialization for all events in a session.
    
    When using DatabaseSessionService, EventCompaction objects in events are incorrectly
    deserialized as dicts instead of Pydantic models. This function reconstructs them properly.
    
    This should be called whenever a session is retrieved from DatabaseSessionService
    before accessing event.actions.compaction.
    
    Args:
        session: Session object with events to fix (modified in-place)
    """
    if not hasattr(session, 'events') or not session.events:
        return
    
    for event in session.events:
        fix_event_compaction_deserialization(event)


def get_session_with_compaction_fix(session_service, session_id: str):
    """
    Get a session from session service and automatically fix EventCompaction deserialization.
    
    Workaround for ADK bug #3633. Use this instead of directly calling session_service.get_session()
    when you need to access events with compaction.
    
    Args:
        session_service: ADK session service instance
        session_id: Session ID to retrieve
    
    Returns:
        Session object with fixed EventCompaction objects
    """
    import asyncio
    
    # Get session (handle both sync and async)
    if asyncio.iscoroutinefunction(session_service.get_session):
        # Async session service
        async def _get_async():
            session = await session_service.get_session(session_id)
            if session:
                fix_session_events_compaction(session)
            return session
        return _get_async()
    else:
        # Sync session service
        session = session_service.get_session(session_id)
        if session:
            fix_session_events_compaction(session)
        return session


def check_compaction_availability_runtime() -> Dict[str, Any]:
    """
    Runtime check of compaction availability (re-checks imports).
    Useful if module-level imports failed but runtime environment is different.
    
    Returns:
        Dict with availability status and error info
    """
    result = {
        'available': False,
        'error': None,
        'EventsCompactionConfig': None,
        'LlmEventSummarizer': None
    }
    
    try:
        from google.adk.apps.app import EventsCompactionConfig
        from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
        result['available'] = True
        result['EventsCompactionConfig'] = EventsCompactionConfig
        result['LlmEventSummarizer'] = LlmEventSummarizer
    except ImportError as e:
        result['error'] = f"ImportError: {e}"
    except Exception as e:
        result['error'] = f"{type(e).__name__}: {e}"
    
    return result


def check_context_features_status(app_or_agent: Any, agent_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Check and report the status of context caching and compaction features.
    
    Args:
        app_or_agent: The App instance or root_agent to check
        agent_name: Optional agent name for reporting
    
    Returns:
        Dictionary with status information about context caching and compaction
    """
    # Re-check compaction availability at runtime if module-level check failed
    runtime_compaction_check = None
    if not CONTEXT_COMPACTION_AVAILABLE:
        runtime_compaction_check = check_compaction_availability_runtime()
    
    status = {
        'agent_name': agent_name or getattr(app_or_agent, 'name', 'unknown'),
        'is_app': False,
        'context_caching': {
            'available': CONTEXT_CACHING_AVAILABLE,
            'enabled': False,
            'config': None
        },
        'context_compaction': {
            'available': CONTEXT_COMPACTION_AVAILABLE or (runtime_compaction_check['available'] if runtime_compaction_check else False),
            'enabled': False,
            'config': None,
            'error': CONTEXT_COMPACTION_ERROR if 'CONTEXT_COMPACTION_ERROR' in globals() else None,
            'runtime_check': runtime_compaction_check
        }
    }
    
    # Check if it's an App instance
    if CONTEXT_CACHING_AVAILABLE or CONTEXT_COMPACTION_AVAILABLE:
        try:
            # Try to check if it's an App instance
            if hasattr(app_or_agent, 'root_agent'):
                status['is_app'] = True
                
                # Check context caching
                if CONTEXT_CACHING_AVAILABLE:
                    if hasattr(app_or_agent, 'context_cache_config'):
                        cache_config = app_or_agent.context_cache_config
                        if cache_config:
                            status['context_caching']['enabled'] = True
                            status['context_caching']['config'] = {
                                'min_tokens': getattr(cache_config, 'min_tokens', None),
                                'ttl_seconds': getattr(cache_config, 'ttl_seconds', None),
                                'cache_intervals': getattr(cache_config, 'cache_intervals', None),
                            }
                
                # Check context compaction (use runtime availability if module-level check failed)
                compaction_available_check = status['context_compaction']['available']
                if compaction_available_check:
                    if hasattr(app_or_agent, 'events_compaction_config'):
                        compaction_config = app_or_agent.events_compaction_config
                        if compaction_config:
                            status['context_compaction']['enabled'] = True
                            status['context_compaction']['config'] = {
                                'compaction_interval': getattr(compaction_config, 'compaction_interval', None),
                                'overlap_size': getattr(compaction_config, 'overlap_size', None),
                                'has_custom_summarizer': hasattr(compaction_config, 'summarizer') and compaction_config.summarizer is not None,
                            }
                            if hasattr(compaction_config, 'summarizer') and compaction_config.summarizer:
                                try:
                                    summarizer_llm = getattr(compaction_config.summarizer, 'llm', None)
                                    if summarizer_llm:
                                        model_name = getattr(summarizer_llm, 'model', None) or getattr(summarizer_llm, 'model_name', None)
                                        status['context_compaction']['config']['summarizer_model'] = model_name
                                except Exception:
                                    pass
            else:
                # It's likely a root_agent, not wrapped in App
                status['is_app'] = False
                status['note'] = 'Agent is not wrapped in App - context features not active'
        except Exception as e:
            status['error'] = f"Error checking status: {e}"
    
    return status


def print_context_features_status(app_or_agent: Any, agent_name: Optional[str] = None) -> None:
    """
    Print a human-readable status report of context caching and compaction.
    
    Args:
        app_or_agent: The App instance or root_agent to check
        agent_name: Optional agent name for reporting
    """
    status = check_context_features_status(app_or_agent, agent_name)
    
    print(f"\n{'='*60}")
    print(f"Context Features Status: {status['agent_name']}")
    print(f"{'='*60}")
    print(f"App Instance: {'✅ Yes' if status['is_app'] else '❌ No (not wrapped in App)'}")
    
    # Context Caching Status
    print(f"\n📦 Context Caching:")
    if not status['context_caching']['available']:
        print("   ⚠️  Not available (ADK version < 1.15.0)")
    elif status['context_caching']['enabled']:
        config = status['context_caching']['config']
        print("   ✅ Enabled")
        print(f"      • Min tokens: {config['min_tokens']}")
        print(f"      • TTL: {config['ttl_seconds']} seconds")
        print(f"      • Cache intervals: {config['cache_intervals']}")
    else:
        print("   ❌ Disabled")
    
    # Context Compaction Status
    print(f"\n🗜️  Context Compaction:")
    compaction_status = status['context_compaction']
    if not compaction_status['available']:
        error_msg = compaction_status.get('error', 'ADK version < 1.16.0')
        print(f"   ⚠️  Not available ({error_msg})")
        
        # Show runtime check if module-level check failed
        if compaction_status.get('runtime_check'):
            runtime = compaction_status['runtime_check']
            if runtime['available']:
                print(f"   ✅ Runtime check: Available (module-level check may have failed)")
            else:
                print(f"   ❌ Runtime check also failed: {runtime.get('error', 'Unknown error')}")
    elif compaction_status['enabled']:
        config = compaction_status['config']
        print("   ✅ Enabled")
        print(f"      • Compaction interval: {config['compaction_interval']} events")
        print(f"      • Overlap size: {config['overlap_size']}")
        if config.get('has_custom_summarizer'):
            model = config.get('summarizer_model', 'custom')
            print(f"      • Summarizer: Custom model ({model})")
        else:
            print(f"      • Summarizer: Default")
        # Show if runtime check was needed
        if compaction_status.get('runtime_check') and compaction_status['runtime_check']['available']:
            print("      (Confirmed via runtime check)")
    else:
        print("   ❌ Disabled")
        print("      (Set CONTEXT_COMPACTION_ENABLED=true to enable)")
        # Show if runtime check was needed
        if compaction_status.get('runtime_check') and compaction_status['runtime_check']['available']:
            print("      (Available via runtime check)")
    
    if 'note' in status:
        print(f"\n⚠️  Note: {status['note']}")
    
    if 'error' in status:
        print(f"\n❌ Error: {status['error']}")
    
    print(f"{'='*60}\n")


def reload_agent_cache(
    agent_name: str,
    agent_loader: Optional[AnyType] = None,
    adk_web_server: Optional[AnyType] = None
) -> Dict[str, Any]:
    """
    Reload an agent by clearing all caches, including all parent agents.
    This ensures the agent will reload with fresh configuration on next request.
    
    Args:
        agent_name: Name of the agent to reload
        agent_loader: Optional AgentLoader instance (will try to get from adk_main if not provided)
        adk_web_server: Optional AdkWebServer instance (will try to get from adk_main if not provided)
        
    Returns:
        Dict with success status, message, and reloaded agent names
    """
    from shared.utils.agent_manager import get_agent_manager
    
    try:
        agent_manager = get_agent_manager()
        candidate_names = set([agent_name])
        
        # Collect all parent agents recursively
        def collect_parents(name: str, collected: set):
            """Recursively collect all parent agents."""
            try:
                parents = agent_manager.get_parent_agents(name)
                for parent in parents:
                    if parent not in collected:
                        collected.add(parent)
                        collect_parents(parent, collected)  # Recursively get grandparents, etc.
            except Exception as e:
                logger.warning(f"Error collecting parents for {name}: {e}")
        
        # Collect all parent agents
        collect_parents(agent_name, candidate_names)
        
        if len(candidate_names) > 1:
            logger.info(f"Reloading {agent_name} and {len(candidate_names) - 1} parent agent(s): {sorted(candidate_names - {agent_name})}")
        
        # Scan AgentManager cache for all agents (original + parents) to find additional cache keys
        try:
            for key, agent in list(agent_manager.initialized_agents.items()):
                if key in candidate_names:
                    continue
                try:
                    a_name = getattr(agent, "name", None)
                except Exception:
                    a_name = None
                try:
                    origin_app = getattr(agent, "_adk_origin_app_name", None)
                except Exception:
                    origin_app = None
                # Check if this agent matches any of the agents we want to reload
                if a_name in candidate_names or origin_app in candidate_names:
                    candidate_names.add(key)
        except Exception as e:
            logger.warning(f"Error scanning AgentManager cache: {e}")
        
        # Try to get agent_loader from adk_main if not provided
        if agent_loader is None:
            try:
                import sys
                adk_main = sys.modules.get('adk_main')
                if adk_main and hasattr(adk_main, 'agent_loader'):
                    agent_loader = adk_main.agent_loader
            except Exception:
                pass
        
        # Also scan AgentLoader cache to collect keys that should be cleared
        if agent_loader:
            try:
                if hasattr(agent_loader, "_agent_cache"):
                    for key, obj in list(agent_loader._agent_cache.items()):
                        if key in candidate_names:
                            continue
                        try:
                            obj_name = getattr(obj, "name", None)
                            if obj_name in candidate_names:
                                candidate_names.add(key)
                                continue
                        except Exception:
                            pass
                        try:
                            origin_app = getattr(obj, "_adk_origin_app_name", None)
                            if origin_app in candidate_names:
                                candidate_names.add(key)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Error scanning AgentLoader cache: {e}")
        
        # Clear AgentManager cache for all candidates
        for name in list(candidate_names):
            if name in agent_manager.initialized_agents:
                del agent_manager.initialized_agents[name]
                logger.debug(f"Cleared AgentManager cache for '{name}'")
        
        # Clear AgentLoader cache for all candidates
        if agent_loader:
            for name in list(candidate_names):
                try:
                    agent_loader.remove_agent_from_cache(name)
                    logger.debug(f"Cleared AgentLoader cache for '{name}'")
                except Exception as e:
                    logger.debug(f"Could not clear AgentLoader cache for '{name}': {e}")
        
        # Try to get adk_web_server from adk_main if not provided
        if adk_web_server is None:
            try:
                import sys
                adk_main = sys.modules.get('adk_main')
                if adk_main and hasattr(adk_main, 'adk_web_server'):
                    adk_web_server = adk_main.adk_web_server
            except Exception:
                pass
        
        # Clear runner cache for all candidates
        if adk_web_server:
            any_runner = False
            for name in list(candidate_names):
                if hasattr(adk_web_server, 'runner_dict') and name in adk_web_server.runner_dict:
                    if hasattr(adk_web_server, 'runners_to_clean'):
                        adk_web_server.runners_to_clean.add(name)
                        any_runner = True
                        logger.debug(f"Marked '{name}' runner for cleanup")
            if not any_runner and len(candidate_names) > 0:
                logger.debug(f"None of {sorted(candidate_names)} found in runner_dict")
        
        agents_to_reload = sorted(candidate_names)
        if len(agents_to_reload) > 1:
            message = f"Agent '{agent_name}' and {len(agents_to_reload) - 1} parent agent(s) cache cleared (agents: {', '.join(agents_to_reload)}). They will reload on next request."
            logger.info(f"Agent '{agent_name}' and {len(agents_to_reload) - 1} parent(s) will reload with fresh config on next request")
        else:
            message = f"Agent '{agent_name}' cache cleared. It will reload on next request."
            logger.info(f"Agent '{agent_name}' will reload with fresh config on next request")
        
        return {
            "success": True,
            "message": message,
            "agent_name": agent_name,
            "reloaded_agents": agents_to_reload
        }
    except Exception as e:
        logger.error(f"Error reloading agent {agent_name}: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"Error: {str(e)}"
        }
