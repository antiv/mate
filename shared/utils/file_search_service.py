"""
Service for managing File Search stores.
"""
import logging
from typing import Dict, List, Optional, Any
from shared.utils.database_client import DatabaseClient

logger = logging.getLogger(__name__)


class FileSearchService:
    """Service for managing File Search stores."""
    
    def __init__(self, db_client: DatabaseClient):
        self.db_client = db_client
    
    def assign_store_to_agent(
        self, 
        agent_name: str, 
        store_name: str, 
        is_primary: bool = False
    ) -> bool:
        """
        Assign a store to an agent.
        
        Note: tool_config is automatically populated from database during agent initialization.
        No manual sync is needed - just reinitialize the agent after assigning stores.
        
        Args:
            agent_name: Name of the agent
            store_name: Full store name from Gemini API (e.g., "fileSearchStores/abc123")
            is_primary: Whether this is the primary store for the agent
        
        Returns:
            True if successful, False otherwise
        """
        session = self.db_client.get_session()
        if not session:
            return False
        
        try:
            from shared.utils.models import FileSearchStore, AgentFileSearchStore, AgentConfig
            
            # Verify agent exists
            agent = session.query(AgentConfig).filter_by(name=agent_name).first()
            if not agent:
                logger.error(f"Agent {agent_name} not found")
                return False
            
            # Get or create store record
            store = session.query(FileSearchStore).filter_by(store_name=store_name).first()
            if not store:
                logger.error(f"Store {store_name} not found. Create it first using create_store()")
                return False
            
            # Check if already assigned
            existing = session.query(AgentFileSearchStore).filter_by(
                agent_name=agent_name,
                store_id=store.id
            ).first()
            
            if existing:
                # Update is_primary flag
                existing.is_primary = is_primary
                logger.info(f"Updated assignment: {agent_name} -> {store_name}")
            else:
                # Create new assignment
                assignment = AgentFileSearchStore(
                    agent_name=agent_name,
                    store_id=store.id,
                    is_primary=is_primary
                )
                session.add(assignment)
                logger.info(f"Assigned store {store_name} to agent {agent_name}")
            
            session.commit()
            logger.info(f"Store {store_name} assigned to agent {agent_name} - reinitialize agent to apply File Search")
            return True
            
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to assign store to agent: {e}")
            return False
        finally:
            session.close()
    
    def unassign_store_from_agent(
        self, 
        agent_name: str, 
        store_name: str
    ) -> bool:
        """
        Remove store assignment from agent.
        
        Note: tool_config is automatically populated from database during agent initialization.
        No manual sync is needed - just reinitialize the agent after unassigning stores.
        
        Args:
            agent_name: Name of the agent
            store_name: Full store name from Gemini API
        
        Returns:
            True if successful, False otherwise
        """
        session = self.db_client.get_session()
        if not session:
            return False
        
        try:
            from shared.utils.models import FileSearchStore, AgentFileSearchStore
            
            store = session.query(FileSearchStore).filter_by(store_name=store_name).first()
            if not store:
                logger.warning(f"Store {store_name} not found")
                return False
            
            assignment = session.query(AgentFileSearchStore).filter_by(
                agent_name=agent_name,
                store_id=store.id
            ).first()
            
            if assignment:
                session.delete(assignment)
                session.commit()
                logger.info(f"Unassigned store {store_name} from agent {agent_name} - reinitialize agent to apply changes")
                return True
            else:
                logger.warning(f"Store {store_name} not assigned to agent {agent_name}")
                return False
                
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to unassign store: {e}")
            return False
        finally:
            session.close()
    
    def create_store(
        self,
        store_name: str,
        display_name: str,
        project_id: int,
        description: Optional[str] = None,
        created_by_agent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new file search store record in the database.
        
        Args:
            store_name: Full store name from Gemini API
            display_name: Human-readable display name
            project_id: Project ID this store belongs to
            description: Optional description
            created_by_agent: Optional agent name that created this store
        
        Returns:
            Dict with success status and store info
        """
        session = self.db_client.get_session()
        if not session:
            return {"success": False, "error": "Database session not available"}
        
        try:
            from shared.utils.models import FileSearchStore
            
            # Check if store already exists
            existing = session.query(FileSearchStore).filter_by(store_name=store_name).first()
            if existing:
                return {
                    "success": True,
                    "store_name": store_name,
                    "display_name": existing.display_name,
                    "store_id": existing.id,
                    "message": "Store already exists"
                }
            
            # Create new store
            new_store = FileSearchStore(
                store_name=store_name,
                display_name=display_name,
                description=description,
                project_id=project_id,
                created_by_agent=created_by_agent
            )
            session.add(new_store)
            session.commit()
            
            return {
                "success": True,
                "store_name": store_name,
                "display_name": display_name,
                "store_id": new_store.id
            }
            
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to create store: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()
    
    def create_store_and_assign(
        self,
        store_name: str,
        display_name: str,
        agent_name: str,
        project_id: int,
        description: Optional[str] = None,
        is_primary: bool = False
    ) -> Dict[str, Any]:
        """
        Create a new store and assign it to an agent in one operation.
        This is a convenience method that combines create_store() and assign_store_to_agent().
        
        Returns:
            Dict with success status and store info
        """
        # Create store first
        result = self.create_store(
            store_name=store_name,
            display_name=display_name,
            project_id=project_id,
            description=description,
            created_by_agent=agent_name
        )
        
        if not result.get("success"):
            return result
        
        # Assign to agent
        if self.assign_store_to_agent(agent_name, store_name, is_primary):
            return result
        else:
            return {"success": False, "error": "Failed to assign store after creation"}
    
    def get_stores_for_agent(self, agent_name: str) -> List[Dict]:
        """Get all stores used by an agent."""
        session = self.db_client.get_session()
        if not session:
            return []
        try:
            from shared.utils.models import FileSearchStore, AgentFileSearchStore
            
            stores = session.query(FileSearchStore).join(
                AgentFileSearchStore
            ).filter(
                AgentFileSearchStore.agent_name == agent_name
            ).all()
            
            return [store.to_dict() for store in stores]
        finally:
            session.close()
    
    def get_all_stores_for_project(self, project_id: int) -> List[Dict]:
        """Get all file search stores in a project."""
        session = self.db_client.get_session()
        if not session:
            return []
        try:
            from shared.utils.models import FileSearchStore
            
            stores = session.query(FileSearchStore).filter_by(
                project_id=project_id
            ).order_by(FileSearchStore.display_name).all()
            
            return [store.to_dict() for store in stores]
        finally:
            session.close()
    
    def get_agents_using_store(self, store_name: str) -> List[str]:
        """Get all agents that use a specific store."""
        session = self.db_client.get_session()
        if not session:
            return []
        try:
            from shared.utils.models import FileSearchStore, AgentFileSearchStore
            
            store = session.query(FileSearchStore).filter_by(store_name=store_name).first()
            if not store:
                return []
            
            assignments = session.query(AgentFileSearchStore).filter_by(
                store_id=store.id
            ).all()
            
            return [assignment.agent_name for assignment in assignments]
        finally:
            session.close()
    
    def list_files_for_agent(self, agent_name: str) -> List[Dict]:
        """List all files accessible to an agent (from all its stores)."""
        stores = self.get_stores_for_agent(agent_name)
        all_files = []
        
        for store in stores:
            files = self.list_files_in_store(store['store_name'])
            # Add store context to each file
            for file in files:
                file['store_display_name'] = store['display_name']
                file['store_name'] = store['store_name']
            all_files.extend(files)
        
        return all_files
    
    def list_files_in_store(self, store_name: str) -> List[Dict]:
        """List all files in a specific store."""
        session = self.db_client.get_session()
        if not session:
            return []
        try:
            from shared.utils.models import FileSearchStore, FileSearchDocument
            
            store = session.query(FileSearchStore).filter_by(store_name=store_name).first()
            if not store:
                return []
            
            documents = session.query(FileSearchDocument).filter_by(store_id=store.id).all()
            return [doc.to_dict() for doc in documents]
        finally:
            session.close()
    
    def add_document(
        self,
        store_name: str,
        document_name: str,
        display_name: Optional[str] = None,
        file_path: Optional[str] = None,
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        status: str = 'processing',
        uploaded_by_agent: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add a document record to a store.
        
        Args:
            store_name: Full store name from Gemini API
            document_name: Full document name from Gemini API (required, will generate if None)
            display_name: Optional human-readable name
            file_path: Optional original file path
            file_size: Optional file size in bytes
            mime_type: Optional MIME type
            status: Document status (processing, completed, failed)
            uploaded_by_agent: Optional agent name that uploaded this
        
        Returns:
            Dict with success status and document info
        """
        session = self.db_client.get_session()
        if not session:
            return {"success": False, "error": "Database session not available"}
        
        try:
            from shared.utils.models import FileSearchStore, FileSearchDocument
            import hashlib
            
            # Validate document_name - generate if None or empty
            if not document_name:
                # Generate a document name based on file path or display name
                if file_path:
                    file_hash = hashlib.md5(file_path.encode()).hexdigest()[:16]
                    document_name = f"fileSearchDocuments/{file_hash}"
                elif display_name:
                    file_hash = hashlib.md5(display_name.encode()).hexdigest()[:16]
                    document_name = f"fileSearchDocuments/{file_hash}"
                else:
                    import time
                    document_name = f"fileSearchDocuments/{int(time.time())}"
                logger.warning(f"Generated document_name: {document_name}")
            
            store = session.query(FileSearchStore).filter_by(store_name=store_name).first()
            if not store:
                return {"success": False, "error": f"Store {store_name} not found"}
            
            # Check if document already exists
            existing = session.query(FileSearchDocument).filter_by(
                store_id=store.id,
                document_name=document_name
            ).first()
            
            if existing:
                # Update existing document
                existing.display_name = display_name or existing.display_name
                existing.file_path = file_path or existing.file_path
                existing.file_size = file_size or existing.file_size
                existing.mime_type = mime_type or existing.mime_type
                existing.status = status
                existing.uploaded_by_agent = uploaded_by_agent or existing.uploaded_by_agent
                session.commit()
                return {
                    "success": True,
                    "document_name": document_name,
                    "document_id": existing.id,
                    "message": "Document updated"
                }
            
            # Create new document
            new_doc = FileSearchDocument(
                store_id=store.id,
                document_name=document_name,
                display_name=display_name,
                file_path=file_path,
                file_size=file_size,
                mime_type=mime_type,
                status=status,
                uploaded_by_agent=uploaded_by_agent
            )
            session.add(new_doc)
            session.commit()
            
            return {
                "success": True,
                "document_name": document_name,
                "document_id": new_doc.id
            }
            
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to add document: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()
    
    def delete_document(self, store_name: str, document_name: str) -> bool:
        """Delete a document from a store."""
        session = self.db_client.get_session()
        if not session:
            return False
        
        try:
            from shared.utils.models import FileSearchStore, FileSearchDocument
            
            store = session.query(FileSearchStore).filter_by(store_name=store_name).first()
            if not store:
                return False
            
            document = session.query(FileSearchDocument).filter_by(
                store_id=store.id,
                document_name=document_name
            ).first()
            
            if document:
                session.delete(document)
                session.commit()
                return True
            return False
            
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to delete document: {e}")
            return False
        finally:
            session.close()
    
    def delete_store(self, store_name: str) -> Dict[str, Any]:
        """
        Delete a file search store.
        
        This will:
        1. Get all agents using this store
        2. Remove store assignments from all agents
        3. Delete all documents in the store
        4. Delete the store itself
        
        Args:
            store_name: Full store name from Gemini API
        
        Returns:
            Dict with success status, agents_affected list, and message
        """
        session = self.db_client.get_session()
        if not session:
            return {"success": False, "error": "Database session not available"}
        
        try:
            from shared.utils.models import FileSearchStore, AgentFileSearchStore, FileSearchDocument
            
            store = session.query(FileSearchStore).filter_by(store_name=store_name).first()
            if not store:
                return {"success": False, "error": f"Store {store_name} not found"}
            
            # Get all agents using this store
            assignments = session.query(AgentFileSearchStore).filter_by(store_id=store.id).all()
            agents_affected = [assignment.agent_name for assignment in assignments]
            
            # Delete all agent assignments
            for assignment in assignments:
                session.delete(assignment)
            
            # Delete all documents in the store
            documents = session.query(FileSearchDocument).filter_by(store_id=store.id).all()
            for document in documents:
                session.delete(document)
            
            # Delete the store itself
            session.delete(store)
            session.commit()
            
            logger.info(f"Deleted store {store_name} (was used by {len(agents_affected)} agent(s))")
            
            return {
                "success": True,
                "agents_affected": agents_affected,
                "message": f"Store deleted successfully. It was removed from {len(agents_affected)} agent(s)."
            }
            
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to delete store: {e}")
            return {"success": False, "error": str(e)}
        finally:
            session.close()

