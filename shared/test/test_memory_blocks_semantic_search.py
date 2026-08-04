#!/usr/bin/env python3
"""
Unit tests for semantic search over memory blocks:
embedding_service helpers, MemoryBlocksService embedding/backfill,
and the search_shared_blocks agent tool (semantic + keyword fallback).
"""

import json
import unittest
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shared.utils import embedding_service
from shared.utils.embedding_service import cosine_similarity, embedding_hash_for_text, embedding_text_for_block
from shared.utils.memory_blocks_service import MemoryBlocksService
from shared.utils.models import Base, MemoryBlock


class FakeDbClient:
    """Minimal db client exposing get_session() over an in-memory SQLite DB."""

    def __init__(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(bind=self.engine)

    def get_session(self):
        return self._factory()


class TestCosineSimilarity(unittest.TestCase):

    def test_identical_vectors(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 2.0], [1.0, 2.0]), 1.0)

    def test_orthogonal_vectors(self):
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_zero_vector(self):
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 2.0]), 0.0)

    def test_mismatched_or_empty(self):
        self.assertEqual(cosine_similarity([1.0], [1.0, 2.0]), 0.0)
        self.assertEqual(cosine_similarity([], []), 0.0)


class TestEmbedTexts(unittest.TestCase):

    def test_returns_none_on_provider_error(self):
        with patch("litellm.embedding", side_effect=Exception("no api key")):
            self.assertIsNone(embedding_service.embed_texts(["hello"]))

    def test_returns_vectors_in_input_order(self):
        response = Mock()
        response.data = [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]
        with patch("litellm.embedding", return_value=response):
            vectors = embedding_service.embed_texts(["a", "b"])
        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])

    def test_empty_input(self):
        self.assertEqual(embedding_service.embed_texts([]), [])


class TestServiceEmbeddingOnWrite(unittest.TestCase):

    def setUp(self):
        self.service = MemoryBlocksService(FakeDbClient())

    def _get_row(self, label):
        session = self.service.db_client.get_session()
        try:
            return session.query(MemoryBlock).filter(MemoryBlock.label == label).first()
        finally:
            session.close()

    def test_create_block_stores_embedding(self):
        with patch("shared.utils.embedding_service.embed_texts", return_value=[[1.0, 0.0]]):
            result = self.service.create_block(project_id=1, label="greeting", value="hello world")
        self.assertEqual(result["status"], "success")
        row = self._get_row("greeting")
        self.assertEqual(json.loads(row.embedding), [1.0, 0.0])
        self.assertEqual(row.embedding_model, embedding_service.get_embedding_model())
        expected_hash = embedding_hash_for_text(embedding_text_for_block("greeting", None, "hello world"))
        self.assertEqual(row.embedding_hash, expected_hash)

    def test_create_block_succeeds_when_embedding_fails(self):
        with patch("shared.utils.embedding_service.embed_texts", return_value=None):
            result = self.service.create_block(project_id=1, label="no_embed", value="text")
        self.assertEqual(result["status"], "success")
        row = self._get_row("no_embed")
        self.assertIsNone(row.embedding)

    def test_modify_block_refreshes_embedding(self):
        with patch("shared.utils.embedding_service.embed_texts", return_value=[[1.0, 0.0]]):
            self.service.create_block(project_id=1, label="doc", value="old")
        with patch("shared.utils.embedding_service.embed_texts", return_value=[[0.0, 1.0]]):
            result = self.service.modify_block(project_id=1, block_id="doc", value="new")
        self.assertEqual(result["status"], "success")
        row = self._get_row("doc")
        self.assertEqual(json.loads(row.embedding), [0.0, 1.0])
        expected_hash = embedding_hash_for_text(embedding_text_for_block("doc", None, "new"))
        self.assertEqual(row.embedding_hash, expected_hash)


