# Agent Configurations with Tools

This document provides database configuration examples for agents that use various tools like MCP, Google Drive, Web Search, and custom tools.

## Database Agent Configuration Structure

When storing agents in the database, tools are handled differently than hardcoded agents:

1. **MCP Tools**: Configured via `mcp_server_url` and `mcp_auth_header` fields
2. **Built-in Tools**: Require custom agent types or special handling
3. **Custom Tools**: Need to be imported and configured in the agent manager

## Configuration Examples

### 1. MCP-Based Agent (example: chess historian with MCP)

```sql
INSERT INTO agents_config (
    name, 
    type, 
    model_name,
    description, 
    instruction, 
    mcp_server_url, 
    mcp_auth_header,
    parent_agents,
    disabled
) VALUES (
    'chess_historian_mcp',
    'llm',
    'gemini-1.5-flash',
    'Chess history and game search using MCP toolset.',
    'You are the Chess Historian agent. Use MCP tools to search historical games and provide clear responses. For simple history requests respond directly; for multi-topic requests return control to the root.',
    'https://your-mcp-server.example/mcp',
    'Bearer your_mcp_token_here',
    '["chess_mate_root"]',
    false
);
```

### 2. Web Search Agent

```sql
INSERT INTO agents_config (
    name, 
    type, 
    model_name,
    description, 
    instruction, 
    parent_agent,
    disabled
) VALUES (
    'web_search_agent_db',
    'custom',  -- Custom type to handle GoogleSearchTool
    'gemini-1.5-flash',
    'Handles web searches and provides information based on search results using Google SearchTool.',
    'You are the Web Search Agent. Your task is to perform web searches and provide comprehensive, accurate information based on search results. Use the SearchTool to search the web for relevant information and synthesize the results into a clear, helpful response. RESPONSE HANDLING: • For simple search requests (like ''Search for X'', ''Find information about Y'', ''What is Z?''): Provide the search results directly and complete the conversation. DO NOT return control to the root agent. • For complex requests that involve web search PLUS other tasks: Return control to the root agent for orchestration. • When in doubt: If the request is just about web search/research, handle it completely. If it involves multiple steps or other agents, return control.',
    '["chess_mate_root"]',
    false
);
```

### 3. CV Agent with Google Drive Tools

```sql
INSERT INTO agents_config (
    name, 
    type, 
    model_name,
    description, 
    instruction, 
    parent_agent,
    disabled
) VALUES (
    'cv_agent_db',
    'custom',  -- Custom type to handle Google Drive tools
    'gemini-1.5-flash',
    'Handles CV processing and analysis from Google Drive using real Google Drive API tools.',
    'You are the CV Agent. Your task is to process and analyze CVs from Google Drive using the available tools. You can list CV files in a Google Drive folder, read CV documents by their ID or Name, search for CVs using keywords, and perform various analyses including comprehensive analysis, skills matching, and experience summary. IMPORTANT: When a user asks to get a CV for a specific person (e.g., ''Get me CV for Ivan Antonijevic''), use the find_cv_by_person_name tool first. This tool will: 1. Search for CVs containing the person''s name in the filename 2. If exactly one match is found, automatically read and return the CV content 3. If multiple matches are found, show the list and ask the user to specify which one 4. If no matches are found, show all available CVs for the user to choose from. Always provide clear, helpful responses based on the CV data retrieved and analyzed. RESPONSE HANDLING: • For simple CV requests (like ''Get CV for X'', ''Analyze CV for Y'', ''List CVs''): Provide the CV information directly and complete the conversation. DO NOT return control to the root agent. • For complex requests that involve CV PLUS other tasks: Complete the CV analysis, then return control to the root agent for orchestration.',
    'people_agent',
    false
);
```

### 4. Multi-Tool Agent (CRM + Web Search)

