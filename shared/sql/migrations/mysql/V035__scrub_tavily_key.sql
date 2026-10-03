-- Migration: Remove the hardcoded Tavily API key from agents_config
-- Version: V035
-- Database: MySQL
--
-- V001 used to seed the Tavily MCP server with a real API key in its URL.
-- Swap the key a row carries for the ${TAVILY_API_KEY} placeholder,
-- which the MCP loader fills from the environment at runtime.
-- The key runs from after "tavilyApiKey=" up to the closing quote of the URL.

UPDATE agents_config
SET mcp_servers_config = REPLACE(
    mcp_servers_config,
    SUBSTR(
        mcp_servers_config,
        INSTR(mcp_servers_config, 'tavilyApiKey=tvly-') + 13,
        INSTR(SUBSTR(mcp_servers_config, INSTR(mcp_servers_config, 'tavilyApiKey=tvly-') + 13), '"') - 1
    ),
    '${TAVILY_API_KEY}'
)
WHERE mcp_servers_config LIKE '%tavilyApiKey=tvly-%';
