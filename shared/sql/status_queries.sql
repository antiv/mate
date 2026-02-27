-- Useful queries for monitoring request status and errors
-- These queries help track successful requests, errors, and access denials

-- 1. Overview of all request statuses
SELECT 
    status,
    COUNT(*) as count,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM token_usage_logs), 2) as percentage
FROM token_usage_logs 
GROUP BY status 
ORDER BY count DESC;

-- 2. Recent access denials (last 24 hours)
SELECT 
    user_id,
    agent_name,
    model_name,
    error_description,
    timestamp
FROM token_usage_logs 
WHERE status = 'ACCESS_DENIED'
  AND timestamp >= NOW() - INTERVAL '24 hours'
ORDER BY timestamp DESC;

-- 3. Most denied agents (security monitoring)
SELECT 
    agent_name,
    COUNT(*) as denial_count,
    COUNT(DISTINCT user_id) as unique_users_denied
FROM token_usage_logs 
WHERE status = 'ACCESS_DENIED'
GROUP BY agent_name 
ORDER BY denial_count DESC;

-- 4. Users with most access denials (potential security issues)
SELECT 
    user_id,
    COUNT(*) as denial_count,
    COUNT(DISTINCT agent_name) as agents_attempted,
    MAX(timestamp) as last_denial
FROM token_usage_logs 
WHERE status = 'ACCESS_DENIED'
GROUP BY user_id 
ORDER BY denial_count DESC
LIMIT 10;

-- 5. Error rate by agent (including access denials)
SELECT 
    agent_name,
    COUNT(*) as total_requests,
    COUNT(CASE WHEN status != 'SUCCESS' THEN 1 END) as error_count,
    ROUND(COUNT(CASE WHEN status != 'SUCCESS' THEN 1 END) * 100.0 / COUNT(*), 2) as error_rate_percent
FROM token_usage_logs 
GROUP BY agent_name 
HAVING COUNT(*) > 10  -- Only agents with more than 10 requests
ORDER BY error_rate_percent DESC;

-- 6. Daily error summary
SELECT 
    DATE(timestamp) as date,
    COUNT(*) as total_requests,
    COUNT(CASE WHEN status = 'SUCCESS' THEN 1 END) as successful,
    COUNT(CASE WHEN status = 'ACCESS_DENIED' THEN 1 END) as access_denied,
    COUNT(CASE WHEN status = 'ERROR' THEN 1 END) as errors,
    COUNT(CASE WHEN status NOT IN ('SUCCESS', 'ACCESS_DENIED', 'ERROR') THEN 1 END) as other
FROM token_usage_logs 
WHERE timestamp >= CURRENT_DATE - INTERVAL '7 days'
GROUP BY DATE(timestamp) 
ORDER BY date DESC;

-- 7. Detailed error analysis
SELECT 
    status,
    error_description,
    COUNT(*) as occurrences,
    MIN(timestamp) as first_seen,
    MAX(timestamp) as last_seen
FROM token_usage_logs 
WHERE status != 'SUCCESS'
  AND error_description IS NOT NULL
GROUP BY status, error_description 
ORDER BY occurrences DESC;

-- 8. User access patterns (successful vs denied)
SELECT 
    user_id,
    COUNT(*) as total_attempts,
    COUNT(CASE WHEN status = 'SUCCESS' THEN 1 END) as successful,
    COUNT(CASE WHEN status = 'ACCESS_DENIED' THEN 1 END) as denied,
    ROUND(COUNT(CASE WHEN status = 'SUCCESS' THEN 1 END) * 100.0 / COUNT(*), 2) as success_rate
FROM token_usage_logs 
WHERE user_id IS NOT NULL
GROUP BY user_id 
HAVING COUNT(*) > 5  -- Users with more than 5 attempts
ORDER BY success_rate ASC;

-- 9. Model usage by status
SELECT 
    model_name,
    status,
    COUNT(*) as count
FROM token_usage_logs 
WHERE model_name IS NOT NULL
GROUP BY model_name, status 
ORDER BY model_name, count DESC;

-- 10. Recent errors with details (troubleshooting)
SELECT 
    request_id,
    user_id,
    agent_name,
    model_name,
    status,
    error_description,
    timestamp
FROM token_usage_logs 
WHERE status != 'SUCCESS'
  AND timestamp >= NOW() - INTERVAL '1 hour'
ORDER BY timestamp DESC;
