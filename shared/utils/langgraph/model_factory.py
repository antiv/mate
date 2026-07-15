"""
Chat model factory for the LangGraph runtime.

Mirrors the provider-prefix routing of shared.utils.utils.create_model() but
returns a LangChain chat model (ChatLiteLLM), so the same model_name strings
stored in agents_config work under both runtimes.
"""

import logging
import os
from typing import Any, Dict, Optional

from langchain_litellm import ChatLiteLLM

from shared.utils.utils import _detect_provider, _is_gemini_model

logger = logging.getLogger(__name__)


def _ensure_gemini_env() -> None:
    """litellm's Gemini provider reads GEMINI_API_KEY; ADK's native Gemini reads GOOGLE_API_KEY."""
    if not os.getenv("GEMINI_API_KEY") and os.getenv("GOOGLE_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]


def create_chat_model(model_name: Optional[str] = None,
                      generate_content_config: Optional[Dict[str, Any]] = None,
                      api_key: Optional[str] = None,
                      base_url: Optional[str] = None) -> ChatLiteLLM:
    """Create a ChatLiteLLM model using the same routing rules as create_model()."""
    effective_model_name = (model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")).strip()
    # streaming=True so token deltas reach the graph's "messages" stream mode
    kwargs: Dict[str, Any] = {"request_timeout": 1200, "streaming": True}

    if _is_gemini_model(effective_model_name):
        _ensure_gemini_env()
        bare_name = effective_model_name.removeprefix("models/")
        effective_model_name = f"gemini/{bare_name}"

    else:
        provider = _detect_provider(effective_model_name)

        if provider == "openrouter":
            kwargs["api_key"] = api_key or os.getenv("OPENROUTER_API_KEY")
            kwargs["api_base"] = base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            kwargs["model_kwargs"] = {"transforms": ["middle-out"]}

        elif provider in ("ollama_chat", "ollama"):
            if provider == "ollama":
                logger.warning(
                    f"Using 'ollama/' prefix — consider 'ollama_chat/' instead to avoid "
                    "infinite tool-call loops and context issues (per ADK docs)."
                )
            if base_url:
                kwargs["api_base"] = base_url

        elif provider in ("lm_studio", "llamacpp", "llama_cpp", "localai", "llamafile"):
            model_parts = effective_model_name.split("/", 1)
            model_suffix = model_parts[1] if len(model_parts) > 1 else "default"
            effective_model_name = f"openai/{model_suffix}"

            if provider == "lm_studio":
                kwargs["api_base"] = base_url or os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
            elif provider in ("llamacpp", "llama_cpp"):
                kwargs["api_base"] = base_url or os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080/v1")
            elif provider == "localai":
                kwargs["api_base"] = base_url or os.getenv("LOCALAI_BASE_URL", "http://localhost:8080/v1")
            elif provider == "llamafile":
                kwargs["api_base"] = base_url or os.getenv("LLAMAFILE_BASE_URL", "http://localhost:8080/v1")

            kwargs["api_key"] = api_key or "local-server"

        else:
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["api_base"] = base_url

    gen_config = generate_content_config or {}
    if gen_config.get("temperature") is not None:
        kwargs["temperature"] = gen_config["temperature"]
    if gen_config.get("max_output_tokens") is not None:
        kwargs["max_tokens"] = gen_config["max_output_tokens"]
    if gen_config.get("top_p") is not None:
        kwargs["top_p"] = gen_config["top_p"]

    logger.info(f"Creating ChatLiteLLM model: {effective_model_name}")
    return ChatLiteLLM(model=effective_model_name, **kwargs)
