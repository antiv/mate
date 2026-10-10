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

from fastapi import FastAPI, Request
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

    @app.get("/nonced")
    def nonced(request: Request):
        # What a template does with {{ request.state.csp_nonce }}
        return HTMLResponse(f'<script nonce="{request.state.csp_nonce}"></script>')

    @app.get("/own-nonced")
    def own_nonced(request: Request):
        return csp.set_csp_header(
            HTMLResponse(f'<script nonce="{request.state.csp_nonce}"></script>'), "*", request)

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


class TestStrictPolicy(unittest.TestCase):

    def test_a_nonce_replaces_unsafe_inline_for_scripts_only(self):
        d = _directives(csp.build_policy(nonce="abc"))
        self.assertIn("'nonce-abc'", d["script-src"])
        self.assertNotIn("'unsafe-inline'", d["script-src"])
        self.assertIn("'wasm-unsafe-eval'", d["script-src"])
        self.assertIn("https://cdn.jsdelivr.net", d["script-src"])
        # Styles keep it: Tailwind's CDN injects <style>, and a style cannot run code
        self.assertIn("'unsafe-inline'", d["style-src"])

    def test_report_only_mode_reports_against_the_strict_policy(self):
        with patch.dict(os.environ, {"CSP_MODE": "report-only"}):
            r = _app().get("/page")
        script_src = _directives(r.headers[csp.CSP_REPORT_ONLY_HEADER])["script-src"]
        self.assertNotIn("'unsafe-inline'", script_src)
        self.assertNotIn(csp.CSP_HEADER, r.headers)

    def test_the_header_carries_the_nonce_the_page_was_rendered_with(self):
        client = _app()
        nonces = set()
        for path in ("/nonced", "/own-nonced"):
            for _ in range(2):
                r = client.get(path)
                page_nonce = r.text.split('nonce="')[1].split('"')[0]
                self.assertIn(f"'nonce-{page_nonce}'",
                              _directives(r.headers[csp.CSP_REPORT_ONLY_HEADER])["script-src"], path)
                nonces.add(page_nonce)
        self.assertEqual(len(nonces), 4, "each response gets its own nonce")
        for nonce in nonces:
            self.assertGreaterEqual(len(nonce), 20)

    def test_a_policy_without_a_request_still_gets_a_nonce(self):
        r = _app().get("/own")
        script_src = _directives(r.headers[csp.CSP_REPORT_ONLY_HEADER])["script-src"]
        self.assertTrue(any(s.startswith("'nonce-") for s in script_src))
        self.assertNotIn("'unsafe-inline'", script_src)


