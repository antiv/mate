# File Search (RAG) Setup Guide

## How File Search Works

File Search is implemented as a **regular Python function tool** that wraps the Gemini File Search API. This allows it to work with ADK agents by being passed via `Agent.tools` instead of `generate_content_config.tools`.

## Automatic Configuration

**No manual `tool_config` needed!** When you assign File Search stores to an agent:

1. **Store Assignment** → Updates `agent_file_search_stores` table in database
2. **Auto-Configuration on Init** → When the agent initializes, `AgentManager` automatically:
   - Reads assigned stores from `agent_file_search_stores` table
   - Adds them to `tool_config` with:
     ```json
     {
       "file_search": {
         "enabled": true,
         "store_names": ["fileSearchStores/abc123", "fileSearchStores/def456"]
       }
     }
     ```
   - Creates a `query_file_search` tool that wraps Gemini File Search API
   - Adds the tool to `Agent.tools` (ADK-compliant)
3. **File Search Active** → Agent can now use the `query_file_search` tool for RAG

## How It Works Internally

The `query_file_search` tool:
- Takes a question as input
- Internally calls `client.models.generate_content()` with `GenerateContentConfig(tools=[FileSearch])`
- Returns the RAG-enhanced response
- Works seamlessly with ADK agents since it's a regular tool, not a `generate_content_config` tool

## Setup Steps

### 1. Assign File Search Stores to Agent

1. Go to the agent list in the dashboard
2. Click **"Edit"** on your agent
3. Click **"Manage Files"** in the File Search section
4. Create a new store or assign an existing store
5. Upload files to the store

### 2. Reinitialize Agent

After assigning stores, reinitialize the agent:

1. Go back to the agent list
2. Click **"Reinitialize"** button
3. The agent will automatically:
   - Read assigned stores from database
   - Add File Search to `tool_config`
   - Create the `query_file_search` tool
   - Make it available to the agent

**That's it!** No manual `tool_config` editing needed.

### 3. Configure System Instructions

To ensure the agent knows to consult file storage before responding, add the following to the agent's system instructions (in the `instruction` field of `agents_config` table):

**Full Version:**
```text
FILE STORAGE CONSULTATION PROTOCOL:

Before responding to any user query, you MUST:
1. **Check File Storage First**: If you have access to File Search stores via the `query_file_search` tool, ALWAYS consult them before providing an answer.
2. **When to Use File Storage**:
   - When the user asks about information that might be in stored documents
   - When you need specific facts, data, or details that could be in files
   - When the query relates to documents, policies, procedures, or stored knowledge
   - When you're uncertain about an answer - check files first
3. **How to Use**: Call `query_file_search(question="[user's question or relevant keywords]")` to search through your file stores
4. **Response Strategy**:
   - If file storage contains relevant information, base your answer on that information
   - If file storage doesn't contain relevant information, you may provide a general answer but mention that you checked your stored files
   - Always cite that you consulted file storage when using information from it
5. **Priority**: File storage information takes precedence over general knowledge when available

Remember: File storage is your primary source of truth. Always check it first before relying on general knowledge.
```

**Short Version:**
```text
FILE STORAGE PROTOCOL:
Before answering any question, ALWAYS use the `query_file_search` tool to check your file stores first. 
File storage is your primary source of truth - consult it before providing any answer.
Only use general knowledge if file storage doesn't contain relevant information.
```

You can add this to the agent's `instruction` field via:
- Dashboard: Edit agent → System Instructions field
- Database: Update `agents_config.instruction` column directly

After updating system instructions, **reinitialize the agent** for changes to take effect.

## Verification

After reinitialization, check the logs for:
```
✅ Auto-configured File Search for agent {agent_name} with stores: [...]
✅ Created File Search query tool for agent {agent_name} with stores: [...]
```

If you see these messages, File Search is properly configured and the agent can use it.

## Troubleshooting

### File Search not working after reinitialization?

1. **Check logs**: Look for "Auto-configured File Search" and "Created File Search query tool" messages
2. **Verify stores**: Ensure stores are actually assigned to the agent in the database
3. **Check GOOGLE_API_KEY**: File Search requires `GOOGLE_API_KEY` environment variable
4. **Check tool availability**: The agent should have a `query_file_search` tool available

### Files uploaded but not visible?

- Files are saved to Gemini File Search automatically
- Database records are created for tracking
- If files don't appear in the UI, check the database `file_search_documents` table
- Refresh the modal to reload the file list

### Manual tool_config check

If you want to verify the auto-generated `tool_config`, use:
```bash
GET /dashboard/api/agents/{agent_name}/file-search/config
```

This shows:
- Stores assigned in database
- Current `tool_config` value (auto-generated)
- Whether File Search is enabled

## API Endpoints

- `GET /dashboard/api/agents/{agent_name}/file-search/config` - Check configuration (diagnostic)
- `GET /dashboard/api/agents/{agent_name}/file-search/stores` - List assigned stores
- `GET /dashboard/api/agents/{agent_name}/file-search/files` - List files
- `POST /dashboard/api/agents/{agent_name}/file-search/stores/assign` - Assign store to agent
- `POST /dashboard/api/agents/{agent_name}/file-search/stores/unassign` - Unassign store from agent
- `POST /dashboard/api/file-search/stores/create` - Create new store
- `POST /dashboard/api/file-search/stores/upload` - Upload file to store

**Note**: No sync endpoint is needed - `tool_config` is automatically populated from the database during agent initialization.

