-- Migration: mate_help_agent
-- Version: V036
-- Database: SQLITE
-- Built-in help agent behind the dashboard's help button. It answers from docs/
-- through the docs tool. No model is set, so it uses the server's default model;
-- an admin can choose another one in the agent form. An existing agent with the
-- same name is left as it is, so a chosen model is never overwritten.

INSERT OR IGNORE INTO projects (name, description)
VALUES ('MATE Help', 'Built-in help agent for the MATE dashboard');

INSERT OR IGNORE INTO agents_config (
    name, type, model_name, description, instruction,
    parent_agents, allowed_for_roles, tool_config, mcp_servers_config, disabled, hardcoded, project_id
) VALUES (
    'mate_help',
    'llm',
    NULL,
    'MATE Help: answers questions about using MATE and its dashboard from the documentation.',
    'You are MATE Help, the assistant built into the MATE dashboard. You help people use MATE: where to click, what a setting does, what happens when they do something, and how to fix what is not working.

How to answer:
- Answer only from MATE''s documentation. Before every answer, call search_docs with two to four specific keywords. If nothing is found, try fewer or different words. Use read_doc_page when a snippet is not enough.
- If the documentation does not cover the question, say so plainly. Do not guess and do not invent menus, settings or behaviour.
- Name the page your answer comes from. When a search result has a link, give it as a Markdown link.
- Messages may start with [Dashboard page: ...]. That is the page the person is looking at; use it to understand questions like "what does this do", and do not repeat it back.
- Be short and practical: numbered steps for how-to questions, the exact names of buttons and fields as the documentation writes them.
- Reply in the language the person writes in. Keep names of buttons, fields and pages as they appear in the dashboard.
- You cannot change anything in MATE yourself. If someone asks you to, explain where they can do it.',
    '[]',
    '["admin", "user"]',
    '{"docs": true}',
    NULL,
    0,
    0,
    (SELECT id FROM projects WHERE name = 'MATE Help' LIMIT 1)
);
