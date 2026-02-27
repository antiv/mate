"""
Database Memory Service for ADK.

Implements BaseMemoryService using SQLAlchemy database storage instead of in-memory storage.
This allows memory data to persist across server restarts.
"""

import re
import logging
from typing import TYPE_CHECKING, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.memory import _utils
from .database_client import get_database_client
from .models import MemorySession, MemoryEvent
from shared.utils.utils import fix_session_events_compaction

if TYPE_CHECKING:
    from google.adk.sessions.session import Session as ADKSession
    from google.adk.events.event import Event

logger = logging.getLogger(__name__)


def _user_key(app_name: str, user_id: str) -> str:
    """Generate user key for memory operations."""
    return f'{app_name}/{user_id}'


def _extract_words_lower(text: str) -> set[str]:
    """Extract words from a string and convert them to lowercase."""
    return set([word.lower() for word in re.findall(r'[A-Za-z]+', text)])


class DBMemoryService(BaseMemoryService):
    """Database-backed memory service that persists memory data across server restarts.
    
    Uses keyword matching instead of semantic search, similar to InMemoryMemoryService.
    Stores session events in the database for persistence.
    """

    def __init__(self):
        """Initialize the database memory service."""
        self.db_client = get_database_client()
        logger.info("DBMemoryService initialized")

    def _get_session(self) -> Optional[Session]:
        """Get a database session."""
        if not self.db_client.is_connected():
            logger.error("Database client not connected")
            return None
        return self.db_client.get_session()

    def _serialize_content(self, content) -> Optional[dict]:
        """Serialize content to JSON-serializable format."""
        if not content or not hasattr(content, 'parts'):
            return None
        
        try:
            parts = []
            for part in content.parts:
                if hasattr(part, 'text') and part.text:
                    parts.append({'text': part.text})
                elif hasattr(part, 'inline_data') and part.inline_data:
                    parts.append({'inline_data': {
                        'mime_type': part.inline_data.mime_type,
                        'data': part.inline_data.data
                    }})
                # Add other content types as needed
            
            return {'parts': parts} if parts else None
        except Exception as e:
            logger.error(f"Error serializing content: {e}")
            return None

    def _deserialize_content(self, content_dict: dict):
        """Deserialize content from JSON format back to Content object."""
        if not content_dict or 'parts' not in content_dict:
            return None
        
        try:
            from google.genai import types
            parts = []
            
            for part_dict in content_dict['parts']:
                if 'text' in part_dict:
                    parts.append(types.Part(text=part_dict['text']))
                elif 'inline_data' in part_dict:
                    inline_data_dict = part_dict['inline_data']
                    parts.append(types.Part(
                        inline_data=types.Blob(
                            mime_type=inline_data_dict['mime_type'],
                            data=inline_data_dict['data']
                        )
                    ))
            
            return types.Content(parts=parts) if parts else None
        except Exception as e:
            logger.error(f"Error deserializing content: {e}")
            return None

    async def add_session_to_memory(self, session: 'ADKSession'):
        """Add a session to the database memory storage."""
        db_session = self._get_session()
        if not db_session:
            logger.error("Could not get database session")
            return

        try:
            # Check if session already exists
            existing_memory_session = db_session.query(MemorySession).filter(
                MemorySession.session_id == session.id
            ).first()

            if existing_memory_session:
                # Update existing session
                existing_memory_session.updated_at = _utils.format_timestamp()
                db_session.commit()
                logger.debug(f"Updated existing memory session: {session.id}")
            else:
                # Create new memory session
                memory_session = MemorySession(
                    session_id=session.id,
                    app_name=session.app_name,
                    user_id=session.user_id,
                    created_at=_utils.format_timestamp(),
                    updated_at=_utils.format_timestamp()
                )
                db_session.add(memory_session)
                db_session.commit()
                logger.debug(f"Created new memory session: {session.id}")

            # Workaround for ADK bug #3633: Fix EventCompaction deserialization
            # When using DatabaseSessionService, EventCompaction objects are incorrectly
            # deserialized as dicts instead of Pydantic models
            fix_session_events_compaction(session)
            
            # Store events with content
            for event in session.events:
                if not event.content or not event.content.parts:
                    continue

                # Check if event already exists
                existing_event = db_session.query(MemoryEvent).filter(
                    MemoryEvent.session_id == session.id,
                    MemoryEvent.event_id == event.id
                ).first()

                if existing_event:
                    # Update existing event
                    existing_event.content = self._serialize_content(event.content)
                    existing_event.author = event.author
                    existing_event.timestamp = event.timestamp
                    db_session.commit()
                    logger.debug(f"Updated existing memory event: {event.id}")
                else:
                    # Create new event
                    memory_event = MemoryEvent(
                        session_id=session.id,
                        event_id=event.id,
                        content=self._serialize_content(event.content),
                        author=event.author,
                        timestamp=event.timestamp,
                        created_at=_utils.format_timestamp()
                    )
                    db_session.add(memory_event)
                    db_session.commit()
                    logger.debug(f"Created new memory event: {event.id}")

        except SQLAlchemyError as e:
            logger.error(f"Database error adding session to memory: {e}")
            db_session.rollback()
        except Exception as e:
            logger.error(f"Unexpected error adding session to memory: {e}")
            db_session.rollback()
        finally:
            db_session.close()

    async def search_memory(
        self, 
        *, 
        app_name: str, 
        user_id: str, 
        query: str
    ) -> SearchMemoryResponse:
        """Search for memories matching the query."""
        db_session = self._get_session()
        if not db_session:
            logger.error("Could not get database session")
            return SearchMemoryResponse()

        try:
            # Get all memory sessions for this user
            memory_sessions = db_session.query(MemorySession).filter(
                MemorySession.app_name == app_name,
                MemorySession.user_id == user_id
            ).all()

            words_in_query = _extract_words_lower(query)
            response = SearchMemoryResponse()

            for memory_session in memory_sessions:
                # Get all events for this session
                memory_events = db_session.query(MemoryEvent).filter(
                    MemoryEvent.session_id == memory_session.session_id
                ).all()

                for memory_event in memory_events:
                    if not memory_event.content or 'parts' not in memory_event.content:
                        continue

                    # Extract text from content parts
                    text_parts = []
                    for part in memory_event.content['parts']:
                        if 'text' in part:
                            text_parts.append(part['text'])

                    if not text_parts:
                        continue

                    # Check for keyword matches
                    words_in_event = _extract_words_lower(' '.join(text_parts))
                    if not words_in_event:
                        continue

                    if any(query_word in words_in_event for query_word in words_in_query):
                        # Deserialize content back to Content object
                        content = self._deserialize_content(memory_event.content)
                        if content:
                            response.memories.append(
                                MemoryEntry(
                                    content=content,
                                    author=memory_event.author,
                                    timestamp=_utils.format_timestamp(memory_event.timestamp),
                                )
                            )

            return response

        except SQLAlchemyError as e:
            logger.error(f"Database error searching memory: {e}")
            return SearchMemoryResponse()
        except Exception as e:
            logger.error(f"Unexpected error searching memory: {e}")
            return SearchMemoryResponse()
        finally:
            db_session.close()
