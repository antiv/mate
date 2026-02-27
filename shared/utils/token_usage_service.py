"""
Token usage service for database operations using SQLAlchemy.

This module provides business logic methods for token usage operations,
separated from the database connection logic.
"""

import logging
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from .database_client import get_database_client
from .models import TokenUsageLog

logger = logging.getLogger(__name__)


class TokenUsageService:
    """Service class for token usage operations using SQLAlchemy."""
    
    def __init__(self):
        self.db_client = get_database_client()
    
    def is_connected(self) -> bool:
        """Check if the database client is connected."""
        return self.db_client.is_connected()
    
    def _get_session(self) -> Optional[Session]:
        """Get a database session."""
        if not self.db_client.is_connected():
            logger.error("Database client not connected")
            return None
        return self.db_client.get_session()
    
    def insert_token_usage(self, token_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Insert token usage data into the database.
        
        Args:
            token_data: Dictionary containing token usage information
            
        Returns:
            Inserted record or None if failed
        """
        session = self._get_session()
        if not session:
            return None
        
        try:
            # Generate request_id if not provided
            request_id = token_data.get('request_id') or str(uuid.uuid4())
            
            # Create TokenUsageLog instance
            token_log = TokenUsageLog(
                request_id=request_id,
                session_id=token_data.get('session_id'),
                user_id=token_data.get('user_id'),
                agent_name=token_data.get('agent_name'),
                model_name=token_data.get('model_name'),
                prompt_tokens=token_data.get('prompt_tokens'),
                response_tokens=token_data.get('response_tokens'),
                thoughts_tokens=token_data.get('thoughts_tokens'),
                tool_use_tokens=token_data.get('tool_use_tokens'),
                status=token_data.get('status', 'SUCCESS'),
                error_description=token_data.get('error_description')
            )
            
            session.add(token_log)
            session.commit()
            
            result = token_log.to_dict()
            logger.info(f"Successfully inserted token usage record: {result.get('id')}")
            return result
            
        except SQLAlchemyError as e:
            logger.error(f"Failed to insert token usage record: {e}")
            session.rollback()
            return None
        finally:
            session.close()
    
    def get_token_usage_by_session(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Get all token usage records for a specific session.
        
        Args:
            session_id: The session ID to query
            
        Returns:
            List of token usage records
        """
        session = self._get_session()
        if not session:
            return []
        
        try:
            records = session.query(TokenUsageLog).filter(
                TokenUsageLog.session_id == session_id
            ).all()
            
            return [record.to_dict() for record in records]
            
        except SQLAlchemyError as e:
            logger.error(f"Failed to get token usage by session: {e}")
            return []
        finally:
            session.close()
    
    def get_token_usage_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all token usage records for a specific user.
        
        Args:
            user_id: The user ID to query
            
        Returns:
            List of token usage records
        """
        session = self._get_session()
        if not session:
            return []
        
        try:
            records = session.query(TokenUsageLog).filter(
                TokenUsageLog.user_id == user_id
            ).all()
            
            return [record.to_dict() for record in records]
            
        except SQLAlchemyError as e:
            logger.error(f"Failed to get token usage by user: {e}")
            return []
        finally:
            session.close()
    
    def get_token_usage_by_agent(self, agent_name: str) -> List[Dict[str, Any]]:
        """
        Get all token usage records for a specific agent.
        
        Args:
            agent_name: The agent name to query
            
        Returns:
            List of token usage records
        """
        session = self._get_session()
        if not session:
            return []
        
        try:
            records = session.query(TokenUsageLog).filter(
                TokenUsageLog.agent_name == agent_name
            ).all()
            
            return [record.to_dict() for record in records]
            
        except SQLAlchemyError as e:
            logger.error(f"Failed to get token usage by agent: {e}")
            return []
        finally:
            session.close()
    
    def get_token_usage_by_timeframe(self, start_time: str, end_time: str) -> List[Dict[str, Any]]:
        """
        Get token usage records within a specific timeframe.
        
        Args:
            start_time: Start time in ISO format
            end_time: End time in ISO format
            
        Returns:
            List of token usage records
        """
        session = self._get_session()
        if not session:
            return []
        
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            
            records = session.query(TokenUsageLog).filter(
                TokenUsageLog.timestamp >= start_dt,
                TokenUsageLog.timestamp <= end_dt
            ).all()
            
            return [record.to_dict() for record in records]
            
        except (SQLAlchemyError, ValueError) as e:
            logger.error(f"Failed to get token usage by timeframe: {e}")
            return []
        finally:
            session.close()
    
    def get_aggregated_token_usage(self, session_id: str) -> Dict[str, Any]:
        """
        Get aggregated token usage for a session.
        
        Args:
            session_id: The session ID to aggregate
            
        Returns:
            Dictionary with aggregated token usage
        """
        records = self.get_token_usage_by_session(session_id)
        
        if not records:
            return {
                "total_prompt_tokens": 0,
                "total_response_tokens": 0,
                "total_thoughts_tokens": 0,
                "total_tool_use_tokens": 0,
                "total_tokens": 0,
                "call_count": 0,
                "agents": {}
            }
        
        total_prompt = sum(record.get("prompt_tokens", 0) or 0 for record in records)
        total_response = sum(record.get("response_tokens", 0) or 0 for record in records)
        total_thoughts = sum(record.get("thoughts_tokens", 0) or 0 for record in records)
        total_tool_use = sum(record.get("tool_use_tokens", 0) or 0 for record in records)
        
        # Group by agent
        agents = {}
        for record in records:
            agent_name = record.get("agent_name", "unknown")
            if agent_name not in agents:
                agents[agent_name] = {
                    "call_count": 0,
                    "prompt_tokens": 0,
                    "response_tokens": 0,
                    "thoughts_tokens": 0,
                    "tool_use_tokens": 0,
                    "total_tokens": 0
                }
            
            agents[agent_name]["call_count"] += 1
            agents[agent_name]["prompt_tokens"] += record.get("prompt_tokens", 0) or 0
            agents[agent_name]["response_tokens"] += record.get("response_tokens", 0) or 0
            agents[agent_name]["thoughts_tokens"] += record.get("thoughts_tokens", 0) or 0
            agents[agent_name]["tool_use_tokens"] += record.get("tool_use_tokens", 0) or 0
            agents[agent_name]["total_tokens"] += (record.get("prompt_tokens", 0) or 0) + (record.get("response_tokens", 0) or 0)
        
        return {
            "total_prompt_tokens": total_prompt,
            "total_response_tokens": total_response,
            "total_thoughts_tokens": total_thoughts,
            "total_tool_use_tokens": total_tool_use,
            "total_tokens": total_prompt + total_response,
            "call_count": len(records),
            "agents": agents
        }
    
    def log_token_usage(self, request_id: str, session_id: Optional[str] = None, 
                       user_id: Optional[str] = None, agent_name: Optional[str] = None,
                       model_name: Optional[str] = None, prompt_tokens: Optional[int] = None,
                       response_tokens: Optional[int] = None, thoughts_tokens: Optional[int] = None,
                       tool_use_tokens: Optional[int] = None, status: str = 'SUCCESS',
                       error_description: Optional[str] = None, 
                       timestamp: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """
        Convenience method to log token usage with all parameters.
        
        Args:
            request_id: Unique request identifier
            session_id: Session identifier (optional)
            user_id: User identifier (optional)
            agent_name: Name of the agent
            model_name: Name of the model used
            prompt_tokens: Number of prompt tokens
            response_tokens: Number of response tokens
            thoughts_tokens: Number of thoughts tokens
            tool_use_tokens: Number of tool use tokens
            status: Request status (SUCCESS, ERROR, ACCESS_DENIED, etc.)
            error_description: Description of error if status is not SUCCESS
            timestamp: Timestamp of the request (optional, defaults to now)
            
        Returns:
            Inserted record or None if failed
        """
        token_data = {
            'request_id': request_id,
            'session_id': session_id,
            'user_id': user_id,
            'agent_name': agent_name,
            'model_name': model_name,
            'prompt_tokens': prompt_tokens,
            'response_tokens': response_tokens,
            'thoughts_tokens': thoughts_tokens,
            'tool_use_tokens': tool_use_tokens,
            'status': status,
            'error_description': error_description
        }
        
        if timestamp:
            token_data['timestamp'] = timestamp
            
        return self.insert_token_usage(token_data)


# Global instance
_token_usage_service = None

def get_token_usage_service() -> TokenUsageService:
    """Get the global token usage service instance."""
    global _token_usage_service
    if _token_usage_service is None:
        _token_usage_service = TokenUsageService()
    return _token_usage_service
