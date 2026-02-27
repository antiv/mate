"""
Image generation tools for agents.

This module provides image generation capabilities using various APIs.
"""

import os
import base64
import time
import logging

from typing import Dict, Any, Optional, Tuple

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

try:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore
except Exception:
    genai = None  # type: ignore
    types = None  # type: ignore

from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)


def _get_context_values(tool_context: ToolContext) -> tuple[str, str, str]:
    """
    Extract app_name, user_id, and session_id from ToolContext.
    
    Args:
        tool_context: The tool context
        
    Returns:
        tuple[str, str, str]: (app_name, user_id, session_id)
    """
    # Get app name
    app_name = getattr(tool_context, 'app_name', None)
    if not app_name and hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
        app_name = getattr(tool_context._invocation_context, 'app_name', None)
    if not app_name:
        app_name = 'unknown'
    
    # Get user_id from invocation context (proper ADK way)
    user_id = None
    if hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
        user_id = getattr(tool_context._invocation_context, 'user_id', None)
    if not user_id:
        user_id = getattr(tool_context, 'user_id', None)
    if not user_id and hasattr(tool_context, 'session') and tool_context.session:
        user_id = getattr(tool_context.session, 'user_id', None)
    if not user_id:
        user_id = 'default'
    
    # Get session_id from invocation context (proper ADK way)
    session_id = None
    if hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
        if hasattr(tool_context._invocation_context, 'session') and tool_context._invocation_context.session:
            session_id = getattr(tool_context._invocation_context.session, 'id', None)
    if not session_id:
        session_id = getattr(tool_context, 'session_id', None)
    if not session_id:
        session_id = 'default'
    
    return app_name, user_id, session_id


def _construct_public_url(app_name: str, user_id: str, session_id: str, filename: str, saved_version: int) -> str:
    """
    Construct the public URL for an artifact based on the artifact service type.
    Only returns URLs for s3 and supabase artifact services.
    For other services (local_folder, in-memory, none), returns empty string.
    
    Args:
        app_name: The application name
        user_id: The user ID
        session_id: The session ID
        filename: The artifact filename
        saved_version: The saved version number
        
    Returns:
        str: The public URL for the artifact, or empty string if not supported
    """
    artifact_service = os.getenv("ARTIFACT_SERVICE", "none").lower()
    
    if artifact_service == "s3":
        # For S3: DISTRIBUTION_DOMAIN/{app_name}/{user_id}/{session_id}/{filename}/{saved_version}
        distribution_domain = os.getenv("DISTRIBUTION_DOMAIN", "")
        if not distribution_domain:
            logger.warning("DISTRIBUTION_DOMAIN not set for S3 artifact service")
            return ""
        # Remove trailing slash if present
        distribution_domain = distribution_domain.rstrip('/')
        # Ensure https:// prefix
        if not distribution_domain.startswith(('http://', 'https://')):
            distribution_domain = f"https://{distribution_domain}"
        return f"{distribution_domain}/{app_name}/{user_id}/{session_id}/{filename}/{saved_version}"
    elif artifact_service == "supabase":
        # For Supabase: SUPABASE_URL/storage/v1/object/public/SUPABASE_BUCKET/public/{app_name}/{user_id}/{session_id}/{filename}/{saved_version}
        supabase_url = os.getenv("SUPABASE_URL", "")
        supabase_bucket = os.getenv("SUPABASE_BUCKET", "")
        if not supabase_url or not supabase_bucket:
            logger.warning("SUPABASE_URL or SUPABASE_BUCKET not set for Supabase artifact service")
            return ""
        # Remove trailing slash if present
        supabase_url = supabase_url.rstrip('/')
        # Ensure https:// prefix
        if not supabase_url.startswith(('http://', 'https://')):
            supabase_url = f"https://{supabase_url}"
        return f"{supabase_url}/storage/v1/object/public/{supabase_bucket}/public/{app_name}/{user_id}/{session_id}/{filename}/{saved_version}"
    else:
        # For local_folder, in-memory, or none - no public URL available
        logger.debug(f"No public URL available for artifact service type: {artifact_service}")
        return ""


