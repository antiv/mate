# Reddit Post for r/agentdevelopmentkit

**Title:** `MATE - Open-source Multi-Agent Tree Engine for Google ADK with dashboard, memory, MCP, and support for 50+ LLM providers`

---

Hey everyone,

I've been building **MATE** (Multi-Agent Tree Engine) - an open-source orchestration layer on top of Google ADK that adds everything you need to run multi-agent systems in production.

## What it does

- **Database-driven agent configuration** - create, modify, and organize agents from a web dashboard. No code changes needed.
- **Self-building agents** - agents can create, update, and delete other agents at runtime through conversation. Enable the `create_agent` tool on any agent and it can spin up new sub-agents, rewire hierarchies, and evolve the system on the fly. Admin-only, RBAC-protected.
- **Hierarchical agent trees** - root agents, sub-agents, sequential/parallel/loop execution patterns. Agents route to each other automatically.
- **Universal LLM support** - Gemini (native), OpenAI, Anthropic, DeepSeek, Ollama (local), OpenRouter (100+ models), and any LiteLLM-supported provider. Switch models per agent with a single config change.
- **Full MCP integration** - agents can consume MCP tools AND be exposed as MCP servers. Connect your agents to Claude Desktop, Cursor, or any MCP client.
- **Persistent memory** - dual memory system: conversation history + persistent memory blocks scoped per project. Agents remember context across sessions.
- **Web dashboard** - manage agents, users, projects, view token usage analytics, run DB migrations. Dark mode, responsive, built with TailwindCSS.
- **RBAC** - role-based access control on every agent. Control who can talk to what.
- **Multi-tenancy** - project-scoped agent hierarchies. Run multiple independent agent setups on one instance.
- **A2A protocol** - agent-to-agent communication following the standard protocol.
- **Token tracking** - monitors prompt, response, thoughts, and tool-use tokens per agent per session.
- **Docker ready** - one command to run: `docker-compose up --build`

## Self-hosted and privacy-friendly

Run entirely on your infrastructure with Ollama for local models. No data leaves your network.

## Tech stack

Python, Google ADK, LiteLLM, FastAPI, SQLAlchemy, PostgreSQL/MySQL/SQLite, TailwindCSS

## Who is this for

- Teams building multi-agent applications on Google ADK who need production infrastructure
- Developers who want a management layer instead of hardcoding agent configs
- Anyone who wants MCP-compatible agents with a web UI
- Privacy-conscious setups using Ollama for local LLM inference

## Why I built this

I found myself repeatedly solving the same problems: agent configuration management, model switching, token tracking, memory persistence, access control. MATE packages all of that into one system.

## Quick Start

```bash
git clone https://github.com/antiv/mate.git && cd mate
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit with your API key
python auth_server.py
# Open http://localhost:8000
```

Would love feedback. What features would you want to see next?

**GitHub:** https://github.com/antiv/mate

---

# Hacker News "Show HN" variant

**Title:** `Show HN: MATE - Open-source multi-agent orchestration with dashboard, memory, and 50+ LLM support`

**Body:** Use the same content above but lead with the self-hosted angle, the "no code changes" database-driven configuration, and the self-building agents feature (agents that create other agents at runtime). HN audience cares about self-hosting, practical tooling, and novel capabilities.

---

# Short version for r/LocalLLaMA

**Title:** `MATE - self-hosted multi-agent system with Ollama support, web dashboard, and persistent memory`

**Body:**

Built an open-source multi-agent orchestration engine that works with Ollama out of the box. Set `model_name` to `ollama_chat/llama3.2` (or any model) in the config and you're running agents locally.

Features: hierarchical agent trees, web dashboard for configuration, persistent memory, MCP protocol support, RBAC, token tracking, and self-building agents (agents that create/modify other agents at runtime). Supports 50+ LLM providers via LiteLLM but the Ollama integration is first-class.

No data leaves your machine. PostgreSQL/MySQL/SQLite for storage, Docker for deployment.

GitHub: https://github.com/antiv/mate

---

# Social media hashtags

`#GoogleADK #MultiAgent #MCP #Ollama #LLM #OpenSource #AI #AgentOrchestration #SelfHosted`
