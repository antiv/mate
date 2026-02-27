# Database Setup Scripts

This directory contains SQL scripts for setting up the MATE database.

## Files

### `setup_database.sql` - Complete Setup (Recommended)
Complete database setup script that creates tables and populates initial agent configurations.

**Usage:**
```bash
# PostgreSQL
psql -U username -d database_name -f sql/setup_database.sql

# MySQL  
mysql -u username -p database_name < sql/setup_database.sql

# SQLite
sqlite3 database.db < sql/setup_database.sql
```

### `01_create_tables.sql` - Table Creation Only
Creates the database tables and indexes without inserting data.

### `02_agent_config_inserts.sql` - Data Population Only  
Inserts the agent configuration data. Run after creating tables.

### `agent_config_inserts.sql` - Generated from Current DB
Complete agent configuration data extracted from the current database.

## Database Schema

### `agents_config` Table
Stores agent configurations including routing, instructions, and tool settings.

```sql
CREATE TABLE public.agents_config ( 
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    type VARCHAR(50) NOT NULL,  -- 'root', 'llm', 'custom'
    model_name VARCHAR(255) NULL,
    description TEXT NULL,
    instruction TEXT NULL,
    mcp_server_url VARCHAR(500) NULL,
    mcp_auth_header VARCHAR(500) NULL,
    parent_agent VARCHAR(255) NULL,
    allowed_for_roles TEXT NULL,  -- JSON array of roles
    tool_config TEXT NULL,  -- JSON configuration for tools
    disabled BOOLEAN NOT NULL DEFAULT FALSE
);
```

### `token_usage_logs` Table
Tracks token usage across all agents for monitoring and analytics.

```sql
CREATE TABLE public.token_usage_logs ( 
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255) NULL,
    user_id VARCHAR(255) NULL,
    agent_name VARCHAR(255) NULL,
    model_name VARCHAR(255) NULL,
    prompt_tokens INTEGER NULL,
    response_tokens INTEGER NULL,
    thoughts_tokens INTEGER NULL,
    tool_use_tokens INTEGER NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

## Agent Hierarchy

The default configuration creates this agent hierarchy:

```
chess_mate_root (root)
├── chess_opening_book (llm) - Knowledge/Context
├── chess_engine_analyst (llm) - Hybrid/Computation
└── chess_historian (llm) - Search Tools
```

## Agent Types

- **root**: Main orchestrator agents that handle routing and complex workflows
- **llm**: Standard language model agents for specific tasks
- **custom**: Agents with custom implementations and tool integrations

## Quick Start

1. **Create your database** (PostgreSQL, MySQL, or SQLite)

2. **Set environment variables:**
   ```bash
   export DATABASE_URL=postgresql://user:pass@localhost:5432/mate_agents
   # OR set individual DB_* variables
   ```

3. **Run the complete setup:**
   ```bash
   psql -U username -d mate_agents -f sql/setup_database.sql
   ```

4. **Verify the setup:**
   ```sql
   SELECT name, type, parent_agent FROM public.agents_config ORDER BY type, name;
   ```

## Customization

### Adding New Agents

1. Insert into `agents_config` table:
   ```sql
   INSERT INTO public.agents_config (
       name, type, model_name, description, instruction,
       parent_agent, allowed_for_roles, disabled
   ) VALUES (
       'my_agent', 'llm', 'openrouter/model', 'My custom agent',
       'Agent instructions here', 'chess_mate_root', 
       '["admin", "user"]', false
   );
   ```

2. Restart the MATE system to load the new configuration.

### Updating Agent Instructions

```sql
UPDATE public.agents_config 
SET instruction = 'New instructions here'
WHERE name = 'agent_name';
```

### Disabling Agents

```sql
UPDATE public.agents_config 
SET disabled = true 
WHERE name = 'agent_name';
```

## Backup and Migration

### Export Configuration
```bash
# Generate current config as SQL
# Agent configurations are now managed through database migrations
# See migrations/ directory for configuration updates
```

### Import to New Database
```bash
# Create tables first
psql -U username -d new_database -f sql/01_create_tables.sql

# Import configuration
psql -U username -d new_database -f backup_config.sql
```

## Troubleshooting

### Connection Issues
- Verify DATABASE_URL or individual DB_* environment variables
- Check database server is running and accessible
- Confirm database exists and user has proper permissions

### Permission Issues
```sql
-- Grant necessary permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO username;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO username;
```

### Reset Database
```bash
# WARNING: This will delete all data
psql -U username -d database_name -c "DROP TABLE IF EXISTS public.token_usage_logs, public.agents_config CASCADE;"
psql -U username -d database_name -f sql/setup_database.sql
```
