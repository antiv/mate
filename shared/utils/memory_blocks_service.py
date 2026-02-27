"""
Local memory blocks service: CRUD for memory_blocks table.
Used by agent tools and dashboard API.
"""

import json
import logging
from typing import Dict, Any, List, Optional

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


class MemoryBlocksService:
    """CRUD for memory blocks scoped by project_id."""

    def __init__(self, db_client):
        self.db_client = db_client

    def _get_session(self):
        return self.db_client.get_session() if self.db_client else None

    def list_blocks(
        self,
        project_id: int,
        limit: int = 100,
        label: Optional[str] = None,
        label_search: Optional[str] = None,
        value_search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List blocks for a project with optional filters."""
        from shared.utils.models import MemoryBlock

        session = self._get_session()
        if not session:
            return {"status": "error", "error_message": "Database session not available"}

        try:
            q = session.query(MemoryBlock).filter(MemoryBlock.project_id == project_id)
            if label:
                q = q.filter(MemoryBlock.label == label)
            if label_search:
                q = q.filter(MemoryBlock.label.contains(label_search))
            if value_search:
                q = q.filter(MemoryBlock.value.contains(value_search))
            rows = q.limit(limit).all()
            blocks = [row.to_dict() for row in rows]
            return {"status": "success", "blocks": blocks, "block_count": len(blocks)}
        except Exception as e:
            logger.exception("list_blocks failed")
            return {"status": "error", "error_message": str(e)}
        finally:
            session.close()

    def get_block(
        self,
        project_id: int,
        block_id: str,
    ) -> Dict[str, Any]:
        """Get one block by id (numeric) or by label."""
        from shared.utils.models import MemoryBlock

        session = self._get_session()
        if not session:
            return {"status": "error", "error_message": "Database session not available"}

        try:
            # Try numeric id first
            if block_id.isdigit():
                row = session.query(MemoryBlock).filter(
                    MemoryBlock.project_id == project_id,
                    MemoryBlock.id == int(block_id),
                ).first()
            else:
                row = session.query(MemoryBlock).filter(
                    MemoryBlock.project_id == project_id,
                    MemoryBlock.label == block_id,
                ).first()
            if not row:
                return {"status": "error", "error_message": f"Block not found: {block_id}"}
            return {"status": "success", **row.to_dict()}
        except Exception as e:
            logger.exception("get_block failed")
            return {"status": "error", "error_message": str(e)}
        finally:
            session.close()

    def create_block(
        self,
        project_id: int,
        label: str,
        value: str = "",
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a memory block. Label must be unique per project."""
        from shared.utils.models import MemoryBlock

        session = self._get_session()
        if not session:
            return {"status": "error", "error_message": "Database session not available"}

        try:
            block = MemoryBlock(
                project_id=project_id,
                label=label.strip(),
                value=value or "",
                description=description,
            )
            if metadata is not None:
                block.set_metadata(metadata)
            session.add(block)
            session.commit()
            session.refresh(block)
            return {
                "status": "success",
                "block_id": str(block.id),
                "label": block.label,
                "value": block.value,
                "message": f"Created memory block '{block.label}' with ID {block.id}",
            }
        except IntegrityError as e:
            session.rollback()
            return {"status": "error", "error_message": f"Label already exists in project: {label}"}
        except Exception as e:
            session.rollback()
            logger.exception("create_block failed")
            return {"status": "error", "error_message": str(e)}
        finally:
            session.close()

    def modify_block(
        self,
        project_id: int,
        block_id: str,
        value: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update block by id or label."""
        from shared.utils.models import MemoryBlock

        session = self._get_session()
        if not session:
            return {"status": "error", "error_message": "Database session not available"}

        try:
            if block_id.isdigit():
                row = session.query(MemoryBlock).filter(
                    MemoryBlock.project_id == project_id,
                    MemoryBlock.id == int(block_id),
                ).first()
            else:
                row = session.query(MemoryBlock).filter(
                    MemoryBlock.project_id == project_id,
                    MemoryBlock.label == block_id,
                ).first()
            if not row:
                return {"status": "error", "error_message": f"Block not found: {block_id}"}
            if value is not None:
                row.value = value
            if description is not None:
                row.description = description
            session.commit()
            return {"status": "success", "block_id": str(row.id), "message": f"Modified block {block_id}"}
        except Exception as e:
            session.rollback()
            logger.exception("modify_block failed")
            return {"status": "error", "error_message": str(e)}
        finally:
            session.close()

    def delete_block(self, project_id: int, block_id: str) -> Dict[str, Any]:
        """Delete block by id or label."""
        from shared.utils.models import MemoryBlock

        session = self._get_session()
        if not session:
            return {"status": "error", "error_message": "Database session not available"}

        try:
            if block_id.isdigit():
                row = session.query(MemoryBlock).filter(
                    MemoryBlock.project_id == project_id,
                    MemoryBlock.id == int(block_id),
                ).first()
            else:
                row = session.query(MemoryBlock).filter(
                    MemoryBlock.project_id == project_id,
                    MemoryBlock.label == block_id,
                ).first()
            if not row:
                return {"status": "error", "error_message": f"Block not found: {block_id}"}
            session.delete(row)
            session.commit()
            return {"status": "success", "block_id": block_id, "message": f"Deleted block {block_id}"}
        except Exception as e:
            session.rollback()
            logger.exception("delete_block failed")
            return {"status": "error", "error_message": str(e)}
        finally:
            session.close()
