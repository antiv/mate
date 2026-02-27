# Tools Documentation

This directory contains various tool implementations for the MATE (Multi-Agent Tree Engine) system.

## Available Tools

### Image Tools (`image_tools.py`)

Provides image generation capabilities using OpenAI DALL-E 3.

**Configuration:**
To enable image generation tools for an agent, add the following to the agent's `tool_config` field in the database:

**Simple Configuration (uses default dall-e-3 model):**

```json
{
  "image_tools": true
}
```

**Advanced Configuration (specify model):**

```json
{
  "image_tools": {
    "model": "dall-e-3"
  }
}
```

**Full Configuration (specify all parameters):**

```json
{
  "image_tools": {
    "model": "dall-e-3",
    "size": "1792x1024",
    "quality": "hd",
    "n": 2
  }
}
```

**Partial Configuration (override specific parameters):**

```json
{
  "image_tools": {
    "model": "dall-e-2",
    "size": "512x512"
  }
}
```

**Supported Models:**

- `dall-e-3` (default) - High quality, returns URL
- `dall-e-2` - Standard quality, returns URL  
- `image-gen-1` - Alternative model, returns base64
- `gpt-image-1` - GPT image model, returns URL

**Configuration Parameters:**

All configuration comes from the database. The following parameters can be specified in the `tool_config`:

- `model` (string): The image generation model to use
- `size` (string): Image dimensions (e.g., "1024x1024", "1792x1024", "512x512")
- `quality` (string): Image quality - "standard" or "hd" (DALL-E 3 only)
- `n` (integer): Number of images to generate (1-10)

**Default Values:**

- `size`: "1024x1024"
- `n`: 1
- `quality`: "standard" (for dall-e-3 only)

**Example Database Configurations:**

```sql
-- Simple configuration (uses default dall-e-3)
INSERT INTO agents_config (name, type, tool_config, ...) 
VALUES ('my_image_agent', 'llm', '{"image_tools": true}', ...);

-- Model-only configuration
INSERT INTO agents_config (name, type, tool_config, ...) 
VALUES ('my_dalle2_agent', 'llm', '{"image_tools": {"model": "dall-e-2"}}', ...);

-- Full configuration with custom parameters
INSERT INTO agents_config (name, type, tool_config, ...) 
VALUES ('my_hd_agent', 'llm', '{"image_tools": {"model": "dall-e-3", "size": "1792x1024", "quality": "hd", "n": 2}}', ...);

-- Partial configuration (override specific parameters)
INSERT INTO agents_config (name, type, tool_config, ...) 
VALUES ('my_small_agent', 'llm', '{"image_tools": {"model": "dall-e-2", "size": "512x512"}}', ...);
```

**Available Functions:**

- `generate_image(prompt: str, tool_context: ToolContext = None) -> dict` (Clean API - only prompt and tool_context needed)

**Response Format:**

- **Success**: `{"prompt": "...", "success": true, "model": "...", "artifact": {...}, "url": "...", "image_access": "..."}`
- **Error**: `{"error": "...", "error_type": "...", "success": false, "prompt": "..."}`

**Response Fields:**

- `prompt`: The original prompt used
- `success`: Boolean indicating success/failure
- `model`: The model used for generation
- `artifact`: Object with filename and version (if artifact saving works)
- `url`: Direct URL to the generated image (for URL-based models like DALL-E)
- `image_access`: Human-readable message explaining how to access the image

**Note**: Base64 image data is not included in the response to avoid consuming large amounts of context. Images are either available via URL or saved as downloadable artifacts.

**Error Types:**

- `missing_dependency`: OpenAI package not installed
- `missing_api_key`: OPENAI_API_KEY not configured
- `authentication_error`: Invalid API key or auth issues
- `rate_limit_error`: API rate limit exceeded
- `invalid_request_error`: Invalid request parameters
- `model_not_found_error`: Model not available or not found
- `openrouter_error`: OpenRouter-specific errors
- `unknown_error`: Other unexpected errors

**Requirements:**

- `OPENAI_API_KEY` environment variable must be set
- `openai` package must be installed
- `requests` package for downloading generated images

### User Profile Tools (`user_profile_tools.py`)

Provides tools for agents to read and update user profile data for personalization.

**Automatic Availability:**
User profile tools are **automatically added to all agents** without any configuration needed. Every agent has access to these tools by default.

**Available Functions:**

- `get_user_profile(tool_context: ToolContext = None) -> str` - Retrieves the current user's profile data
- `update_user_profile(profile_data: str, tool_context: ToolContext = None) -> str` - Updates the current user's profile data

**Usage:**
The agent can call `get_user_profile()` to retrieve user information for personalization, and `update_user_profile(profile_data)` to save new information about the user when learned during conversation.

**Note:** These tools are automatically available to all agents. No configuration in `tool_config` is required, though you can still explicitly enable them via configuration if needed for clarity.

**Example Agent Instructions:**
To help the agent understand when to use these tools, you can add the following to the agent's `instruction` field:

```text
USER PROFILE MANAGEMENT:

You have access to the user's profile data for personalization:
- Use `get_user_profile()` to retrieve user information at the start of conversations or when you need context
- Use `update_user_profile(profile_data)` to save new information when the user shares details about themselves:
  * Personal information (name, occupation, location, education)
  * Technical skills and experience
  * Professional background
  * Preferences and interests
  * Goals and aspirations

When updating the profile, include all relevant information in a well-formatted text format.
```

### Other Tools

- **MCP Tools** (`mcp_tools.py`) - Model Context Protocol integration
- **Google Tools** (`google_tools.py`) - Google Search and Drive integration  
- **CV Tools** (`cv_tools.py`) - CV analysis and processing
- **Custom Tools** (`custom_tools.py`) - Custom function implementations

## Tool Factory

The `tool_factory.py` provides centralized tool creation based on agent configuration. It automatically creates the appropriate tools based on the `tool_config` JSON field in the agent configuration.

## Usage

Tools are automatically created and attached to agents based on their database configuration. The tool factory reads the `tool_config` field and creates the appropriate tools for each agent.
