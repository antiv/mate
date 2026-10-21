"""Make dashboard routes see a given caller, for tests of routes that take a RequestContext."""

from types import SimpleNamespace
from typing import Callable, Union

from fastapi import FastAPI

from server.request_context import get_api_request_context, get_request_context


def as_caller(app: FastAPI, user_id: str, is_admin: Union[bool, Callable[[], bool]]) -> None:
    """Override get_request_context and get_api_request_context on app with this caller.

    is_admin may be a function, read on every request, for tests that change it.
    """
    def ctx():
        admin = is_admin() if callable(is_admin) else is_admin
        return SimpleNamespace(user_id=user_id, role="admin" if admin else "user", is_admin=admin)
    app.dependency_overrides[get_request_context] = ctx
    app.dependency_overrides[get_api_request_context] = ctx
