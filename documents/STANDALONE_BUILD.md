# Standalone Agent Build

Build a MATE agent into a standalone, click-to-run desktop application.

## Overview

The standalone build system takes a MATE agent exported as JSON and packages
everything into a self-contained binary (Windows `.exe`, macOS `.app`, Linux binary).
The packaged application runs a minimal chat server — no dashboard, no auth, no
external dependencies at runtime (except API keys).

## Prerequisites

- Python 3.8+
- MATE project with agents configured
- PyInstaller (for binary packaging): `pip install pyinstaller`

> **Agents with MCP stdio servers** (e.g. Tavily search via `npx`) require those
> external tools to be installed on every machine that runs the binary. See
> [MCP Server Dependencies](#mcp-server-dependencies) below.

## Quick Start

### 1. Export Your Agent

From the MATE dashboard, export your agent hierarchy as JSON:
- Go to **Agents** page
- Select a project and root agent
- Click **Export** to download the JSON file

Or use the API directly:
```bash
curl -u admin:mate "http://localhost:8000/dashboard/api/agents/export?root_agent=my_agent&project_id=1" > my_agent.json
```

### 2. Prepare the Build

```bash
# From the MATE project root
source .venv/bin/activate
python build_standalone_agent.py my_agent.json
```

This creates `build/standalone/` with:
- `standalone_server.py` — minimal server
- `standalone_agent.db` — SQLite database with your agents
- `.env` — configuration (edit to add API keys)
- `MATEAgent.spec` — PyInstaller spec file
- Required source files and assets

### 3. Test Locally

```bash
cd build/standalone
# Edit .env to add your API keys (GOOGLE_API_KEY, OPENROUTER_API_KEY, etc.)
python standalone_server.py
```

The server starts on `http://localhost:8080/` and automatically opens your browser.

### 4. Build the Binary

```bash
# Option A: Let the build script run PyInstaller
python build_standalone_agent.py my_agent.json --build

# Option B: Run PyInstaller manually
cd build/standalone
pyinstaller MATEAgent.spec
```

The binary appears in `build/standalone/dist/`.

## Build Options

| Option | Description | Default |
|--------|-------------|---------|
| `json_file` | Path to MATE agent JSON export | *(required)* |
| `--agent-name` | Root agent name override | *(auto-detected)* |
| `--output-dir` | Output directory | `build/standalone` |
| `--build` | Run PyInstaller after preparation | `false` |
| `--app-name` | Name for the executable | `MATEAgent` |

## Platform-Specific Build Commands

> **Note**: PyInstaller does **not** support cross-compilation. You must build on
> each target platform natively.

### macOS

```bash
source .venv/bin/activate
python build_standalone_agent.py my_agent.json --build --app-name MyAgent
# Output: build/standalone/dist/MyAgent.app
```

### Windows

```cmd
.venv\Scripts\activate
python build_standalone_agent.py my_agent.json --build --app-name MyAgent
REM Output: build\standalone\dist\MyAgent.exe
```

### Linux

```bash
source .venv/bin/activate
python build_standalone_agent.py my_agent.json --build --app-name MyAgent
# Output: build/standalone/dist/MyAgent
```

## Runtime Configuration

The standalone binary requires API keys to communicate with LLM providers.
Set them in a `.env` file placed next to the executable:

```env
# Required — set the key(s) your agent's model needs:
GOOGLE_API_KEY=your-gemini-key          # For Gemini models
OPENROUTER_API_KEY=your-openrouter-key  # For OpenRouter models
OPENAI_API_KEY=your-openai-key          # For OpenAI models

# Optional
STANDALONE_PORT=8080
STANDALONE_HOST=127.0.0.1
```

## MCP Server Dependencies

MCP tools come in two flavours:

| Type | How it works | Bundleable? |
|------|-------------|-------------|
| **stdio** (e.g. `npx`, `uvx`) | Spawns an external subprocess | ❌ No — must be installed on user's machine |
| **SSE / HTTP** (e.g. `transport: sse`) | Connects to a remote URL | ✅ Yes — no local subprocess needed |

**Common stdio commands and what to install:**

| Command | Install |
|---------|---------|
| `npx` | [Node.js](https://nodejs.org/) (includes npx) |
| `node` | [Node.js](https://nodejs.org/) |
| `uvx` | [uv](https://github.com/astral-sh/uv) |
| `bun` | [Bun](https://bun.sh/) |
| `deno` | [Deno](https://deno.land/) |

### Build-time report

When you run `build_standalone_agent.py`, Step 5b now scans all agents and prints which
commands are available on your build machine:

```
🔌 Step 5b: Scanning MCP server dependencies...
   CMD          SERVER               AGENT                     STATUS
   ------------ -------------------- ------------------------- ------
   npx          tavily               chess_historian           ✅ /usr/local/bin/npx
   npc          test                 chess_historian           ❌ NOT FOUND
```

### Runtime report

When the binary starts, it also checks which MCP commands are available **on the user's machine**:

```
🚀 MATE Standalone Server
   Agent: chess_mate_root
   🔌 MCP 'npx': ✅ /usr/local/bin/npx
   🔌 MCP 'npc': ❌ NOT FOUND — Ensure 'npc' is installed and in PATH
```

If a command is missing, the agent will still respond — it just won't have that MCP tool available.


## Architecture

The standalone build uses a simplified architecture compared to the full MATE system:

```
┌──────────────────────────────────────────┐
│         Standalone Binary                │
│                                          │
│  ┌──────────────┐  ┌──────────────────┐  │
│  │ Standalone   │  │ ADK Web Server   │  │
│  │ Server       │──│ (FastAPI)        │  │
│  │ (FastAPI)    │  │ /run_sse         │  │
│  │ /            │  │ /apps/...        │  │
│  └──────────────┘  └──────────────────┘  │
│         │                    │            │
│  ┌──────────────┐  ┌──────────────────┐  │
│  │ Chat UI      │  │ Agent Manager    │  │
│  │ (HTML/JS)    │  │ + SQLite DB      │  │
│  └──────────────┘  └──────────────────┘  │
└──────────────────────────────────────────┘
```

**Key differences from full MATE**:
- No auth proxy (`auth_server.py`)
- No dashboard UI
- No widget API keys
- In-memory services (session, artifact, memory, credential)
- Single embedded SQLite database
- Auto-opens browser on startup

## Troubleshooting

### "ROOT_AGENT_NAME environment variable is required"
Edit the `.env` file next to the executable and ensure `ROOT_AGENT_NAME` is set.

### "Module not found" errors during PyInstaller build
Add the missing module to `hiddenimports` in the `.spec` file and rebuild.

### Binary starts but chat doesn't connect
Ensure your API keys are set correctly in the `.env` file. Check the terminal
output for error messages from the LLM provider.

### Build is very large
PyInstaller includes the entire Python environment. Use `--upx` (already enabled
in the spec) to compress, or use a virtual environment with only required packages.

### macOS Troubleshooting

If you download a `.app` bundle zip using your browser and try to open it, macOS may show a an error saying the app **'is damaged and can't be opened'**.

This is a standard macOS security feature (Gatekeeper App Translocation) for applications that aren't officially signed with an Apple Developer certificate. To fix it, you just need to remove the quarantine attribute by running this in your terminal:

```bash
xattr -cr /path/to/the/extracted.app
```
