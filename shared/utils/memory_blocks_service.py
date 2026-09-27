"""
Local memory blocks service: CRUD for memory_blocks table.
Used by agent tools and dashboard API.

Every write records a version (memory_block_versions) in the same transaction,
naming who made it, so any change can be undone and a deleted block restored.
"""

import json
import logging
from contextlib import contextmanager
from typing import Dict, Any, List, Optional

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

# Versions kept per block. A trigger writing a block every hour would otherwise
# add hundreds a month.
MAX_VERSIONS_PER_BLOCK = 20


def block_state(row) -> Dict[str, Any]:
    return {"label": row.label, "value": row.value or "",
            "description": row.description, "metadata": row.block_metadata}


def record_block_version(session, row, change_type: str, changed_by: Optional[str] = None,
                         previous: Optional[Dict[str, Any]] = None) -> int:
    """
    Add a version holding *row* as it is now, inside the caller's transaction.

    *previous* is the block's state before this write. A block written before
    versioning existed has no history, so that state is recorded first as a
    ``baseline``, and the change can still be undone. Older versions beyond
    MAX_VERSIONS_PER_BLOCK are removed. Returns the new version number.
    """
    from sqlalchemy import func
    from shared.utils.models import MemoryBlockVersion

    if row.id is None:
        session.flush()
    if change_type == "create":
        # A new block has no history. Rows under its id belong to a deleted block
        # whose id the database handed out again (MySQL before 8.0 can).
        session.query(MemoryBlockVersion).filter(
            MemoryBlockVersion.block_id == row.id).delete(synchronize_session=False)
    latest = session.query(func.max(MemoryBlockVersion.version_number)).filter(
        MemoryBlockVersion.block_id == row.id).scalar() or 0

    def add(state, kind, who):
        nonlocal latest
        latest += 1
        session.add(MemoryBlockVersion(
            project_id=row.project_id, block_id=row.id, version_number=latest,
            label=state["label"], value=state["value"] or "", description=state["description"],
            block_metadata=state["metadata"], change_type=kind,
            changed_by=who[:255] if who else None,
        ))

    if previous is not None and latest == 0:
        add(previous, "baseline", None)
    add(block_state(row), change_type, changed_by)
    session.flush()

    stale = [v.id for v in session.query(MemoryBlockVersion.id).filter(
        MemoryBlockVersion.block_id == row.id,
        MemoryBlockVersion.version_number <= latest - MAX_VERSIONS_PER_BLOCK,
    )]
    if stale:
        session.query(MemoryBlockVersion).filter(
            MemoryBlockVersion.id.in_(stale)).delete(synchronize_session=False)
    return latest


@contextmanager
def _memory_blocks_span(operation: str):
    """Context manager for mate.memory_blocks span."""
    span = None
    try:
        from shared.utils.tracing.tracing_config import is_tracing_enabled
        if is_tracing_enabled():
            from opentelemetry import trace
            from shared.utils.tracing.tracer import get_tracer
            tracer = get_tracer("mate", "1.0.0")
            span = tracer.start_span("mate.memory_blocks")
            span.set_attribute("mate.memory.operation", operation)
    except Exception:
        pass
    try:
        yield
    finally:
        if span:
            try:
                span.end()
            except Exception:
                pass


