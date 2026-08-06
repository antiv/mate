"""Path containment helpers for endpoints that build filesystem paths from user input."""

from pathlib import Path
from typing import Union

from fastapi import HTTPException


def resolve_within_base(base: Union[str, Path], *parts: Union[str, Path]) -> Path:
    """Resolve ``base`` joined with ``parts``, rejecting anything outside ``base``.

    Guards against ``..`` traversal and absolute-path parts (``Path("/a") / "/etc"``
    silently drops the base). Raises HTTP 403 if the result escapes the base.
    """
    resolved_base = Path(base).resolve()
    target = resolved_base.joinpath(*parts).resolve()
    if target != resolved_base and not target.is_relative_to(resolved_base):
        raise HTTPException(status_code=403, detail="Invalid path")
    return target
