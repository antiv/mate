"""
Dashboard API for the documentation under ``docs/``: page list, page content, search.

The routes live under ``/dashboard/api/``, so ``DashboardAuthzMiddleware`` already
restricts them to admins, the same audience as the Documentation page itself.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from shared.utils.docs_service import SECTIONS, get_docs_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/api/docs", tags=["Dashboard - Docs"])

MAX_QUERY_LENGTH = 200


def current_user(request: Request) -> Optional[str]:
    """The signed-in dashboard user, or None. Imported lazily, like the other routers do."""
    from server.auth import get_dashboard_auth_user
    return get_dashboard_auth_user(request)


def _require_user(username: Optional[str]) -> None:
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")


def _sections(section: Optional[str]) -> Optional[List[str]]:
    if not section:
        return None
    if section not in SECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown section. Use one of: {', '.join(SECTIONS)}")
    return [section]


@router.get("/pages")
async def list_doc_pages(username: Optional[str] = Depends(current_user)):
    """List every documentation page with its title, summary and section."""
    _require_user(username)
    return {"sections": list(SECTIONS), "pages": get_docs_service().pages()}


@router.get("/page")
async def get_doc_page(
    path: str = Query(..., max_length=300, description="Page path inside docs/, e.g. user/triggers.md"),
    username: Optional[str] = Depends(current_user),
):
    """Return one documentation page as Markdown, with its headings."""
    _require_user(username)
    page = get_docs_service().page(path)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.get("/search")
async def search_docs(
    q: str = Query("", max_length=MAX_QUERY_LENGTH, description="Search terms; every term must match"),
    section: Optional[str] = Query(None, description="Limit to one section: user, dev or reference"),
    limit: int = Query(20, ge=1, le=50),
    username: Optional[str] = Depends(current_user),
):
    """Search the documentation and return matching parts of pages, best first."""
    _require_user(username)
    results = get_docs_service().search(q, sections=_sections(section), limit=limit)
    return {"query": q, "results": results}
