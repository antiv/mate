#!/usr/bin/env python3
"""
Unit tests for PDF and text attachment preprocessing and model capability validation.
"""

import asyncio
import unittest
import sys
import os
import base64
import tempfile
import time
from unittest.mock import patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import widget_routes
from server.widget_routes import (
    extract_text_from_pdf_base64,
    extract_text_from_text_base64,
    model_supports_vision,
    prepare_attachments,
)
from shared.utils import pdf_text


class TestModelSupportsVision(unittest.TestCase):
    """
    The contract: refuse only on positive knowledge that a model is text-only.

    These patch LiteLLM rather than asserting its dataset — the map is refreshed
    from upstream, so pinning a test to what it currently says about a given
    model would make the suite fail on someone else's release.
    """

    def _info(self, value):
        return patch("litellm.get_model_info", return_value={"supports_vision": value})

    def test_a_vision_model_is_allowed(self):
        with self._info(True):
            self.assertTrue(model_supports_vision("some-vision-model"))

    def test_a_known_text_only_model_is_refused(self):
        with self._info(False):
            self.assertFalse(model_supports_vision("some-text-model"))

    def test_no_opinion_is_not_a_refusal(self):
        # LiteLLM knows the model but does not populate the field. That is not
        # evidence of anything, so the provider gets to answer.
        with self._info(None):
            self.assertTrue(model_supports_vision("some-model"))

    def test_a_model_litellm_does_not_know_is_allowed(self):
        # A custom or self-hosted endpoint cannot be judged from its name; the
        # substring allowlist this replaced refused every one of them.
        with patch("litellm.get_model_info", side_effect=Exception("not found")):
            self.assertTrue(model_supports_vision("my-self-hosted-thing"))

    def test_unset_model_defaults_to_true(self):
        self.assertTrue(model_supports_vision(""))
        self.assertTrue(model_supports_vision(None))

    def test_a_current_vision_model_is_allowed_for_real(self):
        # One unpatched check, on ids stable enough to rely on. gpt-4-turbo and
        # claude-sonnet-4-5 are the regression: the old heuristic refused both.
        for name in ("gpt-4o", "gpt-4-turbo", "claude-sonnet-4-5"):
            self.assertTrue(model_supports_vision(name), name)


class TestTextExtraction(unittest.TestCase):
    """Test extracting text from base64 files."""

    def test_extract_text_from_text_base64(self):
        original_text = "Hello, this is a plain text file attachment!"
        base64_data = base64.b64encode(original_text.encode("utf-8")).decode("utf-8")
        result = extract_text_from_text_base64(base64_data)
        self.assertEqual(result, original_text)

    def test_extract_text_from_text_base64_invalid(self):
        result = extract_text_from_text_base64("invalid_base64_%%%")
        self.assertTrue(result.startswith("[Error decoding text file:"))


def make_pdf(text: str = "") -> bytes:
    """A one-page PDF with the text."""
    stream = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 100]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % xref


def pdf_part(data: bytes) -> dict:
    return {"filename": "a.pdf", "inline_data": {"mime_type": "application/pdf",
                                                 "data": base64.b64encode(data).decode()}}


