-- Migration: initial_schema (consolidated final state)
-- Version: V001
-- Database: SQLITE
-- Single migration for fresh repository setup.

-- =============================================================================
-- Projects
-- =============================================================================
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- Users
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(255) NOT NULL UNIQUE,
    roles TEXT NOT NULL DEFAULT '["user"]',
    profile_data TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id);

-- =============================================================================
-- Token usage logs
-- =============================================================================
CREATE TABLE IF NOT EXISTS token_usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255),
    user_id VARCHAR(255),
    agent_name VARCHAR(255),
    model_name VARCHAR(255),
    prompt_tokens INTEGER,
    response_tokens INTEGER,
    thoughts_tokens INTEGER,
    tool_use_tokens INTEGER,
    status VARCHAR(50) DEFAULT 'SUCCESS',
    error_description TEXT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_request_id ON token_usage_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_session_id ON token_usage_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_user_id ON token_usage_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_agent_name ON token_usage_logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_model_name ON token_usage_logs(model_name);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_timestamp ON token_usage_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_status ON token_usage_logs(status);

-- =============================================================================
-- Agents config
-- =============================================================================
CREATE TABLE IF NOT EXISTS agents_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL UNIQUE,
    type VARCHAR(50) NOT NULL,
    model_name VARCHAR(255),
    description TEXT,
    instruction TEXT,
    parent_agents TEXT,
    allowed_for_roles TEXT,
    tool_config TEXT,
    max_iterations INTEGER,
    disabled BOOLEAN NOT NULL DEFAULT 0,
    mcp_servers_config TEXT,
    planner_config TEXT,
    generate_content_config TEXT,
    input_schema TEXT,
    output_schema TEXT,
    include_contents TEXT,
    hardcoded BOOLEAN NOT NULL DEFAULT 0,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agents_config_name ON agents_config(name);
CREATE INDEX IF NOT EXISTS idx_agents_config_type ON agents_config(type);
CREATE INDEX IF NOT EXISTS idx_agents_config_disabled ON agents_config(disabled);
CREATE INDEX IF NOT EXISTS idx_agents_config_project_id ON agents_config(project_id);

-- =============================================================================
-- Memory (DBMemoryService)
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR(255) NOT NULL UNIQUE,
    app_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_app_user ON memory_sessions(app_name, user_id);
CREATE INDEX IF NOT EXISTS idx_memory_sessions_session_id ON memory_sessions(session_id);

