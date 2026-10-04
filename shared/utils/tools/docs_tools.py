"""
Documentation tools: let an agent search and read MATE's own documentation (docs/).

Admins may read every section. Everyone else gets only the user guides
(docs/user/), the same split as the dashboard, whose Documentation page is
admin-only. Results carry a link to the Documentation page only for admins,
since nobody else can open it.
"""

import logging
from typing import Any, Dict, List, Tuple

from google.adk.tools.tool_context import ToolContext

from shared.utils.docs_service import SECTIONS, get_docs_service
from shared.utils.tools.memory_blocks_tools import _is_admin_user

logger = logging.getLogger(__name__)

SEARCH_LIMIT = 8
MAX_PAGE_CHARS = 20000


def _sections(tool_context: ToolContext) -> Tuple[Tuple[str, ...], bool]:
    """The sections this caller may read, and whether the caller is an admin."""
    if _is_admin_user(tool_context):
        return tuple(SECTIONS), True
    return ("user",), False


def _link(path: str, anchor: str = "") -> str:
    return f"/dashboard/docs?page={path}" + (f"#{anchor}" if anchor else "")


def create_docs_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """Create the documentation tools. They need no configuration."""

    def search_docs(query: str, tool_context: ToolContext = None) -> Dict[str, Any]:
        """Search MATE's documentation for how something works or where to find it.

        Use it before answering any question about MATE or its dashboard. Every
        word of the query must appear in a result, so use two to four specific
        keywords (for example "webhook signature" or "memory block limit"), and
        try fewer or different words if nothing is found.

        Args:
            query: Keywords to search for.

        Returns:
            status, and results: each with the page title, the section heading,
            a snippet, the page path and, when the user can open it, a link.
        """
        sections, is_admin = _sections(tool_context)
        results = get_docs_service().search(query or "", sections=sections, limit=SEARCH_LIMIT)
        items = []
        for r in results:
            item = {"title": r["title"], "heading": r["heading"],
                    "snippet": r["snippet"], "path": r["path"]}
            if is_admin:
                item["link"] = _link(r["path"], r["anchor"])
            items.append(item)
        return {"status": "success", "query": query, "results": items}

    def read_doc_page(path: str, tool_context: ToolContext = None) -> Dict[str, Any]:
        """Read one documentation page in full, by the path a search result gave.

        Args:
            path: The page path from search_docs, for example "user/triggers.md".

        Returns:
            status, the page title, its Markdown text and, when the user can open
            it, a link.
        """
        sections, is_admin = _sections(tool_context)
        page = get_docs_service().page(path or "")
        if page is None or page["section"] not in sections:
            return {"status": "error",
                    "error_message": f"No documentation page '{path}'. Use a path from search_docs."}
        markdown = page["markdown"]
        if len(markdown) > MAX_PAGE_CHARS:
            markdown = markdown[:MAX_PAGE_CHARS] + "\n\n[Page truncated]"
        result = {"status": "success", "title": page["title"], "path": page["path"],
                  "markdown": markdown}
        if is_admin:
            result["link"] = _link(page["path"])
        return result

    return [search_docs, read_doc_page]
