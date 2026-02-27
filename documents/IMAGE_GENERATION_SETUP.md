# Image Generation Setup Guide

## Overview

The Image MCP server provides image generation capabilities using DALL-E models. You need to configure API keys to use these features.

## Required API Keys

You need **one** of the following API keys:

### Option 1: OpenAI API Key (Recommended)
- **Variable**: `OPENAI_API_KEY`
- **Source**: [OpenAI Platform](https://platform.openai.com/api-keys)
- **Cost**: Pay-per-use for DALL-E 3
- **Models**: `dall-e-3`, `gpt-image-1`

### Option 2: OpenRouter API Key (Alternative)
- **Variable**: `OPENROUTER_API_KEY` 
- **Source**: [OpenRouter](https://openrouter.ai/)
- **Cost**: Often cheaper than OpenAI direct
- **Models**: Various DALL-E models available

## Setup Instructions

### 1. Create .env File

Create a `.env` file in your project root:

```bash
# Choose ONE of these options:

# Option 1: OpenAI API Key
OPENAI_API_KEY=sk-your-openai-api-key-here

# Option 2: OpenRouter API Key  
OPENROUTER_API_KEY=sk-or-your-openrouter-api-key-here

# Optional: Backup key
OPENAI_API_KEY_BACKUP=sk-backup-key-here
```

### 2. Set Environment Variables

#### Linux/macOS:
```bash
export OPENAI_API_KEY="sk-your-openai-api-key-here"
# OR
export OPENROUTER_API_KEY="sk-or-your-openrouter-api-key-here"
```

#### Windows:
```cmd
set OPENAI_API_KEY=sk-your-openai-api-key-here
# OR
set OPENROUTER_API_KEY=sk-or-your-openrouter-api-key-here
```

### 3. Verify Setup

Test your setup:

```bash
# Test with curl
curl -X POST http://localhost:8000/images/mcp/tools/call \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic YWRtaW46dHJpYmU=" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "generate_image_dall_e_3",
      "arguments": {
        "prompt": "A simple test image",
        "size": "1024x1024"
      }
    }
  }'
```

## API Key Sources

### OpenAI API Keys
1. Go to [OpenAI Platform](https://platform.openai.com/api-keys)
2. Sign in or create account
3. Click "Create new secret key"
4. Copy the key (starts with `sk-`)
5. Set billing information

### OpenRouter API Keys
1. Go to [OpenRouter](https://openrouter.ai/)
2. Sign up for account
3. Go to API Keys section
4. Create new key
5. Add credits to account

## Troubleshooting

### Common Issues:

#### 1. "API key not configured"
```json
{
  "error": "OpenAI API key not configured. Please set one of: OPENAI_API_KEY, OPENROUTER_API_KEY environment variables."
}
```

**Solution**: Set one of the required environment variables.

#### 2. "Invalid API key"
```json
{
  "error": "Incorrect API key provided"
}
```

**Solution**: Verify your API key is correct and has sufficient credits.

#### 3. "Rate limit exceeded"
```json
{
  "error": "Rate limit reached for requests"
}
```

**Solution**: Wait or upgrade your API plan.

#### 4. "Model not found"
```json
{
  "error": "The model 'dall-e-3' does not exist"
}
```

**Solution**: Check if your API key supports the requested model.

### Debug Steps:

1. **Check environment variables**:
   ```bash
   echo $OPENAI_API_KEY
   echo $OPENROUTER_API_KEY
   ```

2. **Test API key directly**:
   ```bash
   curl -H "Authorization: Bearer $OPENAI_API_KEY" \
        https://api.openai.com/v1/models
   ```

3. **Check server logs**:
   ```bash
   python auth_server.py
   # Look for image generation logs
   ```

## Cost Information

### OpenAI DALL-E 3 Pricing:
- **1024x1024**: $0.040 per image
- **1024x1792**: $0.080 per image  
- **1792x1024**: $0.080 per image

### OpenRouter Pricing:
- Varies by model and provider
- Often 20-50% cheaper than OpenAI direct
- Check [OpenRouter pricing](https://openrouter.ai/models) for current rates

## Testing Without API Keys

If you don't want to set up image generation right now, you can:

1. **Test other MCP features**: LinkedIn MCP doesn't require external API keys
2. **Use mock responses**: The system will return helpful error messages
3. **Skip image tests**: Focus on LinkedIn MCP functionality

## Example Usage

Once configured, you can generate images:

```bash
# Generate a simple image
curl -X POST http://localhost:8000/images/mcp/tools/call \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic YWRtaW46dHJpYmU=" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "generate_image_dall_e_3",
      "arguments": {
        "prompt": "A futuristic robot reading a book in a library",
        "size": "1024x1024",
        "quality": "standard"
      }
    }
  }'
```

## Security Notes

- **Never commit API keys** to version control
- **Use .env files** and add them to .gitignore
- **Rotate keys regularly** for security
- **Monitor usage** to avoid unexpected charges
- **Use environment-specific keys** for different deployments
