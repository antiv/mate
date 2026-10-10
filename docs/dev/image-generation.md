---
title: "Image generation"
summary: How the image tool picks a model, which keys it needs, and where generated images are stored.
audience: dev
order: 230
covers:
  - shared/utils/tools/image_tools.py
  - shared/utils/mcp/image_mcp_server.py
---

# Image generation

The image tool generates through LiteLLM's `aimage_generation`, so it can use any
image model LiteLLM supports. Examples are OpenAI (GPT Image, DALL-E), Azure, OpenRouter, Gemini
(Imagen and Gemini Flash Image), Vertex AI, Bedrock, Black Forest Labs (FLUX),
fal.ai, Recraft, Stability, xAI and Dashscope. The model is named the way agent models are,
`provider/model`.

## Turning it on for an agent

In the agent form, tick **Image Tools** and type a model in **Image Model**. The field
suggests common ones. This is stored in `tool_config`:

```json
{"image_tools": true}
{"image_tools": {"model": "black_forest_labs/flux-pro-1.1"}}
{"image_tools": {"model": "gpt-image-1", "size": "1536x1024", "quality": "high"}}
```

- `true`, or an empty model, uses the server default: the model set on the dashboard's
  [Settings](../user/settings.md) page (`system_settings` row `image_model`), else
  `IMAGE_MODEL`, else `dall-e-3`. It is read on every generation, so a change applies
  without a restart; `default_image_model()` returns it with its source.
- Only these other keys are passed on, as request parameters: `size`, `quality`, `n`,
  `style`, `response_format`, `aspect_ratio`, `seed`, `negative_prompt`,
  `output_format`, `background`, `output_compression`, `moderation`
  (`_ALLOWED_PARAMS`). LiteLLM drops the ones a provider does not take
  (`drop_params`). Anything else is ignored with a warning in the log.
- That is an allowlist because LiteLLM takes hundreds of keyword arguments that
  change where a call goes or what it runs: `api_base`, `mock_response` (a URL it
  returns as the image), logging callbacks and their hosts, cloud endpoints,
  `ssl_verify`. An agent can write `tool_config` through `update_agent` when a chat
  steers it, so where the request goes and with which key is set only by the
  server's environment (`AZURE_API_BASE`, `OPENAI_API_BASE`, ...).
- A bare name (`dall-e-3`, `gpt-image-1`) is an OpenAI model, with `size: 1024x1024`
  and `n: 1` as defaults (plus `quality: standard` for DALL-E 3), as before.
- `nano-banana`, the value older agent forms saved, means
  `openrouter/google/gemini-2.5-flash-image`.

The agent sees one tool. It is `generate_image` for the default model, and
otherwise `generate_image_<model>`, with every run of other characters replaced by
`_` (for example `generate_image_gpt_image_1`). Gemini 2.5 Flash Image through
OpenRouter keeps its old name, `generate_image_nano_banana`, so instructions that
name it still work. The tool takes only the prompt.

## Keys

Each provider reads its usual environment variable, as for agents:
`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `BFL_API_KEY`, `STABILITY_API_KEY`,
`XAI_API_KEY`, the AWS and Vertex credentials, and so on. Two cases differ:

- **Gemini models** (`gemini/...`) take `GEMINI_API_KEY`, else `GOOGLE_API_KEY`.
  LiteLLM's image call reads only the first, and MATE's agents use the second.
- **Bare OpenAI names without `OPENAI_API_KEY`** fall back to
  `OPENAI_API_KEY_BACKUP`. Failing that, they use OpenRouter's endpoint with
  `OPENROUTER_API_KEY`, as the tool did before LiteLLM.

`validate_image_generation_setup()` reports whether the default model has a key,
without making a request; the Settings page shows it. A wrong
key, or a model the provider does not have, shows up on the first generation as an
`error_type` of `authentication_error` or `model_not_found_error`.

## What happens to the image

1. The provider returns base64 or a URL. A URL is downloaded only over http(s),
   without following redirects, at most 25 MB, and not from a private, loopback or
   link-local address (cloud metadata, the ADK server on 127.0.0.1) unless
   `IMAGE_ALLOW_PRIVATE_NETWORK=true`, which a local image model returning
   `localhost` URLs needs. A refused URL fails the generation.
2. A PNG is marked as AI-generated (EU AI Act Art. 50(2), see
   [EU AI Act](../user/eu-ai-act.md)). Other formats are saved unmarked, with a
   warning.
3. It is saved as a session artifact named `generated_image_<timestamp>.<ext>`, with
   the MIME type and extension read from the image's first bytes (PNG, JPEG, GIF,
   WebP).
4. The tool returns the artifact's name, version and path, and a public URL when the
   artifact service has one (S3 or Supabase). The image bytes never go back to the
   model, because they would fill its context.

## MCP server

`/images/mcp` exposes three fixed tools for MCP clients:
- `generate_image_gpt_image_1`
- `generate_image_dall_e_3`
- `generate_image_nano_banana`

All three use the same path as the agent tool. Every route but
`/images/mcp/health` needs a signed-in caller (dashboard session, bearer token,
personal access token or basic auth), since the tools spend the server's provider
keys. Callers choose the prompt and a few image parameters, never the model or
other request parameters: the `model_config` argument the Nano Banana tool used to
take is ignored. The health check answers without login and says only whether the
server is up, not which model or keys are set.

## Not covered yet

- Image editing (`litellm.aimage_edit`).
- The vision tool, `image_data_extraction`, still has its own provider routing at
  the end of `image_tools.py`.
