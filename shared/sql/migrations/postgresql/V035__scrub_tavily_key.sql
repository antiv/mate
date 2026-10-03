-- Migration: Remove the hardcoded Tavily API key from agents_config
-- Version: V035
-- Database: PostgreSQL
--
-- V001 used to seed the Tavily MCP server with a real API key in its URL.
-- Swap whatever key a row carries for the ${TAVILY_API_KEY} placeholder,
-- which the MCP loader fills from the environment at runtime.

UPDATE agents_config
SET mcp_servers_config = regexp_replace(mcp_servers_config, 'tavilyApiKey=tvly-[A-Za-z0-9_-]+', 'tavilyApiKey=${TAVILY_API_KEY}', 'g')
WHERE mcp_servers_config LIKE '%tavilyApiKey=tvly-%';