```sql
INSERT INTO agents_config (
    name, 
    type, 
    model_name,
    description, 
    instruction, 
    mcp_server_url, 
    mcp_auth_header,
    parent_agent,
    disabled
) VALUES (
    'research_agent',
    'custom',  -- Custom type to handle multiple tool types
    'gemini-1.5-pro',
    'Research agent that combines CRM data with web search for comprehensive market analysis.',
    'You are the Research Agent. You have access to MCP tools and web search. Combine both sources to provide comprehensive research and analysis. Always cite your sources.',
    'https://your-mcp-server.example/mcp',
    'Bearer your_mcp_token_here',
    '["chess_mate_root"]',
    false
);
```

## Required Agent Manager Updates

To support custom tool configurations from the database, you need to extend the agent manager:

### 1. Add Tool Configuration Field

```sql
-- Add a field to store tool configuration as JSON
ALTER TABLE agents_config 
ADD COLUMN tool_config TEXT;  -- JSON string for tool configuration
```

### 2. Update Agent Manager

Add these methods to `AgentManager` class:

```python
def _create_custom_tools(self, config: Dict[str, Any]) -> List[Any]:
    """Create custom tools based on agent configuration."""
    tools = []
    
    # Add MCP tools if configured
    mcp_tools = self._create_mcp_tools(config)
    tools.extend(mcp_tools)
    
    # Parse tool_config if present
    tool_config = config.get('tool_config')
    if tool_config:
        try:
            tool_config_dict = json.loads(tool_config)
            
            # Google Search Tool
            if tool_config_dict.get('google_search'):
                from google.adk.tools.google_search_tool import GoogleSearchTool
                tools.append(GoogleSearchTool())
                
            # Google Drive Tools
            if tool_config_dict.get('google_drive'):
                tools.extend(self._create_google_drive_tools())
                
            # Custom function tools
            if tool_config_dict.get('custom_functions'):
                tools.extend(self._create_custom_function_tools(tool_config_dict['custom_functions']))
                
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in tool_config for agent {config['name']}")
    
    return tools

def _create_google_drive_tools(self) -> List[Any]:
    """Create Google Drive tools."""
    try:
        from ..cv_agent.google_drive_tools import (
            list_cv_files_in_folder,
            read_google_doc,
            read_google_doc_by_name,
            search_cv_files,
            get_file_metadata,
            find_cv_by_person_name
        )
        return [
            list_cv_files_in_folder,
            read_google_doc,
            read_google_doc_by_name,
            search_cv_files,
            get_file_metadata,
            find_cv_by_person_name
        ]
    except ImportError as e:
        logger.warning(f"Google Drive tools not available: {e}")
        return []
```

### 3. Tool Configuration Examples

```sql
-- Web Search Agent with tool config
UPDATE agents_config 
SET tool_config = '{"google_search": true}' 
WHERE name = 'web_search_agent_db';

-- CV Agent with Google Drive tools
UPDATE agents_config 
SET tool_config = '{"google_drive": true}' 
WHERE name = 'cv_agent_db';

-- Multi-tool agent
UPDATE agents_config 
SET tool_config = '{"google_search": true, "google_drive": true, "custom_functions": ["custom_tool_1"]}' 
WHERE name = 'research_agent';
```

## Environment Variables Required

For different tool types, ensure these environment variables are set:

```bash
# Google Search
GOOGLE_API_KEY=your_google_api_key
GOOGLE_CSE_ID=your_custom_search_engine_id

# Google Drive
GOOGLE_DRIVE_FOLDER_ID=your_folder_id
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json

# Models
GEMINI_API_KEY=your_gemini_api_key
OPENAI_API_KEY=your_openai_api_key
```

## Usage Examples

Once configured, agents with tools work seamlessly:

```python
# Initialize agent with tools from database
agent_manager = get_agent_manager()
root_agent = agent_manager.initialize_agent_hierarchy('chess_mate_root')

# The root agent will have subagents with their configured tools
# - MCP tools for CRM agents
# - Google Search for web agents  
# - Google Drive tools for CV agents
```

This approach allows you to:
- Store agent configurations in the database
- Dynamically configure tools per agent
- Enable/disable agents without code changes
- Manage tool access and permissions centrally
