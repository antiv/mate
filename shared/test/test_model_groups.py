#!/usr/bin/env python3
"""
Model groups: which models may be in one (shared/utils/model_groups.py), and the
platform administrator's routes (server/model_group_routes.py). That nobody else gets
in is test_cross_workspace.py's and test_route_inventory.py's.
"""

import json
import os
import unittest
from typing import Any, Dict, List
from unittest.mock import patch

import httpx

from shared.test.test_cross_workspace import A, B_MARKER, _platform_admin, _TwoWorkspaces
from shared.utils.model_groups import (
    MAX_MODELS, ModelGroupError, check_model_name, check_models, group_of, known_abilities, own_server_refusal,
)
from shared.utils.models import AgentConfig, AgentConfigVersion, AuditLog

COSTS = {
    "gpt-4o": {"litellm_provider": "openai", "supports_function_calling": True, "supports_vision": True},
    "gemini/gemini-2.5-flash": {"litellm_provider": "gemini", "supports_function_calling": True, "supports_vision": True},
    "deepseek/deepseek-chat": {"litellm_provider": "deepseek", "supports_function_calling": True},
    "openai/o1-mini": {"litellm_provider": "openai", "supports_function_calling": False},
    "ollama/llama3.1": {"litellm_provider": "ollama", "supports_function_calling": True},
}
UNKNOWN = "openrouter/deepseek/deepseek-chat-v3.1"


def _addresses(**hosts: str):
    """getaddrinfo() that knows these hosts (dots written as __) and IP addresses."""
    import socket
    known = {host.replace("__", "."): ip for host, ip in hosts.items()}

    def lookup(host: str, port: Any) -> List[tuple]:
        if host in known:
            return [(socket.AF_INET, 1, 6, "", (ip, 0)) for ip in known[host].split()]
        if host.replace(".", "").isdigit():
            return [(socket.AF_INET, 1, 6, "", (host, 0))]
        raise socket.gaierror(host)
    return patch("shared.utils.model_groups.socket.getaddrinfo", side_effect=lookup)