def validate_image_generation_setup() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate that image generation is properly configured and available.
    
    Returns:
        Tuple of (is_available, error_message, details)
        - is_available: True if image generation can work
        - error_message: Empty string if available, error description if not
        - details: Dictionary with validation details
    """
    details = {
        "openai_package": False,
        "google_generativeai_package": False,
        "api_key_configured": False,
        "api_key_source": None,
        "base_url": None,
        "models_available": [],
        "gemini_models_available": []
    }
    
    # Check if OpenAI package is installed
    if OpenAI is None:
        details["openai_package"] = False
    else:
        details["openai_package"] = True
    
    # Check if Google Generative AI package is installed
    if genai is None or types is None:
        details["google_generativeai_package"] = False
    else:
        details["google_generativeai_package"] = True
    
    # Check for API keys
    openai_api_key = os.getenv("OPENAI_API_KEY")
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    backup_api_key = os.getenv("OPENAI_API_KEY_BACKUP")
    google_api_key = os.getenv("GOOGLE_API_KEY")
    
    # Check if we have at least one API key
    has_openai_key = openai_api_key or openrouter_api_key or backup_api_key
    has_google_key = google_api_key
    
    if not has_openai_key and not has_google_key:
        return False, "No API key configured. Please set one of: OPENAI_API_KEY, OPENROUTER_API_KEY, GOOGLE_API_KEY environment variables.", details
    
    details["api_key_configured"] = True
    
    # Test OpenAI/OpenRouter connectivity if available
    if has_openai_key and details["openai_package"]:
        try:
            # Determine API key source and base URL
            if openai_api_key:
                details["api_key_source"] = "OPENAI_API_KEY"
                details["base_url"] = None
            elif openrouter_api_key:
                details["api_key_source"] = "OPENROUTER_API_KEY"
                details["base_url"] = "https://openrouter.ai/api/v1"
            elif backup_api_key:
                details["api_key_source"] = "OPENAI_API_KEY_BACKUP"
                details["base_url"] = None
            
            base_url = details["base_url"]
            api_key = openai_api_key or openrouter_api_key or backup_api_key
            client = OpenAI(api_key=api_key, base_url=base_url)
            
            # Try to list models to test connectivity (this is a lightweight request)
            models = client.models.list()
            details["models_available"] = [model.id for model in models.data if "dall" in model.id.lower() or "gpt-image" in model.id.lower()]
            
        except Exception as e:
            error_msg = str(e).lower()
            if "incorrect api key" in error_msg or "invalid api key" in error_msg:
                return False, f"Invalid API key: {details['api_key_source']}", details
            elif "rate limit" in error_msg:
                return False, f"API rate limit exceeded for {details['api_key_source']}", details
            elif "authentication" in error_msg or "unauthorized" in error_msg:
                return False, f"Authentication failed for {details['api_key_source']}", details
            else:
                return False, f"OpenAI API connectivity test failed: {str(e)}", details
    
    # Test Google Generative AI connectivity if available
    if has_google_key and details["google_generativeai_package"]:
        try:
            genai.configure(api_key=google_api_key)
            # Test with a simple model list (this is a lightweight request)
            # Note: Google's API doesn't have a direct models.list() equivalent, so we'll test with a simple generation
            client = genai.GenerativeModel("gemini-2.0-flash-exp")
            details["gemini_models_available"] = ["gemini-2.0-flash-exp"]
            details["api_key_source"] = "GOOGLE_API_KEY"
            
        except Exception as e:
            error_msg = str(e).lower()
            if "incorrect api key" in error_msg or "invalid api key" in error_msg:
                return False, f"Invalid Google API key: GOOGLE_API_KEY", details
            elif "rate limit" in error_msg:
                return False, f"Google API rate limit exceeded", details
            elif "authentication" in error_msg or "unauthorized" in error_msg:
                return False, f"Google API authentication failed", details
            else:
                return False, f"Google API connectivity test failed: {str(e)}", details
    
    # Check if we have any image generation models available
    total_models = len(details["models_available"]) + len(details["gemini_models_available"])
    if total_models == 0:
        return False, f"No image generation models found. OpenAI models: {len(details['models_available'])}, Gemini models: {len(details['gemini_models_available'])}", details
    
    return True, "", details


def get_model_config(model: str, custom_config: dict = None) -> dict:
    """
    Get model-specific configuration parameters from database configuration.
    
    Args:
        model: The model name
        custom_config: Custom configuration parameters from database
        
    Returns:
        Dictionary of model-specific parameters
    """
    # Start with basic defaults that work for all models
    config = {
        "size": "1024x1024",
        "n": 1
    }
    
    # Add model-specific defaults if needed
    if model == "dall-e-3":
        config["quality"] = "standard"
    
    # Override with database configuration if provided
    if custom_config:
        config.update(custom_config)
    
    return config


async def generate_image(prompt: str, tool_context: ToolContext = None) -> dict:
    """
    Generate image using default configuration.
    This is the main function used by agents.
    
    Args:
        prompt: The text prompt to generate the image from.
        tool_context: Optional tool context for artifact management.

    Returns:
        A dictionary containing the prompt and the base64 encoded image string,
        or an error message if the generation fails.
    """
    return await _generate_image_internal(prompt, tool_context, "dall-e-3", {})


async def _generate_image_internal(prompt: str, tool_context: ToolContext = None, model: str = "dall-e-3", model_config: dict = None) -> dict:
    """
    Internal implementation for image generation.
    
    Args:
        prompt: The text prompt to generate the image from.
        tool_context: Optional tool context for artifact management.
        model: The model to use for image generation.
        model_config: Custom configuration parameters to override defaults.

    Returns:
        A dictionary containing the prompt and the base64 encoded image string,
        or an error message if the generation fails.
    """
    try:
        # Check if OpenAI package is available
        if OpenAI is None:
            error_msg = "OpenAI package not installed. Please install it with: pip install openai"
            logger.error(error_msg)
            return {
                "error": error_msg,
                "prompt": prompt,
                "error_type": "missing_dependency",
                "success": False
            }
        
        # Get OpenAI client - try multiple API key sources
        openai_api_key = (
            os.getenv("OPENAI_API_KEY") or 
            os.getenv("OPENROUTER_API_KEY") or 
            os.getenv("OPENAI_API_KEY_BACKUP")
        )
        
        if not openai_api_key:
            error_msg = "OpenAI API key not configured. Please set one of: OPENAI_API_KEY, OPENROUTER_API_KEY environment variables."
            logger.error(error_msg)
            return {
                "error": error_msg,
                "prompt": prompt,
                "error_type": "missing_api_key",
                "success": False,
                "help": "Set OPENAI_API_KEY for OpenAI API or OPENROUTER_API_KEY for OpenRouter"
            }
        
        # Determine base URL based on which API key is used
        base_url = None
        if os.getenv("OPENROUTER_API_KEY") and not os.getenv("OPENAI_API_KEY"):
            base_url = "https://openrouter.ai/api/v1"
            logger.info("Using OpenRouter API for image generation")
        
        client = OpenAI(api_key=openai_api_key, base_url=base_url)

        # Configure model-specific parameters
        config = get_model_config(model, model_config)
        
        # Generate the image
        response = client.images.generate(
            model=model,
            prompt=prompt,
            **config
        )
        
        # Handle different response formats based on model
        image_data = response.data[0]
        image_url = getattr(image_data, 'url', None)
        image_b64 = getattr(image_data, 'b64_json', None)
        
        artifact_info = None
        try:
            if tool_context is not None:
                image_bytes = None
                
                if image_b64:
                    # Model returns base64 data directly
                    image_bytes = base64.b64decode(image_b64)
                elif image_url:
                    # Model returns URL, download the image
                    import requests
                    image_response = requests.get(image_url)
                    image_response.raise_for_status()
                    image_bytes = image_response.content
                    
                if image_bytes:
                    # Use ADK types.Part for artifact content
                    import google.genai.types as types
                    part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")

                    # Create a unique filename within the session namespace
                    timestamp = int(time.time())
                    filename = f"generated_image_{timestamp}.png"

                    # Save artifact via ToolContext (async)
                    saved_version = await tool_context.save_artifact(filename, part)
                    
                    # Construct ADK artifact path and public URL
                    artifact_path = None
                    public_url = None
                    try:
                        app_name, user_id, session_id = _get_context_values(tool_context)
                        # ADK artifact path pattern
                        artifact_path = f"/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{filename}/versions/{saved_version}"
                        # Public URL based on artifact service
                        public_url = _construct_public_url(app_name, user_id, session_id, filename, saved_version)
                        logger.debug(f"Generated ADK artifact path: {artifact_path}")
                        logger.debug(f"Generated public URL: {public_url}")
                    except Exception as url_err:
                        logger.warning(f"Could not generate artifact URLs: {url_err}")

                    # Best-effort downloadable hint for UI/super agent
                    artifact_info = {
                        "filename": filename,
                        "version": saved_version,
                        "mime_type": "image/png",
                        "artifact_path": artifact_path,
                        "public_url": public_url
                    }
        except Exception as artifact_err:
            # If artifact save fails, proceed with URL/base64 fallback
            logger.warning(f"Artifact save failed: {artifact_err}")

        result = {
            "prompt": prompt,
            "artifact": artifact_info,
            "model": model,
            "success": True
        }
        
        # Add appropriate image data based on what's available
        # NEVER include base64 data - it breaks context length
        if artifact_info and artifact_info.get("public_url"):
            result["url"] = artifact_info["public_url"]
            result["image_access"] = f"Image saved as artifact. View at: {artifact_info['public_url']}"
        elif artifact_info:
            result["image_access"] = f"Image generated and saved as artifact: {artifact_info['filename']} (version {artifact_info.get('version', 0)})"
        elif image_url:
            result["url"] = image_url
            result["image_access"] = f"View the generated image at: {image_url}"
        else:
            result["image_access"] = "Image generated successfully but could not be saved as artifact"
            
        return result

    except Exception as e:
        error_msg = f"Image generation failed: {str(e)}"
        logger.error(f"Image generation error for model {model}: {error_msg}")
        
        # Determine error type for better handling
        error_type = "unknown_error"
        error_str = str(e).lower()
        
        if "authentication" in error_str or "auth" in error_str or "unauthorized" in error_str:
            error_type = "authentication_error"
        elif "rate" in error_str or "limit" in error_str or "quota" in error_str:
            error_type = "rate_limit_error"
        elif "invalid" in error_str or "bad" in error_str or "badrequest" in error_str:
            error_type = "invalid_request_error"
        elif "not found" in error_str or "model" in error_str and "not" in error_str:
            error_type = "model_not_found_error"
        elif "openrouter" in error_str:
            error_type = "openrouter_error"
        
        return {
            "error": error_msg,
            "prompt": prompt,
            "error_type": error_type,
            "model": model,
            "success": False
        }


def create_image_tools_from_config(config: Dict[str, Any]) -> list:
    """
    Create image generation tools based on agent configuration.
    
    Args:
        config: Agent configuration dictionary
        
    Returns:
        List of image generation tools
    """
    tools = []
    
    # Check if image generation is enabled in tool_config
    tool_config = config.get('tool_config')
    if tool_config:
        try:
            import json
            tool_config_dict = json.loads(tool_config)
            
            # Check for image_tools configuration
            image_tools_config = tool_config_dict.get('image_tools')
            if image_tools_config:
                # Handle both boolean and object configurations
                if isinstance(image_tools_config, bool) and image_tools_config:
                    # Simple boolean configuration - use default model
                    tools.append(generate_image)
                    logger.info(f"Created image generation tool with default model for agent {config.get('name', 'unknown')}")
                elif isinstance(image_tools_config, dict):
                    # Object configuration - extract model and other settings
                    model = image_tools_config.get('model', 'dall-e-3')
                    model_config = {k: v for k, v in image_tools_config.items() if k != 'model'}
                    
                    # Handle special case for nano banana (Gemini 2.5 Flash Image via OpenRouter)
                    if (model == 'nano-banana' or model == 'openrouter/google/gemini-2.5-flash-image'):
                        tools.append(generate_image_nano_banana)
                        logger.info(f"Created nano banana image generation tool for agent {config.get('name', 'unknown')} with model {model}")
                    else:
                        # Create a wrapper function with the configured model and parameters
                        def create_model_specific_tool(model_name: str, config_params: dict):
                            async def model_specific_generate_image(prompt: str, tool_context: ToolContext = None) -> dict:
                                # Use the internal implementation with pre-configured parameters
                                return await _generate_image_internal(prompt, tool_context, model_name, config_params)
                            return model_specific_generate_image
                        
                        model_tool = create_model_specific_tool(model, model_config)
                        model_tool.__name__ = f"generate_image_{model.replace('-', '_')}"
                        tools.append(model_tool)
                        
                        config_str = f"model='{model}'"
                        if model_config:
                            config_str += f", config={model_config}"
                        logger.info(f"Created image generation tool with {config_str} for agent {config.get('name', 'unknown')}")
                
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in tool_config for agent {config.get('name', 'unknown')}")
    
    return tools


# Specific model wrapper functions for MCP integration
async def generate_image_gpt_image_1(prompt: str, tool_context: ToolContext = None, size: str = "1024x1024", n: int = 1) -> dict:
    """
    Generate image using GPT Image 1 model.
    
    Args:
        prompt: The text prompt to generate the image from.
        tool_context: Optional tool context for artifact management.
        size: Image size (256x256, 512x512, 1024x1024).
        n: Number of images to generate (1-10).

    Returns:
        A dictionary containing the prompt and the base64 encoded image string,
        or an error message if the generation fails.
    """
    model_config = {
        "size": size,
        "n": n
    }
    return await _generate_image_internal(prompt, tool_context, "gpt-image-1", model_config)


async def generate_image_dall_e_3(prompt: str, tool_context: ToolContext = None, size: str = "1024x1024", quality: str = "standard", n: int = 1) -> dict:
    """
    Generate image using DALL-E 3 model.
    
    Args:
        prompt: The text prompt to generate the image from.
        tool_context: Optional tool context for artifact management.
        size: Image size (1024x1024, 1024x1792, 1792x1024).
        quality: Image quality (standard, hd).
        n: Number of images to generate (1 for DALL-E 3).

    Returns:
        A dictionary containing the prompt and the base64 encoded image string,
        or an error message if the generation fails.
    """
    model_config = {
        "size": size,
        "quality": quality,
        "n": n
    }
    return await _generate_image_internal(prompt, tool_context, "dall-e-3", model_config)


async def generate_image_nano_banana(prompt: str, tool_context: ToolContext = None, asset_name: str = "generated_image", model_config: Optional[Dict[str, Any]] = None) -> dict:
    """
    Generate image using Nano Banana (Gemini 2.5 Flash Image) model.
    
    Args:
        prompt: The text prompt to generate the image from.
        tool_context: Optional tool context for artifact management.
        asset_name: Name for the asset to track versions.
        model_config: Optional configuration parameters from database.

    Returns:
        A dictionary containing the prompt and artifact information,
        or an error message if the generation fails.
    """
    try:
        # Determine model and API configuration
        # Default to OpenRouter to avoid Google API quota issues, with fallback to Google API
        model_name = "openrouter/google/gemini-2.5-flash-image"
        use_openrouter = True
        
        if model_config:
            # Check if using OpenRouter model
            config_model = model_config.get("model")
            if config_model:
                model_name = config_model
                # Check if this is an OpenRouter model
                if "openrouter" in config_model.lower() or "google/" in config_model.lower():
                    use_openrouter = True
                    model_name = config_model  # Keep full name for configuration
                    logger.info(f"Using OpenRouter API for model: {config_model}")
                else:
                    use_openrouter = False
                    model_name = config_model
                    logger.info(f"Using Google API for model: {model_name}")
        
        logger.info(f"Model configuration: {model_config}, use_openrouter: {use_openrouter}, model_name: {model_name}")
        
        # Check if Google Generative AI package is available
        if genai is None or types is None:
            error_msg = "Google Generative AI package not installed. Please install it with: pip install google-generativeai"
            logger.error(error_msg)
            return {
                "error": error_msg,
                "prompt": prompt,
                "error_type": "missing_dependency",
                "success": False,
                "help": "Run 'pip install google-generativeai' to install the required package for Gemini image generation"
            }
        
        # Get API key based on configuration
        if use_openrouter:
            # Use OpenRouter API
            openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
            if not openrouter_api_key:
                error_msg = "OpenRouter API key not configured. Please set OPENROUTER_API_KEY environment variable."
                logger.error(error_msg)
                return {
                    "error": error_msg,
                    "prompt": prompt,
                    "error_type": "missing_api_key",
                    "success": False
                }
            
            # Configure OpenRouter client
            import openai
            client = openai.OpenAI(
                api_key=openrouter_api_key,
                base_url="https://openrouter.ai/api/v1"
            )
            
            # Try to use OpenRouter with a supported image generation model
            # OpenRouter provides OpenAI-compatible completion API for image generation
            try:
                # Remove openrouter/ prefix for the actual API call
                api_model_name = model_name
                if model_name.startswith("openrouter/"):
                    api_model_name = model_name.replace("openrouter/", "")
                
                # Use chat completions API for image generation via OpenRouter
                response = client.chat.completions.create(
                    model=api_model_name,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": prompt
                                }
                            ]
                        }
                    ],
                    max_tokens=1000
                )
                
                # Handle OpenRouter response from chat completions
                # The image data is in message.images[0].image_url.url
                message = response.choices[0].message
                
                logger.info(f"Message has images: {hasattr(message, 'images')}")
                
                # Check if the response contains image data in the images field
                inline_data = None
                timestamp = int(time.time())
                base64_part = None
                if hasattr(message, 'images') and message.images:
                    logger.info(f"Found {len(message.images)} images in response")
                    
                    # Get the first image
                    first_image = message.images[0]
                    
                    # Handle both object and dictionary formats
                    image_url_data = None
                    if hasattr(first_image, 'image_url'):
                        image_url_data = first_image.image_url
                    elif isinstance(first_image, dict) and 'image_url' in first_image:
                        image_url_data = first_image['image_url']
                    
                    if image_url_data:
                        # Get the URL from either object or dictionary
                        url = None
                        if hasattr(image_url_data, 'url'):
                            url = image_url_data.url
                        elif isinstance(image_url_data, dict) and 'url' in image_url_data:
                            url = image_url_data['url']
                        
                        if url:
                            # Check if it's a base64 data URL
                            if url.startswith('data:image/'):
                                logger.info("Found base64 data URL (content not logged)")
                                try:
                                    import base64
                                    # Extract base64 part after comma
                                    base64_part = url.split(',')[1] if ',' in url else url
                                    image_bytes = base64.b64decode(base64_part)
                                    
                                    # Create inline data object
                                    class InlineData:
                                        def __init__(self, data, mime_type):
                                            self.data = data
                                            self.mime_type = mime_type
                                    
                                    # Extract mime type from data URL
                                    mime_type = url.split(';')[0].split(':')[1] if ':' in url else "image/png"
                                    inline_data = InlineData(image_bytes, mime_type)
                                    logger.info(f"Successfully extracted base64 image data, size: {len(image_bytes)} bytes")
                                except Exception as e:
                                    logger.error(f"Failed to extract base64 data: {e}")
                                    inline_data = None
                            else:
                                logger.info("URL is not a base64 data URL")
                                inline_data = None
                        else:
                            logger.info("No URL found in image_url data")
                            inline_data = None
                    else:
                        logger.info("No image_url found in image")
                        inline_data = None
                else:
                    logger.info("No images found in message")
                    inline_data = None
                
                logger.info(f"Found inline_data: {inline_data is not None}")
                
                artifact_info = None
                try:
                    if tool_context is not None and inline_data:
                        # Create a Part object from the inline data to save as artifact
                        image_part = types.Part(inline_data=inline_data)

                        # Create a unique filename within the session namespace
                        filename = f"nano_banana_image_{timestamp}.png"

                        # Save artifact via ToolContext (async)
                        saved_version = await tool_context.save_artifact(filename, image_part)

                        # Construct ADK artifact path and public URL
                        artifact_path = None
                        public_url = None
                        try:
                            app_name, user_id, session_id = _get_context_values(tool_context)
                            # ADK artifact path pattern
                            artifact_path = f"/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{filename}/versions/{saved_version}"
                            # Public URL based on artifact service
                            public_url = _construct_public_url(app_name, user_id, session_id, filename, saved_version)
                            logger.debug(f"Generated ADK artifact path: {artifact_path}")
                            logger.debug(f"Generated public URL: {public_url}")
                        except Exception as url_err:
                            logger.warning(f"Could not generate artifact URLs: {url_err}")

                        # Store artifact filename in session state for future reference
                        tool_context.state["last_generated_image"] = filename
                        tool_context.state["current_asset_name"] = asset_name

                        artifact_info = {
                            "filename": filename,
                            "version": saved_version,
                            "mime_type": "image/png",
                            "artifact_path": artifact_path,
                            "public_url": public_url
                        }
                        
                        logger.info(f"Saved generated image as artifact '{filename}' (version {saved_version})")
                        
                        return {
                            "prompt": prompt,
                            "artifact": artifact_info,
                            "model": model_name,
                            "success": True,
                            "image_access": f"Image generated successfully! Saved as artifact: {filename} (version {saved_version} of {asset_name})"
                        }
                except Exception as artifact_err:
                    logger.warning(f"Artifact save failed: {artifact_err}")
                
                # Fallback response
                result = {
                    "prompt": prompt,
                    "artifact": artifact_info,
                    "model": model_name,
                    "success": True
                }
                
                # NEVER include inline_data - it breaks context length
                if inline_data:
                    if artifact_info and artifact_info.get("public_url"):
                        result["url"] = artifact_info["public_url"]
                        result["image_access"] = f"Image saved as artifact. View at: {artifact_info['public_url']}"
                    elif artifact_info:
                        result["image_access"] = f"Image generated and saved as artifact: {artifact_info['filename']} (version {artifact_info.get('version', 0)})"
                    else:
                        result["image_access"] = "Image generated successfully but could not be saved as artifact"
                else:
                    result["error"] = "No image data received from OpenRouter"
                    result["success"] = False
                
                return result
                
            except Exception as e:
                # If OpenRouter fails, fall back to Google API
                logger.error(f"OpenRouter image generation failed: {e}")
                logger.error(f"OpenRouter error type: {type(e)}")
                logger.error(f"OpenRouter error details: {str(e)}")
                logger.warning("Falling back to Google API")
                use_openrouter = False
                model_name = "gemini-2.5-flash-image"
        
        else:
            # Use Google API directly
            google_api_key = os.getenv("GOOGLE_API_KEY")
            if not google_api_key:
                error_msg = "Google API key not configured. Please set GOOGLE_API_KEY environment variable."
                logger.error(error_msg)
                return {
                    "error": error_msg,
                    "prompt": prompt,
                    "error_type": "missing_api_key",
                    "success": False
                }
            
            # Configure the client
            client = genai.Client(api_key=google_api_key)
        
            # Create unique filename for the artifact
            timestamp = int(time.time())
            artifact_filename = f"nano_banana_image_{timestamp}.png"
            
            # Generate content stream
            for chunk in client.models.generate_content_stream(
                model=model_name,
                contents=[prompt],
            ):
                if (
                    chunk.candidates is None
                    or chunk.candidates[0].content is None
                    or chunk.candidates[0].content.parts is None
                ):
                    continue
                    
                if chunk.candidates[0].content.parts[0].inline_data and chunk.candidates[0].content.parts[0].inline_data.data:
                    inline_data = chunk.candidates[0].content.parts[0].inline_data
                    
                    # Create a Part object from the inline data to save as artifact
                    image_part = types.Part(inline_data=inline_data)
                    
                    try:
                        if tool_context is not None:
                            # Save the image as an artifact
                            version = await tool_context.save_artifact(
                                filename=artifact_filename, 
                                artifact=image_part
                            )
                            
                            # Construct ADK artifact path and public URL
                            artifact_path = None
                            public_url = None
                            try:
                                app_name, user_id, session_id = _get_context_values(tool_context)
                                # ADK artifact path pattern
                                artifact_path = f"/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{artifact_filename}/versions/{version}"
                                # Public URL based on artifact service
                                public_url = _construct_public_url(app_name, user_id, session_id, artifact_filename, version)
                                logger.debug(f"Generated ADK artifact path: {artifact_path}")
                                logger.debug(f"Generated public URL: {public_url}")
                            except Exception as url_err:
                                logger.warning(f"Could not generate artifact URLs: {url_err}")
                            
                            # Store artifact filename in session state for future reference
                            tool_context.state["last_generated_image"] = artifact_filename
                            tool_context.state["current_asset_name"] = asset_name
                            
                            logger.info(f"Saved generated image as artifact '{artifact_filename}' (version {version})")
                            
                            return {
                                "prompt": prompt,
                                "artifact": {
                                    "filename": artifact_filename,
                                    "version": version,
                                    "mime_type": "image/png",
                                    "artifact_path": artifact_path,
                                    "public_url": public_url
                                },
                                "model": model_name,
                                "success": True,
                                "image_access": f"Image generated successfully! Saved as artifact: {artifact_filename} (version {version} of {asset_name})"
                            }
                        else:
                            # If no tool context, we can't save the image
                            # NEVER return inline_data - it breaks context length
                            return {
                                "prompt": prompt,
                                "model": model_name,
                                "success": True,
                                "image_access": "Image generated successfully but could not be saved (no tool context available)"
                            }
                            
                    except Exception as e:
                        logger.error(f"Error saving artifact: {e}")
                        return {
                            "error": f"Error saving generated image as artifact: {e}",
                            "prompt": prompt,
                            "error_type": "artifact_save_error",
                            "success": False
                        }
                else:
                    # Log any text content (though this shouldn't happen for image generation)
                    if hasattr(chunk.candidates[0].content.parts[0], 'text') and chunk.candidates[0].content.parts[0].text:
                        logger.info(f"Text content from stream: {chunk.candidates[0].content.parts[0].text}")
                    
            return {
                "error": "No image was generated",
                "prompt": prompt,
                "error_type": "no_image_generated",
                "success": False
            }

    except Exception as e:
        error_msg = f"Nano Banana image generation failed: {str(e)}"
        logger.error(f"Nano Banana image generation error: {error_msg}")
        
        # Determine error type for better handling
        error_type = "unknown_error"
        error_str = str(e).lower()
        
        if "authentication" in error_str or "auth" in error_str or "unauthorized" in error_str:
            error_type = "authentication_error"
        elif "rate" in error_str or "limit" in error_str or "quota" in error_str:
            error_type = "rate_limit_error"
        elif "invalid" in error_str or "bad" in error_str or "badrequest" in error_str:
            error_type = "invalid_request_error"
        elif "not found" in error_str or "model" in error_str and "not" in error_str:
            error_type = "model_not_found_error"
        
        return {
            "error": error_msg,
            "prompt": prompt,
            "error_type": error_type,
            "model": model_name,
            "success": False
        }