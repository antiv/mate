# Template Library

Pre-built agent configurations for MATE. One-click import creates a project, agents, and memory blocks.

## Overview

Templates are stored as JSON files in `templates/agent_templates/`. The dashboard gallery at `/dashboard/templates` lists them with search and category filters. Import creates a new project and agent hierarchy with unique names (prefix derived from project name).

## Template JSON Schema

```json
{
  "template_meta": {
    "id": "unique-id",
    "name": "Display Name",
    "description": "Short description for the gallery",
    "category": "support|research|code|content|demo",
    "version": "1.0",
    "compatibility_tags": ["mcp", "memory_blocks"],
    "root_agent": "agent_name",
    "agent_prefix": "prefix"
  },
  "project": {
    "name": "Default Project Name",
    "description": "Project description"
  },
  "agents": [
    {
      "name": "prefix_root",
      "type": "llm",
      "model_name": "openrouter/deepseek/deepseek-chat-v3.1",
      "description": "...",
      "instruction": "...",
      "parent_agents": [],
      "allowed_for_roles": "[\"admin\", \"user\"]",
      "tool_config": "{\"memory_blocks\": true}",
      "mcp_servers_config": null,
      "disabled": false,
      "hardcoded": false
    }
  ],
  "memory_blocks": [
    {
      "label": "system_instruction_prefix_root",
      "value": "...",
      "description": "..."
    }
  ]
}
```

### Fields

- **template_meta.id**: Unique identifier (used in URLs and import). Must match filename without `.json`.
- **template_meta.agent_prefix**: String replaced with slug(project_name) in agent names and memory block labels/values. Prevents name collisions when importing the same template multiple times.
- **agents**: Same structure as `agents_config` table. `parent_agents` references agent names (will be substituted on import).
- **memory_blocks**: `label`, `value`, `description`. Labels and values are substituted for agent name references.

## Name Substitution

On import, agent names are made unique by replacing `agent_prefix` with the slugified project name:

- `agent_prefix: "support"` → `support_root` becomes `customer_support_root` when project is "Customer Support"
- `agent_prefix: "chess_"` → `chess_mate_root` becomes `my_chess_mate_root` when project is "My Chess"

Substitution is applied to: agent names, `parent_agents`, memory block labels, and memory block values.

## API Endpoints

- `GET /dashboard/api/templates?category=&search=` - List templates
- `GET /dashboard/api/templates/{id}` - Get full template JSON
- `POST /dashboard/api/templates/import` - One-click import. Body: `{"template_id": "...", "project_name?": "..."}`
- `POST /dashboard/api/templates/create-from-agents` - Create template from existing agents. Body: `{"project_id", "root_agent", "template_id", "template_name?", "description?", "category?"}`

## Creating a Template from Existing Agents

From the Agents page: select a project, filter by root agent (hierarchy), then click **Save as Template**. Fill in template ID, name, description, and category. The template is saved to `templates/agent_templates/{id}.json` and appears in the gallery.

## Adding a Template via GitHub PR

1. Create a new JSON file in `templates/agent_templates/` (e.g. `my-template.json`)
2. Follow the schema above. Use a unique `id` and consistent `agent_prefix`
3. Submit a PR. No migration or code changes needed.

## Remote Templates (Optional)

Set `TEMPLATES_REMOTE_URL` to a URL that returns a JSON array of template objects. Remote templates are merged with local ones; local templates with the same `id` take precedence.

## Built-in Templates

| Template | Category | Description |
|----------|----------|-------------|
| customer-support | support | Triage, knowledge base, escalation |
| research-assistant | research | Search, summarizer, writer (Tavily MCP) |
| code-reviewer | code | Reviewer and suggester (python_code_interpreter) |
| content-writer | content | Researcher, writer, editor (Tavily MCP) |
| chess-mate | demo | Chess opening book, engine analyst, historian |
| image-data-extractor | data | Extract structured data from images (OCR, tables, charts) |