class _Costs(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        costs = patch("litellm.model_cost", COSTS)
        costs.start()
        self.addCleanup(costs.stop)


class TestModels(_Costs):

    def test_a_group_s_name_is_told_from_a_model_s(self) -> None:
        self.assertEqual(group_of(" group/coding "), "coding")
        for name in (None, "", "gemini-2.5-flash", "openai/gpt-4o", "openrouter/group/x"):
            self.assertIsNone(group_of(name), name)

    def test_names(self) -> None:
        for name in ("gemini-2.5-flash", "models/gemini-2.5-flash", "openai/gpt-4o", UNKNOWN, "ollama_chat/qwen3:8b",
                     "lm_studio/anything", "llamacpp/anything", "gemma-of-tomorrow"):
            self.assertEqual(check_model_name(f" {name} "), name)
        for name in (None, "", 3, "a b", "x" * 256, "group/coding", "nobody/model", "gpt-4o", "openai/", "ollama/"):
            with self.assertRaises(ModelGroupError, msg=name):
                check_model_name(name)
        with self.assertRaisesRegex(ModelGroupError, "openai/gpt-4o"):
            check_model_name("gpt-4o")

    def test_what_litellm_knows(self) -> None:
        self.assertEqual(known_abilities("openai/gpt-4o"), {"tools": True, "images": True})
        self.assertEqual(known_abilities("gemini-2.5-flash"), {"tools": True, "images": True})
        self.assertEqual(known_abilities("openai/o1-mini"), {"tools": False, "images": None})
        self.assertEqual(known_abilities("deepseek/deepseek-chat"), {"tools": True, "images": None})
        self.assertEqual(known_abilities(UNKNOWN), {"tools": None, "images": None})
        # The same name at another provider is another model
        self.assertEqual(known_abilities("groq/gpt-4o"), {"tools": None, "images": None})

    def test_a_model_does_what_its_group_needs(self) -> None:
        self.assertEqual(check_models([{"model": "openai/gpt-4o"}], ["tools", "images"], False),
                         [{"model": "openai/gpt-4o", "confirmed": []}])
        with self.assertRaisesRegex(ModelGroupError, "does not call tools"):
            check_models([{"model": "openai/o1-mini", "confirmed": ["tools"]}], ["tools"], False)
        self.assertTrue(check_models([{"model": "openai/o1-mini"}], [], False))
        with self.assertRaisesRegex(ModelGroupError, "confirm it"):
            check_models([{"model": UNKNOWN}], ["tools"], False)
        self.assertEqual(check_models([{"model": UNKNOWN, "confirmed": ["tools"]}], ["tools"], False),
                         [{"model": UNKNOWN, "confirmed": ["tools"]}])
        # What LiteLLM knows needs nobody's word
        self.assertEqual(check_models([{"model": "openai/gpt-4o", "confirmed": ["tools"]}], ["tools"], False),
                         [{"model": "openai/gpt-4o", "confirmed": []}])

    def test_bad_lists(self) -> None:
        twice = [{"model": "openai/gpt-4o"}, {"model": " openai/gpt-4o "}]
        many = [{"model": f"openai/m{i}"} for i in range(MAX_MODELS + 1)]
        for value in (None, "openai/gpt-4o", ["openai/gpt-4o"], twice, many,
                      [{"model": "openai/gpt-4o", "confirmed": "tools"}],
                      [{"model": "openai/gpt-4o", "confirmed": ["cooking"]}]):
            with self.assertRaises(ModelGroupError, msg=value):
                check_models(value, [], False)


class TestOwnServers(_Costs):

    def test_by_the_endpoint_s_address(self) -> None:
        with _addresses(localhost="127.0.0.1", ollama="172.18.0.2", api__example__com="93.184.216.34",
                        both="10.0.0.5 93.184.216.34"), patch.dict(os.environ, clear=True):
            self.assertIsNone(own_server_refusal("ollama_chat/qwen3:8b"))
            self.assertIsNone(own_server_refusal("lm_studio/qwen"))
            for url, own in (("http://ollama:11434", True), ("http://10.0.0.5:11434", True),
                             ("https://api.example.com", False), ("http://both:1", False),
                             ("http://gone:11434", False), ("not a url", False)):
                with patch.dict(os.environ, {"OLLAMA_API_BASE": url}):
                    self.assertEqual(own_server_refusal("ollama/qwen3:8b") is None, own, url)

    def test_a_provider_s_own_endpoint_is_never_own(self) -> None:
        with _addresses(), patch.dict(os.environ, {"OPENROUTER_BASE_URL": "http://10.0.0.5/v1"}):
            for name in (UNKNOWN, "gemini-2.5-flash", "gemini/gemini-2.5-flash", "anthropic/claude-opus-5"):
                self.assertIn("own endpoint", own_server_refusal(name))

    def test_openai_s_only_when_every_variable_names_an_own_server(self) -> None:
        own, other = "http://10.0.0.5/v1", "https://1.1.1.1/v1"
        for env, refused in (({}, True), ({"OPENAI_BASE_URL": own}, False), ({"OPENAI_API_BASE": own}, False),
                             ({"OPENAI_BASE_URL": own, "OPENAI_API_BASE": other}, True)):
            with _addresses(), patch.dict(os.environ, env, clear=True):
                self.assertEqual(own_server_refusal("openai/qwen") is not None, refused, env)

    def test_ollama_s_cloud_models(self) -> None:
        with _addresses(localhost="127.0.0.1"), patch.dict(os.environ, clear=True):
            for name in ("ollama_chat/gpt-oss:120b-cloud", "ollama/glm-4.6:cloud", "ollama_chat/kimi-k2:1T-CLOUD",
                         "ollama_chat/some-cloud"):
                self.assertIn("Ollama's servers", own_server_refusal(name), name)
            self.assertIsNone(own_server_refusal("ollama_chat/cloudy:7b"))

    def test_the_group_takes_no_other(self) -> None:
        with _addresses(localhost="127.0.0.1"), patch.dict(os.environ, clear=True):
            self.assertTrue(check_models([{"model": "ollama/llama3.1"}], ["tools"], True))
            with self.assertRaisesRegex(ModelGroupError, "server of your own"):
                check_models([{"model": "ollama/llama3.1"}, {"model": "openai/gpt-4o"}], ["tools"], True)


class TestRoutes(_TwoWorkspaces):
    API = "/dashboard/api/platform/model-groups"

    def setUp(self) -> None:
        super().setUp()
        for patcher in (patch("litellm.model_cost", COSTS), patch("server.model_group_routes.httpx.post")):
            mock = patcher.start()
            self.addCleanup(patcher.stop)
        self.reload = mock

    def _call(self, method: str, path: str = "", **kwargs: Any) -> httpx.Response:
        return self.client.request(method, self.API + path, headers=_platform_admin(A), **kwargs)

    def _ok(self, method: str, path: str = "", **kwargs: Any) -> Dict[str, Any]:
        response = self._call(method, path, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _refused(self, status: int, method: str, path: str = "", **kwargs: Any) -> str:
        response = self._call(method, path, **kwargs)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()["detail"]

    def _create(self, name: str = "coding", **body: Any) -> Dict[str, Any]:
        return self._ok("POST", json={"name": name, "label": name.title(),
                                      "models": [{"model": "openai/gpt-4o"}], **body})["group"]

    def _group(self, name: str) -> Dict[str, Any]:
        return next(g for g in self._ok("GET")["groups"] if g["name"] == name)

    def _names_the_group(self, group: str) -> List[str]:
        """Has an agent of each workspace name it."""
        names = ["ws_a_root", f"{B_MARKER.replace('-', '_')}_root"]
        session = self.Session()
        try:
            for agent in session.query(AgentConfig).filter(AgentConfig.name.in_(names)):
                agent.model_name = f"group/{group}"
            session.commit()
        finally:
            session.close()
        return sorted(names)

    def _audit(self, action: str) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            return [{"resource": r.resource_id, "actor": r.actor, "workspace": r.workspace_id,
                     "details": json.loads(r.details or "{}")}
                    for r in session.query(AuditLog).filter_by(action=action).order_by(AuditLog.id)]
        finally:
            session.close()

    def test_made_changed_and_deleted(self) -> None:
        group = self._create(needs_tools=True, description="Code")
        self.assertEqual((group["name"], group["label"], group["needs_tools"], group["needs_images"], group["agents"]),
                         ("coding", "Coding", True, False, []))
        self.assertEqual(group["models"], [{"model": "openai/gpt-4o", "confirmed": [],
                                            "known": {"tools": True, "images": True}}])
        order = [{"model": UNKNOWN, "confirmed": ["tools"]}, {"model": "openai/gpt-4o"}]
        self._ok("PUT", "/coding", json={"models": order, "label": "Code"})
        self.assertEqual([m["model"] for m in self._group("coding")["models"]], [UNKNOWN, "openai/gpt-4o"])
        self._ok("PUT", "/coding", json={"label": "Code"})  # changes nothing: no entry
        self._ok("DELETE", "/coding")
        self.assertEqual(self._ok("GET")["groups"], [])
        self._refused(404, "PUT", "/coding", json={"label": "x"})
        self._refused(404, "DELETE", "/coding")

        self.assertEqual(self._audit("model_group.create")[0]["details"]["new"]["models"],
                         [{"model": "openai/gpt-4o", "confirmed": []}])
        update, = self._audit("model_group.update")
        self.assertEqual([m["model"] for m in update["details"]["old"]["models"]], ["openai/gpt-4o"])
        self.assertEqual([m["model"] for m in update["details"]["new"]["models"]], [UNKNOWN, "openai/gpt-4o"])
        self.assertEqual(self._audit("model_group.delete")[0]["details"]["old"]["label"], "Code")

    def test_refused_when_made(self) -> None:
        self._create()
        self.assertIn("this name", self._refused(409, "POST", json={"name": "coding", "label": "x"}))
        for name in ("", "Coding", "a b", "group/x", "-a", "a" * 41, None, 5):
            self._refused(400, "POST", json={"name": name, "label": "x"})
        for body in ({}, {"label": ""}, {"label": "x" * 101}, {"label": "x", "description": "x" * 501},
                     {"label": "x", "needs_tools": "yes"}, {"label": "x", "models": "openai/gpt-4o"},
                     {"label": "x", "models": [{"model": "group/coding"}]},
                     {"label": "x", "needs_images": True, "models": [{"model": UNKNOWN}]}):
            self._refused(400, "POST", json={"name": "other", **body})
        self.assertEqual(len(self._ok("GET")["groups"]), 1)

    def test_a_need_added_later_is_checked_against_its_models(self) -> None:
        self._create(models=[{"model": "deepseek/deepseek-chat"}])
        self.assertIn("read images", self._refused(400, "PUT", "/coding", json={"needs_images": True}))
        self.assertFalse(self._group("coding")["needs_images"])

    def test_its_agents_of_every_workspace(self) -> None:
        self._create()
        names = self._names_the_group("coding")
        agents = self._group("coding")["agents"]
        self.assertEqual(sorted(a["name"] for a in agents), names)
        self.assertEqual(len({a["workspace"] for a in agents}), 2)

    def test_a_group_in_use_keeps_a_model_and_is_not_deleted(self) -> None:
        self._create()
        self._names_the_group("coding")
        self._refused(409, "PUT", "/coding", json={"models": []})
        self.assertIn("2 agent(s)", self._refused(409, "DELETE", "/coding"))
        self._create("empty", models=[])
        for target, status in (("empty", 400), ("coding", 400), ("none", 404)):
            self._refused(status, "DELETE", f"/coding?move_to={target}")
        self.assertEqual(len(self._group("coding")["agents"]), 2)

    def test_deleted_with_its_agents_moved(self) -> None:
        self._create()
        self._create("standard")
        names = self._names_the_group("coding")
        self.assertEqual(self._ok("DELETE", "/coding?move_to=standard"), {"deleted": "coding", "agents_moved": 2})
        self.assertEqual(sorted(a["name"] for a in self._group("standard")["agents"]), names)
        session = self.Session()
        try:
            versions = session.query(AgentConfigVersion).filter_by(change_type="update").all()
            self.assertEqual([v.get_snapshot().get("model_name") for v in versions if v.changed_by], ["group/standard"] * 2)
        finally:
            session.close()
        entries = self._audit("agent.update")
        self.assertEqual(sorted(e["resource"] for e in entries), names)
        self.assertEqual(len({e["workspace"] for e in entries}), 2)
        details = self._audit("model_group.delete")[0]["details"]
        self.assertEqual((details["agents_moved_to"], sorted(details["agents"])), ("standard", names))
        self.assertEqual(self.reload.call_count, 2)

    def test_agents_don_t_leave_own_servers_by_the_way(self) -> None:
        local = {"needs_tools": True, "own_servers_only": True, "models": [{"model": "ollama/llama3.1"}]}
        with _addresses(localhost="127.0.0.1"), patch.dict(os.environ, {"OLLAMA_API_BASE": ""}):
            self._create("local", **local)
            self._create("standard")
            self._names_the_group("local")
            self._refused(409, "PUT", "/local", json={"own_servers_only": False})
            self._refused(400, "PUT", "/local", json={"models": [{"model": "openai/gpt-4o"}]})
            self._refused(409, "DELETE", "/local?move_to=standard")
            self.assertEqual(len(self._group("local")["agents"]), 2)
            self._create("local-two", **local)
            self._ok("DELETE", "/local?move_to=local-two")
            self._ok("PUT", "/local-two", json={"label": "Still own"})

    def test_what_is_known_of_a_model(self) -> None:
        with _addresses(), patch.dict(os.environ, clear=False):
            found = self._ok("GET", "/check?model=%20openai/gpt-4o")
            self.assertEqual((found["model"], found["known"]), ("openai/gpt-4o", {"tools": True, "images": True}))
            self._refused(400, "GET", "/check?model=nobody/model")


class TestSeeds(unittest.TestCase):
    def test_the_six_are_the_same_on_every_database(self) -> None:
        import re
        root = os.path.join(os.path.dirname(__file__), "..", "sql", "migrations")
        rows = []
        for dialect in ("sqlite", "postgresql", "mysql"):
            with open(os.path.join(root, dialect, "V053__add_model_groups.sql"), encoding="utf-8") as f:
                sql = f.read()
            rows.append([(n, l, d, *(x in ("1", "TRUE") for x in flags.split(", ")), models) for n, l, d, flags, models
                         in re.findall(r"\('([a-z]+)', '([^']+)', '([^']+)', ([^']+), '(\[\])'\)", sql)])
        self.assertEqual([r[0] for r in rows[0]], ["standard", "fast", "reasoning", "coding", "vision", "local"])
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(rows[0], rows[2])


if __name__ == "__main__":
    unittest.main()
