import unittest

from fastapi import Request
from fastapi.templating import Jinja2Templates
from pathlib import Path


class TestDashboardSidebarActiveSpace(unittest.TestCase):
    """The Integrations page lives under the Studio space (issue #237)."""

    def setUp(self):
        project_root = Path(__file__).parent.parent.parent
        self.templates = Jinja2Templates(directory=str(project_root / "templates"))

    def _render(self, path):
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "session": {},
        }
        request = Request(scope)
        request.state.csp_nonce = "test-nonce"
        response = self.templates.TemplateResponse(
            request, "base.html", {"request": request, "username": "tester"}
        )
        return response.body.decode("utf-8", "ignore")

    def test_integrations_renders_the_studio_sidebar_with_integrations_active(self):
        html = self._render("/dashboard/integrations")
        # The studio sidebar only renders when active_space == 'studio'; before the
        # fix /dashboard/integrations fell through to the control room, so the
        # Integrations item was absent entirely.
        self.assertIn('href="/dashboard/integrations"', html)
        self.assertIn("sidebar-active", html)

    def test_a_control_room_path_does_not_render_the_integrations_item(self):
        html = self._render("/dashboard/overview")
        self.assertNotIn('href="/dashboard/integrations"', html)


if __name__ == "__main__":
    unittest.main()
