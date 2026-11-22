import unittest
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates


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
        # "sidebar-active" alone is in base.html's <style>, so check the link's own class
        self.assertRegex(html, r'href="/dashboard/integrations" class="[^"]*sidebar-active')

    def test_a_control_room_path_does_not_render_the_integrations_item(self):
        html = self._render("/dashboard/overview")
        self.assertNotIn('href="/dashboard/integrations"', html)


class TestControlRoomSidebarByScope(unittest.TestCase):
    """Control Room groups its pages by scope: Workspace, then Platform (dashboard-2.1.md)."""

    PLATFORM_PAGES = ("/dashboard/workspaces", "/dashboard/users", "/dashboard/settings",
                      "/dashboard/migrations")
    WORKSPACE_PAGES = ("/dashboard/approvals", "/dashboard/members", "/dashboard/models",
                       "/dashboard/rate-limits", "/dashboard/alerts")

    def setUp(self):
        project_root = Path(__file__).parent.parent.parent
        self.templates = Jinja2Templates(directory=str(project_root / "templates"))

    def _render(self, platform_admin=True, wizard=False, shop=False):
        self.templates.env.globals["is_platform_admin"] = lambda request: platform_admin
        self.templates.env.globals["features_in_use"] = lambda: {"wizard": wizard, "shop": shop}
        scope = {"type": "http", "http_version": "1.1", "method": "GET",
                 "path": "/dashboard/overview", "headers": [], "query_string": b"", "session": {}}
        request = Request(scope)
        request.state.csp_nonce = "test-nonce"
        response = self.templates.TemplateResponse(
            request, "base.html", {"request": request, "username": "tester", "is_admin": True})
        html = response.body.decode("utf-8", "ignore")
        return html[html.index('<aside id="sidebar"'):html.index("</aside>")]

    @staticmethod
    def _section(html, header):
        """The links of one sidebar section: from its header to the next one."""
        start = html.index(f'<p class="sidebar-section-header">{header}</p>')
        end = html.find('<p class="sidebar-section-header">', start + 1)
        foot = html.find("mate-nav-foot", start)
        ends = [i for i in (end, foot) if i != -1]
        return html[start:min(ends) if ends else len(html)]

    def test_each_page_is_under_its_scope(self):
        html = self._render()
        workspace, platform = self._section(html, "Workspace"), self._section(html, "Platform")
        for page in self.WORKSPACE_PAGES:
            with self.subTest(page=page):
                self.assertIn(f'href="{page}"', workspace)
                self.assertNotIn(f'href="{page}"', platform)
        for page in self.PLATFORM_PAGES:
            with self.subTest(page=page):
                self.assertIn(f'href="{page}"', platform)
                self.assertNotIn(f'href="{page}"', workspace)
        for old in ("Governance", "Wizard &amp; Commerce", ">System<", "Platform Overview"):
            self.assertNotIn(old, html)

    def test_a_workspace_admin_sees_no_platform_section(self):
        html = self._render(platform_admin=False, wizard=True, shop=True)
        self.assertNotIn('<p class="sidebar-section-header">Platform</p>', html)
        for page in self.PLATFORM_PAGES + ("/dashboard/wizard-leads", "/dashboard/shop-orders"):
            self.assertNotIn(f'href="{page}"', html)
        for page in self.WORKSPACE_PAGES + ("/dashboard/overview", "/dashboard/docs"):
            self.assertIn(f'href="{page}"', html)

    def test_the_wizard_and_shop_pages_show_only_when_in_use(self):
        wizard_pages = ('href="/dashboard/wizard-leads"', 'href="/dashboard/wizard-pricing"')
        shop_page = 'href="/dashboard/shop-orders"'
        html = self._render()
        for page in wizard_pages + (shop_page,):
            self.assertNotIn(page, html)
        html = self._render(wizard=True)
        for page in wizard_pages:
            self.assertIn(page, self._section(html, "Platform"))
        self.assertNotIn(shop_page, html)
        html = self._render(shop=True)
        self.assertIn(shop_page, self._section(html, "Platform"))
        self.assertNotIn(wizard_pages[0], html)


if __name__ == "__main__":
    unittest.main()
