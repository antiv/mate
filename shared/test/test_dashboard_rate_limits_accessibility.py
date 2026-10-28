import unittest
from html.parser import HTMLParser
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates


class _AccessibleNamesParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.labels_by_id = {}
        self.wrapped_labels = {}
        self.controls = {}
        self._label_stack = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "label":
            self._label_stack.append({"for": attributes.get("for"), "text": [], "controls": []})
        elif tag in {"input", "select", "textarea"}:
            control_id = attributes.get("id")
            if control_id:
                self.controls[control_id] = {
                    "aria-label": attributes.get("aria-label"),
                }
                if self._label_stack:
                    self._label_stack[-1]["controls"].append(control_id)

    def handle_data(self, data):
        for label in self._label_stack:
            label["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "label" and self._label_stack:
            label = self._label_stack.pop()
            label_text = " ".join(" ".join(label["text"]).split())
            if label["for"]:
                self.labels_by_id[label["for"]] = label_text
            for control_id in label["controls"]:
                self.wrapped_labels[control_id] = label_text


class TestRateLimitsAccessibleNames(unittest.TestCase):
    def test_usage_and_configuration_controls_have_accessible_names(self):
        project_root = Path(__file__).parent.parent.parent
        templates = Jinja2Templates(directory=str(project_root / "templates"))
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/dashboard/rate-limits",
            "headers": [],
            "query_string": b"",
            "session": {},
        }
        request = Request(scope)
        request.state.csp_nonce = "test-nonce"
        response = templates.TemplateResponse(
            request,
            "dashboard/rate_limits.html",
            {"request": request},
        )
        parser = _AccessibleNamesParser()
        parser.feed(response.body.decode("utf-8"))

        expected_names = {
            "usageUserId": "User ID",
            "usageAgentName": "Agent name",
            "usageProjectId": "Project ID",
            "formScope": "Scope",
            "formScopeId": "Scope ID (user_id, agent_name, or project_id)",
            "formRequestsPerMin": "Requests/min",
            "formTokensPerHour": "Tokens/hour",
            "formTokensPerDay": "Tokens/day",
            "formTokensPerMonth": "Tokens/month (project)",
            "formUsdPerDay": "USD/day",
            "formUsdPerMonth": "USD/month",
            "formMaxTokensPerRequest": "Max tokens/request (agent)",
            "formActionOnLimit": "Action on limit",
            "formBlockUnpriced": "Treat calls without a price as over the dollar budget",
            "formIncludeSubAgents": "Include sub-agents (agent scope): the agent's token and dollar budgets count what its sub-agents use too",
        }
        for control_id, expected_name in expected_names.items():
            with self.subTest(control_id=control_id):
                control = parser.controls[control_id]
                actual_name = (
                    control["aria-label"]
                    or parser.labels_by_id.get(control_id)
                    or parser.wrapped_labels.get(control_id)
                )
                self.assertEqual(actual_name, expected_name)


if __name__ == "__main__":
    unittest.main()
