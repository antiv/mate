"""
The secrets in an agent's mcp_servers_config and tool_config (shared/utils/config_secrets.py):
hidden from whoever reads the config, kept when the hidden form is saved, and never sent
anywhere else.
"""

import copy
import json
import unittest
from unittest.mock import patch

from shared.test.test_cross_workspace import A, A_ROOT, _platform_admin, _TwoWorkspaces
from shared.utils import config_secrets
from shared.utils.config_secrets import HiddenValue, mask, require_config, restore
from shared.utils.models import AgentConfig, AgentConfigVersion

S = "__stored__"
SECRET = "Zk81mQpX0rT5vB2nL7wYc3"

SERVERS = {"mcpServers": {
    "remote": {
        "url": "https://mcp.example.com/mcp",
        "headers": {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json", "X-Env": "${TOKEN}"},
        "timeout": 60,
    },
    "local": {
        "command": "npx",
        "args": ["-y", "mcp-remote", "https://mcp.example.com/sse", "--header", f"Authorization: Bearer {SECRET}"],
        "env": {"API_KEY": SECRET, "FROM_SERVER": "${API_KEY}"},
    },
}}
TOOLS = {
    "google_search": True,
    "memory_blocks": {"enabled": True, "max_tokens": 500},
    "require_confirmation": {
        "tools": ["issue_refund"], "approver": "inbox",
        "notify": {"type": "slack", "url": f"https://hooks.slack.com/services/T0/B0/{SECRET}"},
    },
}


class TestMask(unittest.TestCase):

    def test_what_an_mcp_server_hides(self):
        masked = mask("mcp_servers_config", SERVERS)["mcpServers"]
        self.assertEqual(masked["remote"]["headers"], {
            "Authorization": f"Bearer {S}", "Content-Type": "application/json", "X-Env": "${TOKEN}"})
        self.assertEqual((masked["remote"]["url"], masked["remote"]["timeout"]),
                         ("https://mcp.example.com/mcp", 60))
        self.assertEqual(masked["local"]["args"],
                         ["-y", "mcp-remote", "https://mcp.example.com/sse", "--header", f"Authorization: Bearer {S}"])
        self.assertEqual(masked["local"]["env"], {"API_KEY": S, "FROM_SERVER": "${API_KEY}"})
        self.assertNotIn(SECRET, json.dumps(masked))

    def test_arguments(self):
        for args, expected in [
            (["--api-key", "abc"], ["--api-key", S]),
            (["--api-key=abc"], [f"--api-key={S}"]),
            (["--api-key=${KEY}"], ["--api-key=${KEY}"]),
            (["-e", "DB_PASS=abc"], ["-e", f"DB_PASS={S}"]),
            (["-H", "X-Key: abc"], ["-H", f"X-Key: {S}"]),
            (["--header=X-Key:abc"], [f"--header=X-Key:{S}"]),
            ([SECRET], [S]),
            (["postgresql://bob:hunter2@db/app"], [f"postgresql://bob:{S}@db/app"]),
            (["https://x.example/mcp?api_key=abc&page=2"], [f"https://x.example/mcp?api_key={S}&page=2"]),
            ([f"https://x.example/s/{SECRET}/mcp"], [f"https://x.example/s/{S}/mcp"]),
            (["https://${USER_KEY}@x.example/"], ["https://${USER_KEY}@x.example/"]),
            (["-y", "@modelcontextprotocol/server-github", "--port", "8080", "/srv/data", 3],
             ["-y", "@modelcontextprotocol/server-github", "--port", "8080", "/srv/data", 3]),
        ]:
            with self.subTest(args=args):
                masked = mask("mcp_servers_config", {"mcpServers": {"s": {"command": "npx", "args": args}}})
                self.assertEqual(masked["mcpServers"]["s"]["args"], expected)

    def test_parts_the_runtimes_decode_once_more(self):
        servers = {"mcpServers": {"s": {"url": "https://x.example",
                                        "headers": json.dumps({"X-Api": SECRET}), "args": json.dumps(["--token", "a"])}}}
        masked = mask("mcp_servers_config", servers)["mcpServers"]["s"]
        self.assertEqual(json.loads(masked["headers"]), {"X-Api": S})
        self.assertEqual(json.loads(masked["args"]), ["--token", S])

    def test_what_a_tool_config_hides(self):
        masked = mask("tool_config", TOOLS)
        self.assertEqual(masked["require_confirmation"]["notify"], {"type": "slack", "url": f"https://hooks.slack.com/{S}"})
        masked["require_confirmation"]["notify"]["url"] = TOOLS["require_confirmation"]["notify"]["url"]
        self.assertEqual(masked, TOOLS)
        self.assertEqual(mask("tool_config", {"x": {"api_key": "a", "url": "https://x.example/d/1aB2c3D4e5F6g7H8i9J0k"}}),
                         {"x": {"api_key": S, "url": "https://x.example/d/1aB2c3D4e5F6g7H8i9J0k"}})

    def test_text_stays_text_and_unchanged_without_a_secret(self):
        self.assertEqual(json.loads(mask("mcp_servers_config", json.dumps(SERVERS))), mask("mcp_servers_config", SERVERS))
        for value in ('{ "mcpServers":{} }', "not json", "", None, '{"mcpServers": {"s": {"url": "https://x.example"}}}'):
            self.assertEqual(mask("mcp_servers_config", value), value)


    def test_where_a_trigger_and_an_alert_send(self):
        for url, expected in [
            (f"https://hooks.slack.com/services/T0/B0/{SECRET}", f"https://hooks.slack.com/{S}"),
            ("https://x.example/hook?key=1", f"https://x.example/{S}"),
            ("https://x.example?key=1", f"https://x.example/{S}"),
            ("https://bob:pw@x.example:8443/a", f"https://x.example:8443/{S}"),
            ("https://x.example/", "https://x.example/"), ("https://x.example", "https://x.example"),
            ("not an address", S), ("", ""),
        ]:
            for field in ("output_config", "destination_config"):
                with self.subTest(url=url, field=field):
                    self.assertEqual(mask(field, {"url": url, "headers": {"X-Key": "a", "Accept": "b"}, "timeout": 5}),
                                     {"url": expected, "headers": {"X-Key": S, "Accept": "b"}, "timeout": 5})
        self.assertEqual(mask("output_config", {"to": "a@b.c", "subject": "s", "label": "l"}),
                         {"to": "a@b.c", "subject": "s", "label": "l"})


class TestRestore(unittest.TestCase):

    def test_what_was_hidden_comes_back(self):
        for field, stored in (("mcp_servers_config", SERVERS), ("tool_config", TOOLS)):
            for value in (stored, json.dumps(stored)):
                restored, left = restore(field, mask(field, value), value)
                self.assertEqual((json.loads(restored) if isinstance(restored, str) else restored, left), (stored, []))

    def test_beside_an_edit(self):
        edited = mask("mcp_servers_config", SERVERS)
        edited["mcpServers"]["remote"]["timeout"] = 5
        edited["mcpServers"]["local"]["env"]["NEW"] = "typed"
        restored, left = restore("mcp_servers_config", edited, SERVERS)
        self.assertEqual(left, [])
        expected = copy.deepcopy(SERVERS)
        expected["mcpServers"]["remote"]["timeout"] = 5
        expected["mcpServers"]["local"]["env"]["NEW"] = "typed"
        self.assertEqual(restored, expected)

    def test_a_new_value_replaces_the_stored_one(self):
        edited = mask("mcp_servers_config", SERVERS)
        edited["mcpServers"]["remote"]["headers"]["Authorization"] = "Bearer new"
        restored, _ = restore("mcp_servers_config", edited, SERVERS)
        self.assertEqual(restored["mcpServers"]["remote"]["headers"]["Authorization"], "Bearer new")

    def test_not_to_another_address(self):
        def change(server, **values):
            edited = mask("mcp_servers_config", SERVERS)
            edited["mcpServers"][server].update(values)
            return edited
        for edited in (change("remote", url="https://evil.example"),
                       change("local", command="sh"),
                       change("local", args=["-c", "env"])):
            restored, left = restore("mcp_servers_config", edited, SERVERS)
            self.assertTrue(left)
            changed, = [n for n in ("remote", "local") if edited["mcpServers"][n] != mask("mcp_servers_config", SERVERS)["mcpServers"][n]]
            self.assertNotIn(SECRET, json.dumps(restored["mcpServers"][changed]))

    def test_nor_to_another_name_place_or_kind_of_text(self):
        masked = mask("mcp_servers_config", SERVERS)["mcpServers"]
        for edited in ({"evil": masked["remote"]},
                       {"remote": {**masked["remote"], "headers": {"X-Other": f"Bearer {S}"}}},
                       {"remote": {**masked["remote"], "headers": {"Authorization": S}}},
                       {"remote": {"url": "https://mcp.example.com/mcp", "note": f"Bearer {S}"}}):
            restored, left = restore("mcp_servers_config", {"mcpServers": edited}, SERVERS)
            self.assertTrue(left, edited)
            self.assertNotIn(SECRET, json.dumps(restored))

    def test_a_notification_s_headers_go_with_its_address(self):
        stored = {"require_confirmation": {"tools": ["a"], "notify": {
            "type": "http", "url": "https://x.example", "headers": {"X-Key": SECRET}}}}
        edited = mask("tool_config", stored)
        edited["require_confirmation"]["notify"]["url"] = "https://evil.example"
        restored, left = restore("tool_config", edited, stored)
        self.assertEqual((restored["require_confirmation"]["notify"]["headers"], len(left)), ({"X-Key": ""}, 1))

    def test_without_a_stored_config_nothing_can_be_kept(self):
        for stored in (None, "", "not json"):
            config = {"mcp_servers_config": json.dumps(mask("mcp_servers_config", SERVERS))}
            with self.assertRaisesRegex(HiddenValue, "mcpServers.remote.headers.Authorization"):
                require_config(config, {"mcp_servers_config": stored})
        with self.assertRaises(HiddenValue):
            require_config({"mcp_servers_config": '{"mcpServers": __stored__'}, {"mcp_servers_config": json.dumps(SERVERS)})

    def test_a_config_without_hidden_values_is_left_alone(self):
        config = {"mcp_servers_config": '{ "mcpServers": {} }', "tool_config": None, "name": "a"}
        before = dict(config)
        require_config(config, None)
        self.assertEqual(config, before)


class TestTriggersAndAlerts(_TwoWorkspaces):
    URL = f"https://hooks.example.com/services/{SECRET}"
    CONFIG = {"url": URL, "headers": {"Authorization": f"Bearer {SECRET}"}}
    SHOWN = {"url": f"https://hooks.example.com/{S}", "headers": {"Authorization": f"Bearer {S}"}}

    def _row(self, model, row_id):
        session = self.Session()
        try:
            return session.get(model, row_id)
        finally:
            session.close()

    def _request(self, method, path, body=None):
        return getattr(self.client, method)(path, headers=_platform_admin(A), **({"json": body} if body else {}))

    def _check(self, path, kind, model, stored, field, create):
        made = self._request("post", path, {**create, field: self.CONFIG})
        self.assertEqual(made.status_code, 200, made.text)
        row_id = made.json()[kind]["id"]
        self.assertEqual(made.json()[kind][field], self.SHOWN)
        self.assertNotIn(SECRET, self._request("get", path).text)
        # As shown, with something else changed
        kept = self._request("put", f"{path}/{row_id}", {**create, "name": "renamed", field: self.SHOWN})
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertEqual(getattr(self._row(model, row_id), stored)(), self.CONFIG)
        # To another host
        moved = self._request("put", f"{path}/{row_id}",
                              {**create, field: {**self.SHOWN, "url": "https://evil.example/x"}})
        self.assertEqual(moved.status_code, 400, moved.text)
        self.assertIn("headers.Authorization", moved.json()["detail"])
        self.assertEqual(getattr(self._row(model, row_id), stored)(), self.CONFIG)
        # Nothing to keep in a new one
        self.assertEqual(self._request("post", path, {**create, "name": "two", field: self.SHOWN}).status_code, 400)
        replaced = self._request("put", f"{path}/{row_id}", {**create, field: {"url": "https://new.example/y"}})
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(getattr(self._row(model, row_id), stored)(), {"url": "https://new.example/y"})
        return row_id

    def test_a_trigger(self):
        from shared.utils.models import AgentTrigger
        with patch("shared.utils.trigger_runner.TriggerRunner.sync_cron_jobs"):
            self._check("/dashboard/api/triggers", "trigger", AgentTrigger, "get_output_config", "output_config", {
                "name": "t", "trigger_type": "cron", "cron_expression": "0 9 * * *", "agent_name": A_ROOT,
                "project_id": self.ids["project_a"], "prompt": "p", "output_type": "http_callback"})

    def test_an_alert_rule(self):
        from shared.utils.models import AlertRule
        self._check("/dashboard/api/alert-rules", "alert_rule", AlertRule, "get_destination_config",
                    "destination_config", {
                        "name": "r", "scope": "agent", "scope_id": A_ROOT, "condition_type": "agent_error_count",
                        "condition_config": {"threshold": 1, "window_minutes": 5}, "destination_type": "http"})

    def test_an_imported_trigger_has_no_hidden_value(self):
        import auth_server
        from shared.utils.models import AgentTrigger
        server = auth_server.app.state.dashboard_server
        session = self.Session()
        from shared.utils.workspaces import agent_workspace
        workspace = agent_workspace(session, A_ROOT)
        session.close()
        result = server._import_agent_configs({"agents": [], "triggers": [{
            "name": "imported", "agent_name": A_ROOT, "output_type": "http_callback", "output_config": self.SHOWN}]},
            workspace_id=workspace)
        self.assertIn("imported without its hidden values", " ".join(result["errors"]))
        session = self.Session()
        trigger = session.query(AgentTrigger).filter_by(name="imported").one()
        self.assertEqual(trigger.get_output_config(), {"url": "", "headers": {"Authorization": ""}})
        session.close()

    def test_a_failed_delivery_does_not_name_the_address(self):
        import httpx
        from shared.utils import notify
        for handler, expected in [
            (lambda request: httpx.Response(404), "HTTP 404"),
            (lambda request: (_ for _ in ()).throw(httpx.ConnectError(f"no route to {self.URL}")), "ConnectError"),
        ]:
            client = httpx.Client(transport=httpx.MockTransport(handler))
            with patch.object(notify.httpx, "Client", return_value=client), \
                    self.assertLogs("shared.utils.notify", "INFO") as logs:
                ok, detail = notify.post_json(self.URL, {})
            self.assertFalse(ok)
            self.assertIn(expected, detail)
            self.assertNotIn(SECRET, detail + " ".join(logs.output))


class TestInTheDashboard(_TwoWorkspaces):

    def setUp(self):
        super().setUp()
        self._store(mcp_servers_config=json.dumps(SERVERS), tool_config=json.dumps(TOOLS))

    def _store(self, **values):
        session = self.Session()
        session.query(AgentConfig).filter_by(name=A_ROOT).update(values)
        session.commit()
        session.close()

    def _stored(self, name=A_ROOT):
        session = self.Session()
        try:
            return session.query(AgentConfig).filter_by(name=name).one().to_dict()
        finally:
            session.close()

    def _put(self, **values):
        row = self._stored()
        form = {"name": A_ROOT, "type": "llm", "project_id": str(row["project_id"]), "description": "d",
                "instruction": "i", "model_api_key": S, **values}
        with patch("httpx.AsyncClient.post"):
            return self.client.put(f"/dashboard/api/agents/{row['id']}", headers=_platform_admin(A), data=form)

    def _listed(self):
        agents = self.client.get("/dashboard/api/agents", headers=_platform_admin(A))
        self.assertNotIn(SECRET, agents.text)
        found = agents.json()
        found = found if isinstance(found, list) else found.get("agents") or found.get("configs")
        return next(a for a in found if a["name"] == A_ROOT)

    def test_no_page_or_list_shows_them(self):
        for path in ("/dashboard/agents", f"/dashboard/agents/{A_ROOT}", "/dashboard/agents/visual",
                     f"/dashboard/agents/new?from={A_ROOT}", "/dashboard/api/agents",
                     f"/dashboard/api/agents/export?root_agent={A_ROOT}"):
            with self.subTest(path=path):
                response = self.client.get(path, headers=_platform_admin(A))
                self.assertEqual(response.status_code, 200, response.text[:200])
                self.assertNotIn(SECRET, response.text)
        self.assertIn(S, self._listed()["mcp_servers_config"])

    def test_saving_what_the_list_gave_keeps_them(self):
        listed = self._listed()
        response = self._put(mcp_servers_config=listed["mcp_servers_config"], tool_config=listed["tool_config"])
        self.assertTrue(response.json().get("success"), response.text)
        row = self._stored()
        self.assertEqual(json.loads(row["mcp_servers_config"]), SERVERS)
        self.assertEqual(json.loads(row["tool_config"]), TOOLS)

    def test_the_versions_hide_them_and_a_rollback_restores_them(self):
        self._put(mcp_servers_config=json.dumps(SERVERS), tool_config=json.dumps(TOOLS))
        row = self._stored()
        versions = self.client.get(f"/dashboard/api/agents/{row['id']}/versions", headers=_platform_admin(A))
        self.assertEqual(versions.status_code, 200)
        self.assertNotIn(SECRET, versions.text)
        self._put(mcp_servers_config="", tool_config="")
        session = self.Session()
        first = session.query(AgentConfigVersion.id).filter_by(agent_config_id=row["id"]).order_by(
            AgentConfigVersion.version_number).first()[0]
        session.close()
        answer = self.client.post(f"/dashboard/api/agents/{row['id']}/rollback/{first}", headers=_platform_admin(A))
        self.assertTrue(answer.json().get("success"), answer.text)
        self.assertNotIn(SECRET, answer.text)
        self.assertEqual(json.loads(self._stored()["mcp_servers_config"]), SERVERS)

    def test_another_address_is_refused_and_nothing_saved(self):
        edited = json.loads(self._listed()["mcp_servers_config"])
        edited["mcpServers"]["remote"]["url"] = "https://evil.example"
        response = self._put(mcp_servers_config=json.dumps(edited))
        self.assertEqual(response.status_code, 400)
        self.assertIn("mcpServers.remote.headers.Authorization", response.json()["detail"])
        self.assertEqual(json.loads(self._stored()["mcp_servers_config"]), SERVERS)

    def test_a_copy_asks_for_them(self):
        listed = self._listed()
        response = self.client.post("/dashboard/api/agents", headers=_platform_admin(A), data={
            "name": "ws_a_copy", "type": "llm", "project_id": str(listed["project_id"]), "description": "d",
            "instruction": "i", "mcp_servers_config": listed["mcp_servers_config"]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Enter the hidden value again", response.json()["detail"])

    def _export(self):
        return self.client.get(f"/dashboard/api/agents/export?root_agent={A_ROOT}", headers=_platform_admin(A)).json()

    def _import(self, data, overwrite=True):
        import auth_server
        server = auth_server.app.state.dashboard_server
        return server._import_agent_configs(data, overwrite=overwrite, workspace_id=self._workspace_of_a())

    def _workspace_of_a(self):
        from shared.utils.workspaces import agent_workspace
        session = self.Session()
        try:
            return agent_workspace(session, A_ROOT)
        finally:
            session.close()

    def test_importing_an_export_over_the_agent_keeps_them(self):
        self._store(model_api_key="sk-own")
        export = self._export()
        result = self._import({"agents": export["agents"]})
        self.assertEqual(result.get("errors"), [], result)
        row = self._stored()
        self.assertEqual(json.loads(row["mcp_servers_config"]), SERVERS)
        self.assertEqual(row["model_api_key"], "sk-own")

    def test_importing_it_as_a_new_agent_leaves_them_empty_and_says_so(self):
        agent = {**self._export()["agents"][0], "name": "ws_a_imported", "parent_agents": []}
        result = self._import({"agents": [agent]}, overwrite=False)
        self.assertIn("imported without its hidden values", " ".join(result["errors"]))
        row = self._stored("ws_a_imported")
        self.assertNotIn(S, row["mcp_servers_config"] + row["tool_config"])
        self.assertEqual(json.loads(row["mcp_servers_config"])["mcpServers"]["local"]["env"]["API_KEY"], "")

    def test_a_template_s_new_agent_gets_none_of_the_hidden_values(self):
        import auth_server
        server = auth_server.app.state.dashboard_server
        template = self._export()["agents"][0]
        self.assertTrue(server._create_agent_config({
            "name": "ws_a_from_template", "type": "llm", "project_id": template["project_id"], "description": "d",
            "instruction": "i", "model_api_key": S, "mcp_servers_config": template["mcp_servers_config"],
            "tool_config": json.loads(template["tool_config"])}))
        row = self._stored("ws_a_from_template")
        self.assertIsNone(row["model_api_key"])
        self.assertNotIn(S, row["mcp_servers_config"] + row["tool_config"])

    def test_a_standalone_build_has_them(self):
        import auth_server
        server = auth_server.app.state.dashboard_server
        build = server._export_agent_configs(root_agent=A_ROOT, for_build=True)
        self.assertIn(SECRET, json.dumps(build))

    def test_a_template_s_hidden_value_is_the_project_agent_s(self):
        from shared.utils.dashboard.dashboard_server import DashboardServer
        row = type("Row", (), {"mcp_servers_config": json.dumps(SERVERS)})
        value = DashboardServer._template_value("mcp_servers_config", mask("mcp_servers_config", json.dumps(SERVERS)),
                                                row, lambda text: text)
        self.assertEqual(json.loads(value), SERVERS)

    def test_an_agent_that_edits_agents_never_sees_them_and_keeps_them(self):
        from types import SimpleNamespace
        from shared.utils.database_client import get_database_client
        from shared.utils.tools import create_agent_tool
        context = SimpleNamespace(agent_name=A_ROOT)
        with patch.object(create_agent_tool, "_is_admin_user", return_value=True), \
                patch.object(create_agent_tool, "reload_agent_cache", return_value={"success": True}), \
                patch.object(create_agent_tool, "get_database_client", get_database_client):
            read = create_agent_tool.read_agent(tool_context=context)
            self.assertNotIn(SECRET, json.dumps(read, default=str))
            listed = self._listed()["mcp_servers_config"]
            edited = json.loads(listed)
            edited["mcpServers"]["remote"]["url"] = "https://evil.example"
            refused = create_agent_tool.update_agent(A_ROOT, mcp_servers_config=json.dumps(edited), tool_context=context)
            self.assertEqual(refused["status"], "error", refused)
            self.assertIn("hidden value", refused["error_message"])
            kept = create_agent_tool.update_agent(A_ROOT, mcp_servers_config=listed, tool_context=context)
        self.assertEqual(kept["status"], "success", kept)
        self.assertEqual(json.loads(self._stored()["mcp_servers_config"]), SERVERS)


if __name__ == "__main__":
    unittest.main()
