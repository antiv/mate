"""Make dashboard routes see a given caller, for tests of routes that take a RequestContext."""

from types import SimpleNamespace

from fastapi import FastAPI

from server.request_context import get_request_context


def as_caller(app: FastAPI, user_id: str, is_admin: bool) -> None:
    """Override get_request_context on app with this caller."""
    ctx = SimpleNamespace(user_id=user_id, role="admin" if is_admin else "user", is_admin=is_admin)
    app.dependency_overrides[get_request_context] = lambda: ctx
