# Changelog

All notable changes to MATE (Multi-Agent Tree Engine) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2025-02-28

### Added

- **Multi-Agent Tree Engine** - hierarchical agent orchestration with root, sequential, parallel, and loop agent types
- **Database-driven agent configuration** - create and manage agents via database without code changes
- **Project-scoped multi-tenancy** - isolated agent hierarchies per project
- **Universal LLM support** - Gemini (native), OpenAI, Anthropic, DeepSeek, Ollama (local), OpenRouter, and any LiteLLM-supported provider
- **MCP protocol integration** - agents can consume MCP tools and be exposed as MCP servers
- **Built-in MCP servers** - Image Generation (DALL-E 3, GPT Image 1, Gemini) and Google Drive
- **Dynamic Agent MCP servers** - expose any agent as an MCP endpoint for Claude Desktop, Cursor, etc.
- **A2A protocol support** - agent-to-agent communication via standard protocol
- **Persistent memory system** - dual memory: conversation history (DBMemoryService) + persistent memory blocks
- **Web dashboard** - full management interface with TailwindCSS, dark mode, Monaco editor
- **User management** - CRUD operations with role assignment
- **RBAC** - role-based access control on every agent
- **Token usage tracking** - monitors prompt, response, thoughts, and tool-use tokens per agent per session
- **Usage analytics** - dashboard with charts for token consumption, request patterns, cost analysis
- **Database migration system** - versioned migrations with checksums, rollback, auto-run on startup
- **Multi-database support** - PostgreSQL, MySQL, SQLite with cross-database migrations
- **Tool Factory system** - extensible tool creation for MCP, Google services, custom functions
- **Planner support** - PlanReActPlanner and BuiltInPlanner configurable per agent
- **Docker support** - Dockerfile and docker-compose for containerized deployment
- **Prometheus metrics** - HTTP metrics via `/metrics` endpoint
- **HTTP Basic Authentication** - with bearer token support
- **Agent import/export** - backup and restore agent configurations as JSON
- **Hardcoded agent integration** - mix database agents with Python-coded agents
- **Fallback mode** - graceful degradation when database is unavailable