class TestMiddleware(unittest.TestCase):

    def test_report_only_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CSP_MODE", None)
            r = _app().get("/page")
        self.assertIn(csp.CSP_REPORT_ONLY_HEADER, r.headers)
        self.assertNotIn(csp.CSP_HEADER, r.headers)

    def test_enforce(self):
        # Until every page is off inline handlers: enforce the legacy policy,
        # and report what the strict one would still block
        with patch.dict(os.environ, {"CSP_MODE": "enforce"}):
            r = _app().get("/page")
        enforced = _directives(r.headers[csp.CSP_HEADER])["script-src"]
        reported = _directives(r.headers[csp.CSP_REPORT_ONLY_HEADER])["script-src"]
        self.assertIn("'unsafe-inline'", enforced)
        self.assertFalse(any(s.startswith("'nonce-") for s in enforced))
        self.assertNotIn("'unsafe-inline'", reported)
        self.assertTrue(any(s.startswith("'nonce-") for s in reported))

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
        # ADK's markup is not ours to put nonces on: it keeps the legacy policy
        self.assertIn("'unsafe-inline'", _directives(policy)["script-src"])
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
    """frame-ancestors lets through the sites _check_origin lets through."""

    def _ancestors(self, origins, strict=True):
        return csp.widget_frame_ancestors(origins, strict).split()

    def test_no_allowlist_may_be_embedded_anywhere(self):
        self.assertEqual(self._ancestors(None), ["*"])

    def test_without_strict_origins_any_site_may_embed_it(self):
        # WIDGET_ORIGIN_STRICT off only logs a foreign embedder, so framing must
        # not block it either once the CSP is enforced
        self.assertEqual(self._ancestors(["https://shop.example.com"], strict=False), ["*"])

    def test_allowlist_entries_become_sources(self):
        self.assertEqual(self._ancestors([
            "https://shop.example.com", "https://shop.example.com/", "http://localhost:8000",
            "https://app.example.com:443", "https://app.example.com:8443", "HTTPS://Upper.Example.com"]),
            ["'self'", "https://shop.example.com", "http://localhost:8000", "https://app.example.com",
             "https://app.example.com:8443", "https://upper.example.com"])

    def test_a_scheme_wildcard_covers_the_domain_itself(self):
        # _origin_matches("https://example.com", "https://*.example.com") is true
        self.assertEqual(self._ancestors(["https://*.example.com"]),
                         ["'self'", "https://*.example.com", "https://example.com"])

    def test_a_bare_wildcard_covers_any_scheme_and_port(self):
        # _origin_matches ignores scheme and port for a bare *.example.com
        self.assertEqual(self._ancestors(["*.partner.example"]),
                         ["'self'", "https://*.partner.example:*", "http://*.partner.example:*",
                          "https://partner.example:*", "http://partner.example:*"])

    def test_entries_that_are_not_plain_origins_are_dropped(self):
        self.assertEqual(self._ancestors([
            "https://a.example; script-src *",
            "https://a.example 'unsafe-inline'",
            "javascript:alert(1)",
            "https://a.example/path",
            "https://user@a.example",
            "data:text/html,x",
            "https://[::1]:8443",
            "*",
            "*.",
            "",
        ]), ["'self'"])

    def test_entries_that_are_not_strings_are_skipped(self):
        self.assertEqual(self._ancestors(["https://a.example", 5, None, {"x": 1}]),
                         ["'self'", "https://a.example"])

    def test_an_empty_or_malformed_allowlist_allows_only_self(self):
        self.assertEqual(self._ancestors([]), ["'self'"])
        self.assertEqual(self._ancestors("https://a.example"), ["'self'"])


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

    def _get(self, origins, strict=True, referer="https://shop.example.com/", key=_Key):
        app = FastAPI()
        app.middleware("http")(csp.add_csp_header)
        app.include_router(wr.router)
        with patch.object(wr, "_lookup_widget_key", return_value=key(origins) if key else None), \
                patch.object(wr, "_agent_disclosure", return_value="AI"), \
                patch.object(wr, "ORIGIN_STRICT", strict):
            return TestClient(app, base_url="http://mate.local").get(
                "/widget/chat?key=wk", headers={"Referer": referer})

    def _ancestors(self, response):
        return _directives(response.headers[csp.CSP_REPORT_ONLY_HEADER])["frame-ancestors"]

    def test_frame_ancestors_follow_the_allowlist_when_strict(self):
        r = self._get(["https://shop.example.com"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._ancestors(r), ["'self'", "https://shop.example.com"])
        # The rest is the dashboard's policy
        self.assertIn("https://cdn.jsdelivr.net", _directives(r.headers[csp.CSP_REPORT_ONLY_HEADER])["script-src"])

    def test_without_strict_origins_any_site_may_frame_it(self):
        self.assertEqual(self._ancestors(self._get(["https://shop.example.com"], strict=False)), ["*"])

    def test_without_an_allowlist_any_site_may_frame_it(self):
        self.assertEqual(self._ancestors(self._get(None)), ["*"])

    def test_error_pages_may_be_framed_so_their_message_shows(self):
        refused = self._get(["https://shop.example.com"], referer="https://evil.example/")
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self._ancestors(refused), ["*"])
        unknown = self._get(None, key=None)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(self._ancestors(unknown), ["*"])


