#!/usr/bin/env python3
"""SSO sign-in sends PKCE (S256) to both providers.

The login route's redirect carries a code challenge, and the callback's token
request carries the verifier it was made from, read back from the session.
"""

import base64
import hashlib
import os
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from server import oauth_routes

PROVIDERS = {
    "GOOGLE_CLIENT_ID": "google-id", "GOOGLE_CLIENT_SECRET": "google-secret",
    "GITHUB_CLIENT_ID": "github-id", "GITHUB_CLIENT_SECRET": "github-secret",
}


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class TestOAuthPkce(unittest.TestCase):

    def setUp(self):
        self._env = patch.dict(os.environ, PROVIDERS)
        self._env.start()
        oauth_routes._oauth = None

    def tearDown(self):
        oauth_routes._oauth = None
        self._env.stop()

    def test_both_providers_use_s256(self):
        oauth = oauth_routes._get_oauth()
        for provider in ("google", "github"):
            client = getattr(oauth, provider)
            self.assertEqual(client.client_kwargs.get("code_challenge_method"), "S256", provider)

    def test_github_sign_in_sends_the_verifier_of_its_challenge(self):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-secret")
        app.include_router(oauth_routes.router)
        client = TestClient(app)

        resp = client.get("/auth/login/github", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        query = parse_qs(urlparse(resp.headers["location"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        challenge, state = query["code_challenge"][0], query["state"][0]

        sent = {}

        async def fetch_access_token(self, **kwargs):
            sent.update(kwargs)
            raise RuntimeError("stop after the token request")

        github = type(oauth_routes._get_oauth().github)
        with patch.object(github, "fetch_access_token", fetch_access_token):
            resp = client.get(f"/auth/callback/github?code=abc&state={state}",
                              follow_redirects=False)

        self.assertEqual(resp.headers["location"], "/login?error=auth_failed")
        self.assertIn("code_verifier", sent)
        self.assertEqual(_challenge(sent["code_verifier"]), challenge)


if __name__ == "__main__":
    unittest.main()
