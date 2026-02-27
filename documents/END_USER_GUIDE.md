# MATE End User Guide

This guide explains how to use MATE (Multi-Agent Tree Engine) as an end user—whether you’re chatting with agents in the dashboard, managing the system as an admin, or using MATE from Claude Desktop or Cursor.

---

## Quick Start

1. **Access the system** at `http://localhost:8000` (or the URL provided by your administrator).
2. **Log in** with the credentials given to you (default: `admin` / `mate`).
3. **Chat with agents** from the dashboard or via MCP clients (Claude Desktop, Cursor, etc.).

---

## Logging In

- Go to the MATE URL (e.g. `http://localhost:8000`).
- Enter your username and password when prompted.
- If you see an authentication error, contact your administrator for correct credentials.

Credentials can be changed via environment variables (`AUTH_USERNAME`, `AUTH_PASSWORD`). Ask your admin if you need new credentials.

---

## Chatting with Agents

### From the Dashboard

1. Go to **Agent Management** (`/dashboard/agents`).
2. Select a project.
3. Find the agent you want to chat with.
4. Click the **Chat** (or similar) action next to that agent.
5. If prompted, choose a user for the chat session (for RBAC and usage tracking).
6. Type your message and send.
7. The agent processes your request and returns a response.

The chat opens in a side panel or new tab. You can keep the conversation in one place or open it in a separate tab.

### User Selection

Some deployments ask you to choose a user before chatting. This sets:

- **Access control** – which agents you can use.
- **Usage tracking** – how your usage is recorded.

Start typing the user ID, pick it from the list, then start the chat.

---

## Using MATE via Claude Desktop or Cursor

MATE agents can be used from MCP-compatible apps such as Claude Desktop and Cursor IDE.

### Prerequisites

- Your admin must expose agents in `MCP_EXPOSED_AGENTS`.
- You need the MATE server URL and auth details (if required).

### Claude Desktop

1. Open the Claude Desktop config (e.g. `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS).
2. Add a MCP server entry for MATE. Example:

```json
{
  "mcpServers": {
    "mate-chess": {
      "url": "http://localhost:8000/agents/chess_mate_root/mcp",
      "headers": {
        "Authorization": "Basic BASE64_ENCODED_CREDENTIALS"
      }
    }
  }
}
```

3. Restart Claude Desktop.
4. Use the `call_chess_mate_root_agent` tool (or equivalent) when chatting with Claude.
5. Provide the `message` parameter with your question; optionally use `session_id` for multi-turn context.

### Cursor IDE

1. Open Cursor settings → MCP.
2. Add a new MCP server pointing to the MATE agent URL.
3. Include the required auth headers if your deployment uses them.
4. The agent tools appear in Cursor’s tool palette.

### Tool Format

Each MATE agent exposes one tool: `call_{agent_name}_agent`.

- **message** (required): Your question or request.
- **session_id** (optional): For follow-up questions in the same conversation.

---

## Dashboard Overview

### Home Dashboard (`/dashboard`)

- **Total requests** – number of agent calls.
- **Total tokens** – prompt + response tokens.
- **Active users** – distinct users in the selected period.
- **Active agents** – distinct agents called.
- **Daily usage** – requests and tokens over time.
- **Top agents** – most used agents.
- **System status** – ADK server, database, auth.

Use **Refresh** to update system status.

### Agent Management (`/dashboard/agents`)

- **View agents** – list and search agents.
- **Filter by project** – select a project first.
- **Filter by hierarchy** – show only agents under a given root.
- **Chat** – open a chat panel with a selected agent.
- **Create / Edit / Copy / Delete** – manage agents (admin).

### User Management (`/dashboard/users`)

- **List users** – see all users and their roles.
- **Create / Edit / Delete** – manage users.
- **Roles** – assign or change roles (e.g. admin, user, custom).

### Usage Analytics (`/dashboard/usage`)

- **Analytics view** – aggregated usage and top agents.
- **Logs view** – per-request logs with request IDs.
- **Filters** – time range (7/30/90 days) and page size.
- **Details** – click a request ID for token breakdown.

### Migrations (`/dashboard/migrations`)

- **Migration history** – applied migrations.
- **Run pending** – apply new migrations.
- **Re-run / delete** – for admins doing maintenance.

### API Documentation (`/dashboard/docs`)

- Links to API docs.
- Server control – start, stop, restart ADK server.
- Server status – check if the ADK server is running.

---

## Understanding Roles and Access

MATE uses role-based access control (RBAC).

- **user** – default role for new users; access to unrestricted agents.
- **admin** – administrative access.
- **Custom roles** – as configured by your organization.

Some agents are restricted by role (e.g. admin-only). If you lack the required role, you’ll see an access denied error. Contact your administrator if you think you need different permissions.

---

## Dark Mode

- Use the theme toggle (sun/moon icon) in the sidebar.
- The choice is saved in your browser.

---

## Troubleshooting

### “Access Denied” or similar

- Your role may not allow access to that agent.
- Verify you’re logged in and your session hasn’t expired.
- Contact your administrator if access should be allowed.

### Agent chat doesn’t load

- Check the **ADK Server** status on the Dashboard Home.
- If it’s down, use the server control in Dashboard Docs (or ask an admin to start it).
- Ensure the agent is not disabled.

### MCP tool not appearing in Claude / Cursor

- Confirm the agent is in `MCP_EXPOSED_AGENTS`.
- Verify the MCP server URL and auth in your client config.
- Restart the MCP client after changing config.

### Slow or failing responses

- Check **Usage Analytics** for errors or unusual load.
- Confirm API keys (e.g. OpenRouter, Google) are set correctly; this is an admin responsibility.
- If the ADK server is overloaded, an admin may need to scale or investigate.

### Can’t log in

- Ensure the username and password are correct.
- Ask your admin to confirm credentials and that your account is active.

---

## Getting Help

1. Check this guide for common tasks and errors.
2. Review **Usage Analytics** and **System Status** in the dashboard.
3. Contact your system administrator for:
   - Credentials and access
   - API and infrastructure issues
   - Agent configuration changes

For developers and deployment details, see the main [README.md](../README.md).