class TestReports(unittest.TestCase):

    def setUp(self):
        self._reset()
        self.addCleanup(self._reset)
        self.client = _app()

    @staticmethod
    def _reset():
        csp._seen_reports.clear()
        csp._window.update(started=0.0, dropped=0)

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

    def _report(self, i):
        return ('{"csp-report": {"document-uri": "https://m.example/a",'
                f' "effective-directive": "img-src", "blocked-uri": "https://x{i}.example/"}}}}')

    def test_logging_is_capped_per_window_not_for_good(self):
        # Anyone can post reports, so a flood of fake ones must not silence real
        # ones until restart: the next window logs again and says what it dropped
        clock = [1000.0]
        with patch.object(csp, "MAX_REPORTS_PER_WINDOW", 2), \
                patch.object(csp.time, "monotonic", side_effect=lambda: clock[0]), \
                self.assertLogs("server.csp", level="WARNING") as logs:
            for i in range(5):
                self._post(self._report(i))
            self.assertEqual(len(logs.output), 2)
            clock[0] += csp.REPORT_WINDOW_SECONDS
            self._post(self._report("real"))
        self.assertIn("3 more violation report(s) were not logged", logs.output[2])
        self.assertIn("https://xreal.example/", logs.output[3])

    def test_a_large_body_is_refused_before_it_is_read(self):
        import asyncio
        read = []

        class _Request:
            headers = {}

            async def stream(self):
                for _ in range(100):
                    read.append(1)
                    yield b"x" * 1024

        self.assertIsNone(asyncio.run(csp._read_capped(_Request())))
        self.assertLessEqual(len(read), csp.MAX_REPORT_BYTES // 1024 + 1, "read past the cap")
        # A declared length over the cap is refused without reading at all
        r = self.client.post(csp.REPORT_PATH, content=b"{}",
                             headers={"Content-Type": "application/csp-report",
                                      "Content-Length": str(csp.MAX_REPORT_BYTES + 1)})
        self.assertEqual(r.status_code, 413)

    def test_fields_that_are_not_strings_are_ignored(self):
        body = '{"csp-report": {"effective-directive": ["x"], "blocked-uri": {"a": 1}, "document-uri": 5}}'
        self.assertEqual(self._post(body).status_code, 204)

    def test_deeply_nested_json_is_a_bad_request(self):
        body = "[" * 100000 + "]" * 100000
        with patch.object(csp, "MAX_REPORT_BYTES", 300000):
            self.assertEqual(self._post(body).status_code, 400)

    def test_unicode_line_breaks_cannot_forge_log_lines(self):
        body = ('{"csp-report": {"document-uri": "https://m.example/a", "effective-directive": "img-src",'
                ' "blocked-uri": "https://x.example/\\u2028WARNING forged\\u0085line"}}')
        with self.assertLogs("server.csp", level="WARNING") as logs:
            self._post(body)
        for ch in ("\u2028", "\u0085"):
            self.assertNotIn(ch, logs.output[0])


if __name__ == "__main__":
    unittest.main()


class TestConvertedTemplates(unittest.TestCase):
    """Pages moved off inline handlers must stay off them.

    The strict policy blocks on*= attributes and inline scripts without the
    nonce. Add a template here once it is converted; the last step of #124
    is when this list covers every template.
    """

    CONVERTED = [
        "base.html",
        "login.html",
        "dashboard/index.html",
        "dashboard/agents.html",
        "dashboard/agents_visual.html",
        "dashboard/modals/agent_modal_macro.html",
        "dashboard/modals/config_modals.html",
        "dashboard/modals/edit_instruction_modal.html",
        "dashboard/modals/file_search_modal.html",
        "dashboard/modals/import_agents_modal.html",
        "dashboard/modals/memory_blocks_modal.html",
        "dashboard/modals/parent_agents_modal.html",
        "dashboard/modals/project_modal.html",
        "dashboard/modals/save_template_modal.html",
        "dashboard/modals/template_sync_modal.html",
        "dashboard/modals/version_history_modal.html",
        "dashboard/modals/widget_keys_modal.html",
    ]
    # Scripts that build HTML: the markup they generate must not have handlers either
    CONVERTED_JS = [
        "agent-management.js",
        "modals/file-search.js",
        "modals/memory-blocks.js",
        "modals/version-history.js",
        "modals/widget-keys.js",
    ]
    _TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                              "templates")

    def _read(self, name):
        with open(os.path.join(self._TEMPLATES, name), encoding="utf-8") as f:
            return f.read()

    def test_no_inline_event_handlers(self):
        import re
        for name in self.CONVERTED:
            found = re.findall(r"\son[a-z]+\s*=", self._read(name))
            self.assertEqual(found, [], name)

    def test_inline_scripts_carry_the_nonce(self):
        import re
        for name in self.CONVERTED:
            for tag in re.findall(r"<script\b[^>]*>", self._read(name)):
                if "src=" in tag or 'type="application/json"' in tag:
                    continue
                self.assertIn('nonce="{{ request.state.csp_nonce }}"', tag, f"{name}: {tag}")

    def test_no_handlers_in_generated_markup(self):
        import re
        static = os.path.join(os.path.dirname(self._TEMPLATES), "static", "js")
        for name in self.CONVERTED_JS:
            with open(os.path.join(static, name), encoding="utf-8") as f:
                found = re.findall(r"\son[a-z]+=[\"']", f.read())
            self.assertEqual(found, [], name)

    def test_no_javascript_urls(self):
        for name in self.CONVERTED:
            self.assertNotIn("javascript:", self._read(name), name)
