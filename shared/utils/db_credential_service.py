"""
Database Credential Service for ADK.

Implements BaseCredentialService using SQLAlchemy database storage instead of in-memory storage.
This allows credential data to persist across server restarts.
"""

import json
import logging
from typing import Optional, TYPE_CHECKING
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from google.adk.auth.credential_service.base_credential_service import BaseCredentialService
from .database_client import get_database_client
from .models import Credential

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.auth.auth_credential import AuthCredential
    from google.adk.auth.auth_tool import AuthConfig

logger = logging.getLogger(__name__)


class DBCredentialService(BaseCredentialService):
    """Database-backed credential service that persists credential data across server restarts.
    
    Uses SQLAlchemy database storage for credentials, similar to InMemoryCredentialService
    but with persistence across server restarts.
    """

    def __init__(self):
        """Initialize the database credential service."""
        super().__init__()
        self.db_client = get_database_client()
        logger.info("DBCredentialService initialized")

    def _get_session(self) -> Optional[Session]:
        """Get a database session."""
        if not self.db_client.is_connected():
            logger.error("Database client not connected")
            return None
        return self.db_client.get_session()

    def _serialize_credential(self, credential) -> Optional[str]:
        """Serialize AuthCredential to JSON string."""
        if not credential:
            return None
        
        try:
            # Convert credential to dictionary format
            credential_dict = {
                'type': type(credential).__name__,
                'data': {}
            }
            
            # Extract credential data based on type
            if hasattr(credential, 'access_token'):
                credential_dict['data']['access_token'] = credential.access_token
            if hasattr(credential, 'refresh_token'):
                credential_dict['data']['refresh_token'] = credential.refresh_token
            if hasattr(credential, 'expires_at'):
                credential_dict['data']['expires_at'] = credential.expires_at.isoformat() if credential.expires_at else None
            if hasattr(credential, 'token_type'):
                credential_dict['data']['token_type'] = credential.token_type
            if hasattr(credential, 'scope'):
                credential_dict['data']['scope'] = credential.scope
            
            # Add any other attributes
            for attr_name in dir(credential):
                if not attr_name.startswith('_') and not callable(getattr(credential, attr_name)):
                    if attr_name not in credential_dict['data']:
                        try:
                            value = getattr(credential, attr_name)
                            if value is not None:
                                credential_dict['data'][attr_name] = str(value)
                        except Exception:
                            pass  # Skip attributes that can't be serialized
            
            return json.dumps(credential_dict)
            
        except Exception as e:
            logger.error(f"Error serializing credential: {e}")
            return None

    def _deserialize_credential(self, credential_json: str):
        """Deserialize JSON string to AuthCredential."""
        if not credential_json:
            return None
        
        try:
            credential_dict = json.loads(credential_json)
            
            # Import AuthCredential classes
            from google.adk.auth.auth_credential import AuthCredential
            
            # Create credential based on type
            credential_type = credential_dict.get('type', 'AuthCredential')
            data = credential_dict.get('data', {})
            
            if credential_type == 'AuthCredential':
                # Create basic AuthCredential
                credential = AuthCredential()
                
                # Set attributes if they exist
                if 'access_token' in data:
                    credential.access_token = data['access_token']
                if 'refresh_token' in data:
                    credential.refresh_token = data['refresh_token']
                if 'expires_at' in data and data['expires_at']:
                    from datetime import datetime
                    credential.expires_at = datetime.fromisoformat(data['expires_at'])
                if 'token_type' in data:
                    credential.token_type = data['token_type']
                if 'scope' in data:
                    credential.scope = data['scope']
                
                return credential
            else:
                # Fallback to basic AuthCredential
                logger.warning(f"Unknown credential type: {credential_type}, using basic AuthCredential")
                return AuthCredential()
                
        except Exception as e:
            logger.error(f"Error deserializing credential: {e}")
            return None

    async def load_credential(
        self,
        auth_config: "AuthConfig",
        callback_context: "CallbackContext",
    ) -> Optional["AuthCredential"]:
        """Load credential from database."""
        session = self._get_session()
        if not session:
            logger.error("No database session available")
            return None
        
        try:
            app_name = callback_context._invocation_context.app_name
            user_id = callback_context._invocation_context.user_id
            credential_key = auth_config.credential_key
            
            # Query for existing credential
            credential_record = session.query(Credential).filter(
                Credential.app_name == app_name,
                Credential.user_id == user_id,
                Credential.credential_key == credential_key
            ).first()
            
            if credential_record:
                logger.debug(f"Loaded credential for {app_name}/{user_id}/{credential_key}")
                return self._deserialize_credential(credential_record.credential_data)
            else:
                logger.debug(f"No credential found for {app_name}/{user_id}/{credential_key}")
                return None
                
        except SQLAlchemyError as e:
            logger.error(f"Database error loading credential: {e}")
            return None
        except Exception as e:
            logger.error(f"Error loading credential: {e}")
            return None
        finally:
            session.close()

    async def save_credential(
        self,
        auth_config: "AuthConfig",
        callback_context: "CallbackContext",
    ) -> None:
        """Save credential to database."""
        session = self._get_session()
        if not session:
            logger.error("No database session available")
            return
        
        try:
            app_name = callback_context._invocation_context.app_name
            user_id = callback_context._invocation_context.user_id
            credential_key = auth_config.credential_key
            credential_data = self._serialize_credential(auth_config.exchanged_auth_credential)
            
            if not credential_data:
                logger.error("Failed to serialize credential data")
                return
            
            # Check if credential already exists
            existing_credential = session.query(Credential).filter(
                Credential.app_name == app_name,
                Credential.user_id == user_id,
                Credential.credential_key == credential_key
            ).first()
            
            if existing_credential:
                # Update existing credential
                existing_credential.credential_data = credential_data
                logger.debug(f"Updated credential for {app_name}/{user_id}/{credential_key}")
            else:
                # Create new credential
                new_credential = Credential(
                    app_name=app_name,
                    user_id=user_id,
                    credential_key=credential_key,
                    credential_data=credential_data
                )
                session.add(new_credential)
                logger.debug(f"Created new credential for {app_name}/{user_id}/{credential_key}")
            
            session.commit()
            
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Database error saving credential: {e}")
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving credential: {e}")
        finally:
            session.close()
