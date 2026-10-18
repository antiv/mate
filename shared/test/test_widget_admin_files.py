#!/usr/bin/env python3
"""
The widget admin file routes only reach the stores of the key's own agent.

A widget admin key belongs to one agent of one project. The file routes used to
take any store name for an upload and delete any file search document by id, so
one widget's admin key could change another project's files.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import widget_routes as wr
from shared.utils.models import FileSearchDocument, FileSearchStore

ADMIN = {"X-Widget-Key": "wak_secret"}
OWN_STORE = "fileSearchStores/own"
OTHER_STORE = "fileSearchStores/other-project"


class _Key:
    id = 1
    admin_key = "wak_secret"
    agent_name = "support_agent"
    project_id = 1

    def get_allowed_origins(self):
        return None


class WidgetFilesTestCase(unittest.TestCase):

    def setUp(self):
        self.service = MagicMock()
        self.service.get_stores_for_agent.return_value = [{"store_name": OWN_STORE}]
        self.service.delete_document.return_value = True
        self.upload = MagicMock(return_value={"success": True, "document_name": "doc-1"})
        self.db = MagicMock()
        patchers = [
            patch.object(wr, "_lookup_widget_admin_key",
                         side_effect=lambda k: _Key() if k == _Key.admin_key else None),
            patch.object(wr, "get_database_client", return_value=self.db),
            patch("shared.utils.file_search_service.FileSearchService", return_value=self.service),
            patch("shared.utils.tools.file_search_tools.upload_file_to_store", self.upload),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(wr.admin_api_router)
        self.client = TestClient(app)

    def _stored_file(self, store_name):
        """The database holds document 5 in a store named store_name."""
        doc = MagicMock(id=5, store_id=9, document_name="doc-5")
        store = MagicMock(id=9, store_name=store_name)
        session = MagicMock()

        def query(model):
            q = MagicMock()
            q.filter_by.return_value.first.return_value = doc if model is FileSearchDocument else store
            return q
        session.query.side_effect = query
        self.db.get_session.return_value = session

    def _upload(self, store_name):
        return self.client.post("/widget/api/files/upload", headers=ADMIN,
                                data={"store_name": store_name},
                                files={"file": ("notes.txt", b"hello", "text/plain")})


class TestUpload(WidgetFilesTestCase):

    def test_own_store_is_accepted(self):
        resp = self._upload(OWN_STORE)
        self.assertEqual(resp.status_code, 200)
        self.upload.assert_called_once()
        self.assertEqual(self.upload.call_args.kwargs["store_name"], OWN_STORE)

    def test_another_agents_store_is_refused(self):
        resp = self._upload(OTHER_STORE)
        self.assertEqual(resp.status_code, 404)
        self.upload.assert_not_called()


class TestDelete(WidgetFilesTestCase):

    def test_file_in_own_store_is_deleted(self):
        self._stored_file(OWN_STORE)
        resp = self.client.delete("/widget/api/files/5", headers=ADMIN)
        self.assertEqual(resp.json(), {"success": True})
        self.service.delete_document.assert_called_once_with(OWN_STORE, "doc-5")

    def test_file_in_another_agents_store_is_not_found(self):
        self._stored_file(OTHER_STORE)
        resp = self.client.delete("/widget/api/files/5", headers=ADMIN)
        self.assertEqual(resp.json(), {"success": False, "error": "File not found"})
        self.service.delete_document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
