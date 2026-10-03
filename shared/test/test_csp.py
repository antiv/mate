#!/usr/bin/env python3
"""
Tests for the Content-Security-Policy (#124).

The policy limits where scripts, styles and connections may come from, so an
injected script cannot load code from another host or send data to one. It
ships as Report-Only, with violations posted to /csp-report.

The widget chat page is the one page other sites frame, so its frame-ancestors
come from the widget key's origin allowlist. That list is written by whoever
holds the key's admin key, so entries are rebuilt from their parsed parts, not
pasted into the header: a value like "https://a.com; script-src *" must not
become a directive.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from server import csp
from server import widget_routes as wr


def _directives(policy):
    out = {}
    for part in policy.split(";"):
        name, _, value = part.strip().partition(" ")
        out[name] = value.split()
    return out


def _app():
    app = FastAPI()
    app.middleware("http")(csp.add_csp_header)
    app.include_router(csp.report_router)

    @app.get("/page")
    def page():
        return HTMLResponse("<p>hi</p>")

    @app.get("/data")
    def data():
        return JSONResponse({"a": 1})

    @app.get("/own")
    def own():
        return csp.set_csp_header(HTMLResponse("<p>widget</p>"), "*")

    return TestClient(app)


class TestPolicy(unittest.TestCase):

    def test_scripts_are_limited_to_self_and_the_cdns_in_use(self):
        d = _directives(csp.build_policy())
        self.assertEqual(d["default-src"], ["'self'"])
        self.assertIn("'self'", d["script-src"])
        self.assertIn("https://cdn.jsdelivr.net", d["script-src"])
        self.assertNotIn("*", d["script-src"])
        self.assertNotIn("https:", d["script-src"])
        # WebAssembly for Pyodide, but never eval() of JavaScript
        self.assertIn("'wasm-unsafe-eval'", d["script-src"])
        self.assertNotIn("'unsafe-eval'", d["script-src"])
        self.assertNotIn("*", d["connect-src"])

    def test_the_dashboard_may_only_be_framed_by_itself(self):
        d = _directives(csp.build_policy())
        self.assertEqual(d["frame-ancestors"], ["'self'"])
        self.assertEqual(d["object-src"], ["'none'"])
        self.assertEqual(d["base-uri"], ["'self'"])
        self.assertEqual(d["report-uri"], ["/csp-report"])

    def test_extra_sources_are_added_where_code_and_connections_load(self):
        with patch.dict(os.environ, {"CSP_EXTRA_SOURCES": "https://cdn.example.com, https://*.example.org"}):
            d = _directives(csp.build_policy())
        for name in ("script-src", "style-src", "font-src", "connect-src", "frame-src"):
            self.assertIn("https://cdn.example.com", d[name], name)
            self.assertIn("https://*.example.org", d[name], name)

    def test_an_extra_source_cannot_add_a_directive(self):
        with patch.dict(os.environ, {"CSP_EXTRA_SOURCES": "https://ok.example; script-src * 'unsafe-eval'"}):
            policy = csp.build_policy()
        names = [part.strip().split(" ", 1)[0] for part in policy.split(";")]
        self.assertEqual(len(names), len(set(names)), "a directive was added twice")
        self.assertNotIn("'unsafe-eval'", policy)
        self.assertNotIn("*", _directives(policy)["script-src"])
        self.assertNotIn("https://ok.example;", policy)


class TestMiddleware(unittest.TestCase):

    def test_report_only_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CSP_MODE", None)
            r = _app().get("/page")
        self.assertIn(csp.CSP_REPORT_ONLY_HEADER, r.headers)
        self.assertNotIn(csp.CSP_HEADER, r.headers)

    def test_enforce(self):
        with patch.dict(os.environ, {"CSP_MODE": "enforce"}):
            r = _app().get("/page")
        self.assertIn(csp.CSP_HEADER, r.headers)
        self.assertNotIn(csp.CSP_REPORT_ONLY_HEADER, r.headers)

    def test_off(self):
        with patch.dict(os.environ, {"CSP_MODE": "off"}):
            r = _app().get("/page")
        self.assertNotIn(csp.CSP_HEADER, r.headers)
        self.assertNotIn(csp.CSP_REPORT_ONLY_HEADER, r.headers)

    def test_an_unknown_mode_falls_back_to_report_only(self):
        with patch.dict(os.environ, {"CSP_MODE": "enforced"}):
            r = _app().get("/page")
        self.assertIn(csp.CSP_REPORT_ONLY_HEADER, r.headers)

    def test_only_adks_dev_ui_may_eval(self):
        app = FastAPI()
        app.middleware("http")(csp.add_csp_header)

        @app.get("/dev-ui/")
        def dev_ui():
            return HTMLResponse("<p>adk</p>")

        @app.get("/dev-uix")
        def lookalike():
            return HTMLResponse("<p>not adk</p>")

        client = TestClient(app)
        with patch.dict(os.environ, {"ADK_DEV_UI": "false"}):
            policy = client.get("/dev-ui/").headers[csp.CSP_REPORT_ONLY_HEADER]
        self.assertNotIn("'unsafe-eval'", _directives(policy)["script-src"],
                         "no eval when the dev UI is not served")
        with patch.dict(os.environ, {"ADK_DEV_UI": "true"}):
            policy = client.get("/dev-ui/").headers[csp.CSP_REPORT_ONLY_HEADER]
        self.assertIn("'unsafe-eval'", _directives(policy)["script-src"])
        policy = client.get("/dev-uix").headers[csp.CSP_REPORT_ONLY_HEADER]
        self.assertNotIn("'unsafe-eval'", _directives(policy)["script-src"])
        policy = _app().get("/page").headers[csp.CSP_REPORT_ONLY_HEADER]
        self.assertNotIn("'unsafe-eval'", _directives(policy)["script-src"])

    def test_only_html_gets_a_policy(self):
        r = _app().get("/data")
        self.assertNotIn(csp.CSP_REPORT_ONLY_HEADER, r.headers)

    def test_a_page_with_its_own_policy_keeps_it(self):
        r = _app().get("/own")
        self.assertEqual(_directives(r.headers[csp.CSP_REPORT_ONLY_HEADER])["frame-ancestors"], ["*"])


class TestWidgetFrameAncestors(unittest.TestCase):

    def test_no_allowlist_may_be_embedded_anywhere(self):
        self.assertEqual(csp.widget_frame_ancestors(None), "*")

    def test_allowlist_entries_become_sources(self):
        value = csp.widget_frame_ancestors([
            "https://shop.example.com", "https://shop.example.com/", "*.partner.example",
            "http://localhost:8000", "https://app.example.com:443", "HTTPS://Upper.Example.com"])
        self.assertEqual(value.split(), ["'self'", "https://shop.example.com", "*.partner.example",
                                         "http://localhost:8000", "https://app.example.com",
                                         "https://upper.example.com"])

    def test_entries_that_are_not_plain_origins_are_dropped(self):
        value = csp.widget_frame_ancestors([
            "https://a.example; script-src *",
            "https://a.example 'unsafe-inline'",
            "javascript:alert(1)",
            "https://a.example/path",
            "https://user@a.example",
            "data:text/html,x",
            "*",
            "",
        ])
        self.assertEqual(value, "'self'")

    def test_an_empty_or_malformed_allowlist_allows_only_self(self):
        self.assertEqual(csp.widget_frame_ancestors([]), "'self'")
        self.assertEqual(csp.widget_frame_ancestors("https://a.example"), "'self'")


class _Key:
    def __init__(self, origins):
        self.id = 1
        self.agent_name = "test_agent"
        self._origins = origins

    def get_allowed_origins(self):
        return self._origins

    def get_widget_config(self):
        return {}


class TestWidgetChatPage(unittest.TestCase):

    def _get(self, origins):
        app = FastAPI()
        app.middleware("http")(csp.add_csp_header)
        app.include_router(wr.router)
        with patch.object(wr, "_lookup_widget_key", return_value=_Key(origins)), \
                patch.object(wr, "_agent_disclosure", return_value="AI"):
            return TestClient(app, base_url="http://mate.local").get(
                "/widget/chat?key=wk", headers={"Referer": "https://shop.example.com/"})

    def test_frame_ancestors_follow_the_allowlist(self):
        r = self._get(["https://shop.example.com"])
        self.assertEqual(r.status_code, 200)
        d = _directives(r.headers[csp.CSP_REPORT_ONLY_HEADER])
        self.assertEqual(d["frame-ancestors"], ["'self'", "https://shop.example.com"])
        # The rest is the dashboard's policy
        self.assertIn("https://cdn.jsdelivr.net", d["script-src"])

    def test_without_an_allowlist_any_site_may_frame_it(self):
        r = self._get(None)
        self.assertEqual(_directives(r.headers[csp.CSP_REPORT_ONLY_HEADER])["frame-ancestors"], ["*"])


class TestReports(unittest.TestCase):

    def setUp(self):
        csp._seen_reports.clear()
        self.addCleanup(csp._seen_reports.clear)
        self.client = _app()

    def _post(self, body, content_type="application/csp-report"):
        return self.client.post(csp.REPORT_PATH, content=body, headers={"Content-Type": content_type})

    def test_a_violation_is_logged_once_without_query_strings(self):
        body = ('{"csp-report": {"document-uri": "https://mate.example/widget/chat?key=wk_secret",'
                ' "effective-directive": "script-src-elem",'
                ' "blocked-uri": "https://evil.example/x.js?token=t0ken"}}')
        with self.assertLogs("server.csp", level="WARNING") as logs:
            self.assertEqual(self._post(body).status_code, 204)
            self.assertEqual(self._post(body).status_code, 204)
            csp.logger.warning("end")
        self.assertEqual(len(logs.output), 2)
        line = logs.output[0]
        self.assertIn("script-src-elem blocked 'https://evil.example/x.js' on /widget/chat", line)
        self.assertNotIn("wk_secret", line)
        self.assertNotIn("t0ken", line)

    def test_reporting_api_format(self):
        body = ('[{"type": "csp-violation", "body": {"documentURL": "https://mate.example/dashboard",'
                ' "effectiveDirective": "connect-src", "blockedURL": "https://evil.example/"}}]')
        with self.assertLogs("server.csp", level="WARNING") as logs:
            self.assertEqual(self._post(body, "application/reports+json").status_code, 204)
        self.assertIn("connect-src blocked 'https://evil.example/' on /dashboard", logs.output[0])

    def test_control_characters_cannot_forge_log_lines(self):
        body = ('{"csp-report": {"document-uri": "https://m.example/a", "effective-directive": "img-src",'
                ' "blocked-uri": "https://x.example/\\nWARNING forged line"}}')
        with self.assertLogs("server.csp", level="WARNING") as logs:
            self._post(body)
        self.assertNotIn("\n", logs.output[0])

    def test_oversized_and_malformed_bodies_are_refused(self):
        self.assertEqual(self._post("x" * (csp.MAX_REPORT_BYTES + 1)).status_code, 413)
        self.assertEqual(self._post("not json").status_code, 400)
        self.assertEqual(self._post('"a string"').status_code, 204)

    def test_distinct_reports_are_capped(self):
        with patch.object(csp, "MAX_DISTINCT_REPORTS", 2), \
                self.assertLogs("server.csp", level="WARNING") as logs:
            for i in range(5):
                self._post('{"csp-report": {"document-uri": "https://m.example/a",'
                           f' "effective-directive": "img-src", "blocked-uri": "https://x{i}.example/"}}}}')
        self.assertEqual(len(logs.output), 3)  # two reports and one notice that the rest are dropped
        self.assertIn("ignoring further ones", logs.output[2])


if __name__ == "__main__":
    unittest.main()
