"""
Unit tests for Dashboard Session Tracking API (ADK & LangGraph).
"""

import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI

from shared.utils.dashboard.dashboard_server import DashboardServer
from shared.utils.models import LangGraphSession, LangGraphEvent

PROJECT_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def mock_dashboard_server():
    app = FastAPI()
    with patch("shared.utils.database_client.get_database_client") as mock_db:
        mock_db_client = MagicMock()
        mock_db.return_value = mock_db_client
        server = DashboardServer(app, project_root=PROJECT_ROOT)
        yield server, app


def test_get_sessions_langgraph(mock_dashboard_server):
    server, app = mock_dashboard_server
    
    mock_db_session = MagicMock()
    server.db_client = MagicMock()
    server.db_client.get_session.return_value = mock_db_session

    mock_session = MagicMock(spec=LangGraphSession)
    mock_session.id = "session-lg-123"
    mock_session.app_name = "test_agent"
    mock_session.user_id = "test_user"
    mock_session.created_at = MagicMock(isoformat=lambda: "2026-07-23T10:00:00Z")
    mock_session.updated_at = MagicMock(isoformat=lambda: "2026-07-23T10:05:00Z")

    # Set mock return for query chained calls
    mock_query = MagicMock()
    mock_db_session.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.all.return_value = [mock_session]
    mock_query.count.return_value = 2

    mock_last_evt = MagicMock()
    mock_last_evt.content = json.dumps({"role": "model", "parts": [{"text": "Hello world response"}]})
    mock_query.first.return_value = mock_last_evt

    result = server._get_sessions(runtime="langgraph", page=1, limit=50)

    assert "sessions" in result
    assert result["total"] == 1
    assert result["sessions"][0]["id"] == "session-lg-123"
    assert result["sessions"][0]["runtime"] == "langgraph"
    assert result["sessions"][0]["app_name"] == "test_agent"
    assert result["sessions"][0]["last_preview"] == "Hello world response"


def test_get_session_detail_langgraph(mock_dashboard_server):
    server, app = mock_dashboard_server

    with patch("shared.utils.langgraph.session_store.get_session_store") as mock_store_fn:
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store

        mock_store.get_session.return_value = {
            "id": "session-lg-123",
            "appName": "test_agent",
            "userId": "test_user",
            "state": {"step": 1},
            "events": [
                {
                    "id": "evt-1",
                    "author": "user",
                    "timestamp": 1721730000.0,
                    "content": {"role": "user", "parts": [{"text": "How are you?"}]}
                }
            ]
        }

        detail = server._get_session_detail(runtime="langgraph", app_name="test_agent", user_id="test_user", session_id="session-lg-123")

        assert detail is not None
        assert detail["id"] == "session-lg-123"
        assert detail["runtime"] == "langgraph"
        assert len(detail["events"]) == 1


def test_delete_session_langgraph(mock_dashboard_server):
    server, app = mock_dashboard_server

    with patch("shared.utils.langgraph.session_store.get_session_store") as mock_store_fn:
        mock_store = MagicMock()
        mock_store_fn.return_value = mock_store
        mock_store.delete_session.return_value = True

        success = server._delete_session(runtime="langgraph", app_name="test_agent", user_id="test_user", session_id="session-lg-123")
        assert success is True