class MemoryBlocksService:
    """CRUD for memory blocks scoped by project_id."""

    def __init__(self, db_client):
        self.db_client = db_client

    def _get_session(self):
        return self.db_client.get_session() if self.db_client else None

    def _set_block_embedding(self, row) -> None:
        """Best-effort: compute and set embedding fields on a block row.

        Never raises — a failed embedding must not break block writes.
        Skips the API call when the stored embedding is already current.
        """
        from shared.utils import embedding_service as emb

        try:
            text = emb.embedding_text_for_block(row.label, row.description, row.value)
            new_hash = emb.embedding_hash_for_text(text)
            model = emb.get_embedding_model()
            if row.embedding and row.embedding_model == model and row.embedding_hash == new_hash:
                return
            vectors = emb.embed_texts([text])
            if vectors:
                row.embedding = json.dumps(vectors[0])
                row.embedding_model = model
                row.embedding_hash = new_hash
        except Exception as e:
            logger.warning(f"Skipping embedding for block '{getattr(row, 'label', '?')}': {e}")

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

        with _memory_blocks_span("list"):
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

        with _memory_blocks_span("get"):
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
        changed_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a memory block. Label must be unique per project."""
        from shared.utils.models import MemoryBlock

        with _memory_blocks_span("create"):
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
                self._set_block_embedding(block)
                session.add(block)
                session.flush()
                record_block_version(session, block, "create", changed_by)
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
        metadata: Optional[Dict[str, Any]] = None,
        changed_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update block by id or label. *metadata* replaces the stored metadata when given."""
        from shared.utils.models import MemoryBlock

        with _memory_blocks_span("modify"):
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
                previous = block_state(row)
                if value is not None:
                    row.value = value
                if description is not None:
                    row.description = description
                if metadata is not None:
                    row.set_metadata(metadata)
                if block_state(row) == previous:
                    return {"status": "success", "block_id": str(row.id), "message": f"Block {block_id} unchanged"}
                self._set_block_embedding(row)
                record_block_version(session, row, "update", changed_by, previous=previous)
                session.commit()
                return {"status": "success", "block_id": str(row.id), "message": f"Modified block {block_id}"}
            except Exception as e:
                session.rollback()
                logger.exception("modify_block failed")
                return {"status": "error", "error_message": str(e)}
            finally:
                session.close()

    def semantic_search_blocks(
        self,
        project_id: int,
        query: str,
        top_k: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """Rank project blocks by cosine similarity to the query.

        Returns None when embeddings are unavailable (caller falls back to
        keyword search). Blocks with missing/stale embeddings are re-embedded
        in one batch before ranking (lazy backfill).
        """
        from shared.utils.models import MemoryBlock
        from shared.utils import embedding_service as emb

        with _memory_blocks_span("semantic_search"):
            query_vectors = emb.embed_texts([query])
            if not query_vectors:
                return None
            query_vec = query_vectors[0]

            session = self._get_session()
            if not session:
                return {"status": "error", "error_message": "Database session not available"}

            try:
                rows = session.query(MemoryBlock).filter(MemoryBlock.project_id == project_id).all()
                model = emb.get_embedding_model()

                stale = []
                for row in rows:
                    text = emb.embedding_text_for_block(row.label, row.description, row.value)
                    text_hash = emb.embedding_hash_for_text(text)
                    if not row.embedding or row.embedding_model != model or row.embedding_hash != text_hash:
                        stale.append((row, text, text_hash))
                if stale:
                    vectors = emb.embed_texts([text for _, text, _ in stale])
                    if vectors:
                        for (row, _, text_hash), vector in zip(stale, vectors):
                            row.embedding = json.dumps(vector)
                            row.embedding_model = model
                            row.embedding_hash = text_hash
                        session.commit()

                scored = []
                for row in rows:
                    if not row.embedding or row.embedding_model != model:
                        continue
                    try:
                        vector = json.loads(row.embedding)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    block = row.to_dict()
                    value = block.pop("value", "") or ""
                    block["value_preview"] = value[:200]
                    block["score"] = round(emb.cosine_similarity(query_vec, vector), 4)
                    scored.append(block)

                scored.sort(key=lambda b: b["score"], reverse=True)
                top = scored[:max(1, top_k)]
                return {"status": "success", "blocks": top, "block_count": len(top)}
            except Exception as e:
                session.rollback()
                logger.exception("semantic_search_blocks failed")
                return {"status": "error", "error_message": str(e)}
            finally:
                session.close()

    def delete_block(self, project_id: int, block_id: str,
                     changed_by: Optional[str] = None) -> Dict[str, Any]:
        """Delete block by id or label. Its history is kept, so it can be restored."""
        from shared.utils.models import MemoryBlock

        with _memory_blocks_span("delete"):
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
                record_block_version(session, row, "delete", changed_by)
                session.delete(row)
                session.commit()
                return {"status": "success", "block_id": block_id, "message": f"Deleted block {block_id}"}
            except Exception as e:
                session.rollback()
                logger.exception("delete_block failed")
                return {"status": "error", "error_message": str(e)}
            finally:
                session.close()

    def list_versions(self, project_id: int, block_id: str) -> Dict[str, Any]:
        """A block's versions, newest first. *block_id* is an id, or a label for a block that exists."""
        from shared.utils.models import MemoryBlock, MemoryBlockVersion

        session = self._get_session()
        if not session:
            return {"status": "error", "error_message": "Database session not available"}
        try:
            if block_id.isdigit():
                numeric_id = int(block_id)
            else:
                row = session.query(MemoryBlock).filter(
                    MemoryBlock.project_id == project_id, MemoryBlock.label == block_id).first()
                if not row:
                    return {"status": "error", "error_code": "not_found",
                            "error_message": f"Block not found: {block_id}"}
                numeric_id = row.id
            versions = session.query(MemoryBlockVersion).filter(
                MemoryBlockVersion.project_id == project_id,
                MemoryBlockVersion.block_id == numeric_id,
            ).order_by(MemoryBlockVersion.version_number.desc()).all()
            return {"status": "success", "versions": [v.to_dict() for v in versions]}
        except Exception as e:
            logger.exception("list_versions failed")
            return {"status": "error", "error_message": str(e)}
        finally:
            session.close()

    def list_deleted_blocks(self, project_id: int) -> Dict[str, Any]:
        """Blocks of the project that were deleted, each as its last recorded version."""
        from shared.utils.models import MemoryBlock, MemoryBlockVersion

        session = self._get_session()
        if not session:
            return {"status": "error", "error_message": "Database session not available"}
        try:
            existing = session.query(MemoryBlock.id).filter(MemoryBlock.project_id == project_id)
            versions = session.query(MemoryBlockVersion).filter(
                MemoryBlockVersion.project_id == project_id,
                ~MemoryBlockVersion.block_id.in_(existing),
            ).order_by(MemoryBlockVersion.version_number.desc()).all()
            latest: Dict[int, Any] = {}
            for v in versions:
                latest.setdefault(v.block_id, v)
            blocks = sorted((v.to_dict() for v in latest.values()),
                            key=lambda b: b["created_at"] or "", reverse=True)
            return {"status": "success", "blocks": blocks}
        except Exception as e:
            logger.exception("list_deleted_blocks failed")
            return {"status": "error", "error_message": str(e)}
        finally:
            session.close()

    def restore_version(self, project_id: int, version_id: int,
                        changed_by: Optional[str] = None) -> Dict[str, Any]:
        """
        Put a block back as it was in one of its versions, recreating it under its
        old id if it was deleted. The restore is itself a version. Refused with
        ``error_code: conflict`` when another block now holds the label.
        """
        from shared.utils.models import MemoryBlock, MemoryBlockVersion

        session = self._get_session()
        if not session:
            return {"status": "error", "error_message": "Database session not available"}
        try:
            version = session.query(MemoryBlockVersion).filter(
                MemoryBlockVersion.project_id == project_id,
                MemoryBlockVersion.id == version_id,
            ).first()
            if not version:
                return {"status": "error", "error_code": "not_found",
                        "error_message": f"Version not found: {version_id}"}

            taken = session.query(MemoryBlock).filter(
                MemoryBlock.project_id == project_id,
                MemoryBlock.label == version.label,
                MemoryBlock.id != version.block_id,
            ).first()
            if taken:
                return {"status": "error", "error_code": "conflict",
                        "error_message": f"Another block is now labelled '{version.label}'"}

            row = session.query(MemoryBlock).filter(
                MemoryBlock.project_id == project_id, MemoryBlock.id == version.block_id).first()
            previous = block_state(row) if row else None
            if not row:
                row = MemoryBlock(id=version.block_id, project_id=project_id)
                session.add(row)
            row.label = version.label
            row.value = version.value or ""
            row.description = version.description
            row.block_metadata = version.block_metadata
            if previous is not None and block_state(row) == previous:
                return {"status": "success", "block_id": str(row.id), "unchanged": True,
                        "message": "The block already matches this version"}

            self._set_block_embedding(row)
            number = record_block_version(session, row, "restore", changed_by, previous=previous)
            session.commit()
            return {"status": "success", "block_id": str(row.id), "label": row.label,
                    "version_number": number, "restored_from": version.version_number,
                    "recreated": previous is None}
        except IntegrityError:
            session.rollback()
            return {"status": "error", "error_code": "conflict",
                    "error_message": "The block could not be restored because its label is taken"}
        except Exception as e:
            session.rollback()
            logger.exception("restore_version failed")
            return {"status": "error", "error_message": str(e)}
        finally:
            session.close()