class TestSemanticSearch(unittest.TestCase):

    def setUp(self):
        self.service = MemoryBlocksService(FakeDbClient())
        # Blocks created without embeddings — exercises lazy backfill at search time.
        with patch("shared.utils.embedding_service.embed_texts", return_value=None):
            self.service.create_block(project_id=1, label="alpha", value="about cats")
            self.service.create_block(project_id=1, label="beta", value="about dogs")
            self.service.create_block(project_id=1, label="gamma", value="about kittens")
            self.service.create_block(project_id=2, label="other_project", value="about cats")

    def test_returns_none_when_query_embedding_unavailable(self):
        with patch("shared.utils.embedding_service.embed_texts", return_value=None):
            self.assertIsNone(self.service.semantic_search_blocks(project_id=1, query="cats"))

    def test_backfills_and_ranks_by_similarity(self):
        def fake_embed(texts):
            if len(texts) == 1:  # the query
                return [[1.0, 0.0]]
            # backfill batch for alpha, beta, gamma (creation order)
            return [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]

        with patch("shared.utils.embedding_service.embed_texts", side_effect=fake_embed):
            result = self.service.semantic_search_blocks(project_id=1, query="cats", top_k=2)

        self.assertEqual(result["status"], "success")
        labels = [b["label"] for b in result["blocks"]]
        self.assertEqual(labels, ["alpha", "gamma"])
        self.assertGreater(result["blocks"][0]["score"], result["blocks"][1]["score"])
        self.assertNotIn("value", result["blocks"][0])
        self.assertEqual(result["blocks"][0]["value_preview"], "about cats")

        # Embeddings persisted: a second search only embeds the query.
        calls = []

        def count_embed(texts):
            calls.append(texts)
            return [[1.0, 0.0]]

        with patch("shared.utils.embedding_service.embed_texts", side_effect=count_embed):
            result2 = self.service.semantic_search_blocks(project_id=1, query="cats", top_k=2)
        self.assertEqual(result2["status"], "success")
        self.assertEqual(len(calls), 1)

    def test_scoped_to_project(self):
        def fake_embed(texts):
            return [[1.0, 0.0]] * len(texts)

        with patch("shared.utils.embedding_service.embed_texts", side_effect=fake_embed):
            result = self.service.semantic_search_blocks(project_id=2, query="cats", top_k=10)
        self.assertEqual([b["label"] for b in result["blocks"]], ["other_project"])


class TestSearchSharedBlocksTool(unittest.TestCase):

    def test_tool_is_registered(self):
        from shared.utils.tools.memory_blocks_tools import create_memory_blocks_tools_from_config
        tools = create_memory_blocks_tools_from_config({"project_id": 1})
        names = [t.__name__ for t in tools]
        self.assertIn("search_shared_blocks", names)

    def test_semantic_path(self):
        mock_service = Mock()
        mock_service.semantic_search_blocks.return_value = {
            "status": "success",
            "blocks": [{"label": "alpha", "value_preview": "x", "score": 0.9}],
            "block_count": 1,
        }
        from shared.utils.tools.memory_blocks_tools import create_memory_blocks_tools_from_config
        with patch("shared.utils.tools.memory_blocks_tools._get_service", return_value=mock_service):
            tools = create_memory_blocks_tools_from_config({"project_id": 1})
            tool = next(t for t in tools if t.__name__ == "search_shared_blocks")
            result = tool(query="cats")
        self.assertEqual(result["search_type"], "semantic")
        self.assertEqual(result["blocks"][0]["label"], "alpha")

    def test_keyword_fallback(self):
        mock_service = Mock()
        mock_service.semantic_search_blocks.return_value = None
        mock_service.list_blocks.return_value = {
            "status": "success",
            "blocks": [{"label": "alpha", "value": "v" * 300, "description": None}],
            "block_count": 1,
        }
        from shared.utils.tools.memory_blocks_tools import create_memory_blocks_tools_from_config
        with patch("shared.utils.tools.memory_blocks_tools._get_service", return_value=mock_service):
            tools = create_memory_blocks_tools_from_config({"project_id": 1})
            tool = next(t for t in tools if t.__name__ == "search_shared_blocks")
            result = tool(query="cats")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["search_type"], "keyword")
        self.assertEqual(len(result["blocks"]), 1)  # deduped across label/value search
        self.assertEqual(len(result["blocks"][0]["value_preview"]), 200)
        self.assertNotIn("value", result["blocks"][0])

    def test_empty_query(self):
        from shared.utils.tools.memory_blocks_tools import create_memory_blocks_tools_from_config
        tools = create_memory_blocks_tools_from_config({"project_id": 1})
        tool = next(t for t in tools if t.__name__ == "search_shared_blocks")
        result = tool(query="  ")
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
