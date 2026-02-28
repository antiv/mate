#!/bin/bash
# Setup GitHub repository metadata for MATE open-source launch.
# Requires: gh CLI authenticated (brew install gh && gh auth login)

REPO="antiv/mate"

echo "Setting repository description..."
gh repo edit "$REPO" \
  --description "Production-ready multi-agent orchestration engine built on Google ADK. Database-driven agent config, 50+ LLM providers, MCP protocol, persistent memory, web dashboard, RBAC." \
  --homepage "" \
  --enable-issues \
  --enable-wiki=false

echo "Setting repository topics..."
gh repo edit "$REPO" --add-topic "multi-agent" \
  --add-topic "google-adk" \
  --add-topic "llm" \
  --add-topic "mcp" \
  --add-topic "ollama" \
  --add-topic "agent-orchestration" \
  --add-topic "fastapi" \
  --add-topic "litellm" \
  --add-topic "ai-agents" \
  --add-topic "self-hosted" \
  --add-topic "python" \
  --add-topic "dashboard"

echo "Done! Topics and description set."
echo ""
echo "Next steps:"
echo "  1. Push all changes: git push origin main"
echo "  2. Make the repo public: gh repo edit $REPO --visibility public"
echo "  3. Create a release: gh release create v1.0.0 --title 'v1.0.0' --notes 'Initial open-source release. See CHANGELOG.md for details.'"
echo "  4. Add a demo screenshot/gif to the repo and reference it in README"