class TestPdfText(unittest.TestCase):
    """A PDF is read in a process of its own, for a limited time (shared/utils/pdf_text.py)."""

    def _program(self, code: str):
        """Run code instead of the reader."""
        path = os.path.join(self.enterContext(tempfile.TemporaryDirectory()), "reader.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(code)
        return patch.object(pdf_text, "__file__", path)

    def assert_all_slots_free(self):
        taken = [pdf_text._slots.acquire(blocking=False) for _ in range(pdf_text.MAX_CONCURRENT)]
        for _ in filter(None, taken):
            pdf_text._slots.release()
        self.assertTrue(all(taken))

    def test_the_text_of_a_pdf(self):
        self.assertEqual(pdf_text.pdf_text(make_pdf("Quarterly report")), "Quarterly report")
        self.assertEqual(pdf_text.pdf_text(make_pdf()), "")

    def test_what_is_not_a_pdf_is_an_error(self):
        with self.assertRaises(pdf_text.PdfError):
            pdf_text.pdf_text(b"not a pdf")
        self.assert_all_slots_free()

    def test_one_that_takes_too_long_is_stopped(self):
        with self._program("while True: pass"), patch.object(pdf_text, "TIMEOUT_SECONDS", 1):
            started = time.monotonic()
            with self.assertRaisesRegex(pdf_text.PdfError, "too long"):
                pdf_text.pdf_text(b"%PDF")
        self.assertLess(time.monotonic() - started, 10)
        self.assert_all_slots_free()

    def test_a_reader_that_dies_is_an_error(self):
        with self._program("import os; os._exit(9)"):
            with self.assertRaisesRegex(pdf_text.PdfError, "could not be read"):
                pdf_text.pdf_text(b"%PDF")
        self.assert_all_slots_free()

    def test_the_reader_has_no_environment(self):
        with self._program("import json, os; print(json.dumps({'text': os.environ.get('MATE_TEST_SECRET', 'none')}))"), \
                patch.dict(os.environ, {"MATE_TEST_SECRET": "s3cret"}):
            self.assertEqual(pdf_text.pdf_text(b"%PDF"), "none")

    def test_a_large_file_is_refused_unread(self):
        with patch.object(pdf_text.subprocess, "run") as run:
            with self.assertRaisesRegex(pdf_text.PdfError, "larger than"):
                pdf_text.pdf_text(b"x" * (pdf_text.MAX_BYTES + 1))
            result = extract_text_from_pdf_base64("A" * (pdf_text.MAX_BYTES * 4 // 3 + 8))
        self.assertIn("larger than 10 MB", result)
        run.assert_not_called()

    def test_one_more_at_once_is_refused(self):
        for _ in range(pdf_text.MAX_CONCURRENT):
            self.assertTrue(pdf_text._slots.acquire(blocking=False))
            self.addCleanup(pdf_text._slots.release)
        with patch.object(pdf_text.subprocess, "run") as run:
            with self.assertRaisesRegex(pdf_text.PdfError, "too many"):
                pdf_text.pdf_text(make_pdf("x"))
        run.assert_not_called()

    def test_the_text_is_cut(self):
        with patch.object(pdf_text, "MAX_CHARS", 5):
            self.assertEqual(pdf_text._extract(make_pdf("Quarterly")), "Quart")

    def test_from_an_attachment(self):
        self.assertEqual(extract_text_from_pdf_base64(base64.b64encode(make_pdf("Hello")).decode()), "Hello")
        for bad in ("invalid_pdf_base64_data", None):
            self.assertTrue(extract_text_from_pdf_base64(bad).startswith("[Error extracting text from PDF:"))

    def test_from_google_drive(self):
        from shared.utils.tools import google_drive_tools
        self.assertEqual(google_drive_tools.extract_text_from_pdf(make_pdf("A  report")), "A report")
        self.assertEqual(google_drive_tools.extract_text_from_pdf(make_pdf()), "[No text content found in PDF]")
        self.assertTrue(google_drive_tools.extract_text_from_pdf(b"x").startswith("[Error extracting PDF text:"))


class TestPrepareAttachments(unittest.TestCase):

    def test_a_pdf_becomes_text_unless_the_model_is_gemini(self):
        part = pdf_part(make_pdf("Hello"))
        self.assertEqual(prepare_attachments([part], "openrouter/x/y"),
                         [{"text": "[Attached PDF file 'a.pdf']:\nHello\n"}])
        self.assertEqual(prepare_attachments([part], "gemini-2.5-flash"), [part])

    def test_too_many_pdfs_are_refused_unread(self):
        from fastapi import HTTPException
        parts = [pdf_part(b"%PDF")] * (widget_routes.MAX_PDFS_PER_MESSAGE + 1)
        with patch.object(widget_routes, "extract_text_from_pdf_base64") as extract:
            with self.assertRaises(HTTPException) as caught:
                prepare_attachments(parts, "openrouter/x/y")
        self.assertEqual(caught.exception.status_code, 400)
        extract.assert_not_called()


class TestOffTheEventLoop(unittest.TestCase):
    """Reading attachments must not hold up the server's other requests."""

    def _prepare(self, parts, model_name):
        from fastapi import HTTPException
        try:
            asyncio.get_running_loop()
            self.on_loop = True
        except RuntimeError:
            self.on_loop = False
        raise HTTPException(status_code=418)

    def _client(self):
        import auth_server
        from fastapi.testclient import TestClient
        self.on_loop = None
        return TestClient(auth_server.app)

    def test_in_the_widget(self):
        import auth_server
        key = MagicMock(id=1, agent_name="a")
        key.get_widget_config.return_value = {}
        auth_server.app.dependency_overrides[widget_routes.verify_widget_key] = lambda: key
        self.addCleanup(auth_server.app.dependency_overrides.pop, widget_routes.verify_widget_key)
        with patch.object(widget_routes, "prepare_attachments", self._prepare):
            response = self._client().post("/widget/api/chat", json={
                "session_id": "s", "user_id": "u", "parts": [pdf_part(b"%PDF")]})
        self.assertEqual((response.status_code, self.on_loop), (418, False), response.text)

    def test_in_the_work_room(self):
        from shared.test.test_cross_workspace import A, _platform_admin
        with patch.object(widget_routes, "prepare_attachments", self._prepare):
            response = self._client().post("/run_sse", headers=_platform_admin(A), json={
                "app_name": "a", "user_id": "u", "session_id": "s",
                "new_message": {"role": "user", "parts": [pdf_part(b"%PDF")]}})
        self.assertEqual((response.status_code, self.on_loop), (418, False), response.text)


if __name__ == "__main__":
    unittest.main()
