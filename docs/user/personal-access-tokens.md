---
title: Personal access tokens
summary: Create a token to call your agents from other tools, through the OpenAI-compatible API or as MCP servers, and revoke it when you are done.
audience: user
order: 115
covers:
  - server/pat_auth.py
  - shared/utils/dashboard/dashboard_server.py::*token*
---

# Personal access tokens

A personal access token lets a tool outside the dashboard act as you: a coding
assistant, a script, or an MCP client such as Claude Desktop or Cursor. It can then
talk to the agents you are allowed to use, in two ways:

- as a **model**, through MATE's OpenAI-compatible API at `https://<your MATE>/v1`;
- as an **MCP server**, at `https://<your MATE>/agents/<agent name>/mcp`.

## Who can have one

Admins, and people with a role that may use the API. By default that is the
`developer` role; your administrator can change the list with the
`ALLOWED_API_ROLES` setting. Ask an administrator to give you `developer` on the
**Users** page if **Personal Tokens** is missing from your account menu. See
[Users and roles](users-and-roles.md).

A token does not widen what you may do. Every agent still checks your roles against
its **Allowed Roles**, as in the Work Room.

## Create a token

1. Click your avatar at the top right, then **Personal Tokens**.
2. Under **Generate New Token**, enter a **Name** that says where it will be used,
   for example `VS Code Continue`.
3. Choose when it **Expires**: 7, 30, 90 or 365 days, or **Never**.
4. Click **Generate Token**, then **Copy**.

The token starts with `mate_pat_`. **It is shown only once**: MATE keeps only a hash
of it, so it cannot show it again. If you lose it, revoke it and create another.

## Use it as a model

An agent can be used this way when an admin has switched on **Expose as Model** for
it (only top-level agents can be). In your tool, add an OpenAI-compatible provider
with:

| Setting | Value |
|---|---|
| Base URL | `https://<your MATE>/v1` |
| API key | your token |
| Model | the agent's name |

The tool's model list (`GET /v1/models`) shows the agents exposed this way.

## Use it from an MCP client

An agent is an MCP server when your administrator has listed it in the
`MCP_EXPOSED_AGENTS` setting. The token is the auth key the MCP client needs:
generate one as above, then point your MCP client at
`https://<your MATE>/agents/<agent name>/mcp` and send the token as the header
`Authorization: Bearer mate_pat_...`. `https://<your MATE>/mcp`, with the same header,
lists the agents you can reach this way, with their addresses.

Without a valid token the server answers 401; with a token but without a role the
agent allows, 403.

## Revoke a token

Open **Personal Tokens** again. **Active Tokens** lists your tokens, with when each
was created and when it expires; click **Revoke** on one. Tools using it lose access
at once. An expired token stops working by itself.

## Good to know

- Treat a token like a password: anyone who has it can use your agents as you, and
  their usage counts as yours on the **Usage** page and in your budgets.
- Create one token per tool, so you can revoke one without breaking the others.
- Administrators find client-by-client setup (OpenCode, Continue, Cursor) in the
  developer guide [OpenAI compatibility](../dev/openai-compatibility.md), and the
  API and MCP details in [OpenAI-compatible API and MCP](../dev/openai-api-and-mcp.md).
