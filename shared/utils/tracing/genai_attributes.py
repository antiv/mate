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
# Set on a gen_ai.inference span answered by the agent's fallback model;
# gen_ai.request.model is then the primary and gen_ai.response.model the fallback.
MATE_MODEL_FALLBACK = "mate.model.fallback"
# The agent config the answering agent was built from (shared/utils/agent_config_identity.py):
# its agent_config_versions id and number when a stored version holds it, and always
# a short hash of it, so two runs on different unversioned configs still differ.
MATE_AGENT_CONFIG_VERSION_ID = "mate.agent.config_version_id"
MATE_AGENT_CONFIG_VERSION = "mate.agent.config_version"
MATE_AGENT_CONFIG_HASH = "mate.agent.config_hash"