CREATE TABLE IF NOT EXISTS memory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR(255) NOT NULL REFERENCES memory_sessions(session_id) ON DELETE CASCADE,
    event_id VARCHAR(255) NOT NULL,
    content TEXT,
    author VARCHAR(255),
    timestamp DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_memory_events_session_id ON memory_events(session_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_timestamp ON memory_events(timestamp);

-- =============================================================================
-- Credentials
-- =============================================================================
CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    credential_key VARCHAR(500) NOT NULL,
    credential_data TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_unique ON credentials(app_name, user_id, credential_key);
CREATE INDEX IF NOT EXISTS idx_credentials_app_user ON credentials(app_name, user_id);
CREATE INDEX IF NOT EXISTS idx_credentials_key ON credentials(credential_key);

-- =============================================================================
-- File search (RAG)
-- =============================================================================
CREATE TABLE IF NOT EXISTS file_search_stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_name VARCHAR(500) NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    description TEXT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by_agent VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_file_search_stores_project_id ON file_search_stores(project_id);
CREATE INDEX IF NOT EXISTS idx_file_search_stores_store_name ON file_search_stores(store_name);

CREATE TABLE IF NOT EXISTS agent_file_search_stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name VARCHAR(255) NOT NULL,
    store_id INTEGER NOT NULL REFERENCES file_search_stores(id) ON DELETE CASCADE,
    is_primary BOOLEAN DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_name, store_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_file_search_stores_agent_name ON agent_file_search_stores(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_file_search_stores_store_id ON agent_file_search_stores(store_id);

CREATE TABLE IF NOT EXISTS file_search_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES file_search_stores(id) ON DELETE CASCADE,
    document_name VARCHAR(500) NOT NULL,
    display_name VARCHAR(255),
    file_path VARCHAR(1000),
    file_size INTEGER,
    mime_type VARCHAR(255),
    status VARCHAR(50) DEFAULT 'processing',
    uploaded_by_agent VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(store_id, document_name)
);
CREATE INDEX IF NOT EXISTS idx_file_search_documents_store_id ON file_search_documents(store_id);
CREATE INDEX IF NOT EXISTS idx_file_search_documents_status ON file_search_documents(status);

-- =============================================================================
-- Seed data: Chess MATE Demo Tree
-- Hierarchy: chess_mate_root (Captain) -> chess_opening_book | chess_engine_analyst | chess_historian
-- All agents are DB-configured (hardcoded = false).
-- =============================================================================
INSERT OR IGNORE INTO projects (name, description)
VALUES ('Chess MATE Demo', 'Demo project: Grandmaster MATE tree (Knowledge, Calculation, Search)');

INSERT OR REPLACE INTO agents_config (
    name, type, model_name, description, instruction,
    parent_agents, allowed_for_roles, tool_config, mcp_servers_config, disabled, hardcoded, project_id
) VALUES
    ('chess_mate_root', 'llm', 'openrouter/deepseek/deepseek-chat-v3.1', 'Chess Team Captain. Routes to opening book, engine analyst, or historian.', 'IDENTITY:
You are the Chess Team Captain. Analyze the user''s request.
Your goal is to orchestrate and delegating requests.

PROTOCOL (REQUIRED):
At the start of every session, or when you are unsure how to proceed:
1.  **Search Memory**:
    - Call `list_shared_blocks(label_search="system_instruction_shared_")` (for common rules).
    - Call `list_shared_blocks(label_search="system_instruction_<YOUR_NAME>*")` (e.g., `system_instruction_chess_mate_root`).
2.  **Load Instructions**: Read the content of every block you find.
3.  **Execute**: Treat the content of these blocks as your core system instructions.

LAZY LOADING PROTOCOL:
- IF the user asks for "visualization", "frontend data", or "smart object":
- THEN call `list_shared_blocks(label="smart_object_output_format_json")`.
- AND use that schema to format your response.

ESCALATION PROTOCOL:
- IF you cannot fulfill the request using your tools or knowledge:
- THEN call `list_shared_blocks(label="system_instruction_escalation_protocol")`.
- AND use that response as your escalation instructions

MEMORY UPDATE PROTOCOL:
1  **System Instructions (`system_instruction_*`)**:
    - If you believe a system instruction needs changing (e.g., a new routing rule), PROPOSE the change to the user.
    - ONLY update if the user explicitly confirms.',
    NULL, '["admin", "user"]', '{"memory_blocks": true, "create_agent": true}', NULL, 0, 0,
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1)),

    ('chess_opening_book', 'llm', 'openrouter/deepseek/deepseek-chat-v3.1', 'Chess Opening expert. Knowledge/context (load with RAG or instruction files).', 'IDENTITY:
You are a Chess Opening expert.

PROTOCOL (REQUIRED):
At the start of every session, or when you are unsure how to proceed:
1.  **Search Memory**: Call `list_shared_blocks(label_search="system_instruction_shared_")` and `list_shared_blocks(label_search="system_instruction_chess_opening_book")` (and optionally `system_instruction_chess_opening_book_database` for opening theory content).
2.  **Load Instructions**: Read the content of every block you find.
3.  **Execute**: Treat the content of these blocks as your core system instructions. Then explain the requested opening moves, variations, and strategic ideas clearly.',
    '["chess_mate_root"]', '["admin", "user"]', '{"memory_blocks": true}', NULL, 0, 0,
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1)),

    ('chess_engine_analyst', 'llm', 'openrouter/deepseek/deepseek-chat-v3.1', 'Calculation engine. Uses code execution / Tool Factory for best move or evaluation.', 'You are a calculation engine. Use the available tools to calculate the best move or evaluate the position.',
    '["chess_mate_root"]', '["admin", "user"]', '{"tools": ["python_code_interpreter"]}', NULL, 0, 0,
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1)),

    ('chess_historian', 'llm', 'openrouter/deepseek/deepseek-chat-v3.1', 'Researcher. Tavily search for historical games, players, tournaments.', 'IDENTITY:
You are the Chess Historian. You search for historical match results, player biographies, and tournament trivia using available search tools.

PROTOCOL (REQUIRED):
At the start of every session, or when you are unsure how to proceed:
1.  **Search Memory**: Call `list_shared_blocks(label_search="system_instruction_shared_")` and `list_shared_blocks(label_search="system_instruction_chess_historian")`.
2.  **Load Instructions**: Read the content of every block you find.
3.  **Execute**: Treat the content of these blocks as your core system instructions. Then use the available search tools to answer the user.',
    '["chess_mate_root"]', '["admin", "user"]', '{"memory_blocks": true}', '{"mcpServers": {"tavily": {"command": "npx", "args": ["-y", "mcp-remote", "https://mcp.tavily.com/mcp/?tavilyApiKey=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"], "timeout": 300}}}', 0, 0,
    (SELECT id FROM projects WHERE name = 'Chess MATE Demo' LIMIT 1));
