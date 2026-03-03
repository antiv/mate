"""
OpenTelemetry GenAI Semantic Convention attribute names.

See: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/
"""

GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"

# MATE-specific attributes
MATE_AGENT_NAME = "mate.agent.name"
MATE_USER_ID = "mate.user.id"
MATE_TOOL_NAME = "mate.tool.name"
MATE_RBAC_ALLOWED = "mate.rbac.allowed"
MATE_MEMORY_OPERATION = "mate.memory.operation"
