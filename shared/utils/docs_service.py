"""Serve the ``docs/`` tree to the dashboard: page list, page content and search.

The docs are Markdown files in the repo (see ``docs/README.md``). This module
reads them from disk, so the dashboard shows exactly what shipped with the
running code, with no database table and no build step.

It is standard library only and imports nothing from the rest of MATE, so
``scripts/gen_docs.py`` and the tests can load it without the agent runtime.

Search is deliberately simple: a few dozen pages are scored in memory on every
query. Every term must match (by prefix, so it works while typing), and matches
in a title or heading outweigh matches in the body.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Top-level folders of docs/ that are served, in display order.
SECTIONS: Tuple[str, ...] = ("user", "dev", "reference")

DEFAULT_ORDER = 1000
MAX_QUERY_TERMS = 8
SNIPPET_RADIUS = 90
# A term that only starts a word counts for less than a term that is the word.
PREFIX_WEIGHT = 0.6
# What a page-title match is worth to a section further down that page.
PAGE_TITLE_WEIGHT = 4

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")


# --------------------------------------------------------------------------- #
# Text helpers                                                                 #
# --------------------------------------------------------------------------- #

def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split a page into its frontmatter and body.

    Understands the small YAML subset the docs use: ``key: value``,
    ``key: [a, b]`` and ``key:`` followed by ``- item`` lines.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta: Dict[str, Any] = {}
    current: Optional[str] = None
    for raw in text[4:end].splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", raw)
        if item and current:
            meta.setdefault(current, [])
            if isinstance(meta[current], list):
                meta[current].append(item.group(1).strip().strip("\"'"))
            continue
        pair = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", raw)
        if not pair:
            continue
        current, value = pair.group(1), pair.group(2).strip()
        if value == "":
            meta[current] = []
        elif value.startswith("[") and value.endswith("]"):
            meta[current] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[current] = value.strip("\"'")
    return meta, text[end + 4:].lstrip("\n")


def fold(text: str) -> str:
    """Lowercase and strip diacritics, so ``Č`` finds ``c`` and the reverse."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.replace("đ", "d")


def stem(token: str) -> str:
    """Drop a plural ``s`` so ``trigger`` finds ``triggers``. Nothing cleverer than that."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> List[str]:
    """Words of a text, folded and stemmed. Splits on underscores: ``SMTP_HOST`` is two words."""
    return [stem(token) for token in _TOKEN.findall(fold(text))]


def plain_heading(text: str) -> str:
    """A heading's text without Markdown markup."""
    text = _LINK.sub(r"\1", text)
    return re.sub(r"[`*]", "", text).strip()


def slugify(heading: str) -> str:
    """GitHub-style anchor for a heading, so links written for GitHub work here."""
    text = plain_heading(heading).lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text.strip())


def to_plain_text(markdown: str) -> str:
    """Markdown reduced to readable text, for indexing and result snippets."""
    lines: List[str] = []
    for line in markdown.splitlines():
        if _FENCE.match(line) or _TABLE_RULE.match(line):
            continue
        line = _LINK.sub(r"\1", line)
        line = re.sub(r"^\s*(#{1,6}|>|[-*+]|\d+\.)\s+", "", line)
        line = line.replace("|", " ")
        line = re.sub(r"[`*]", "", line)
        if line.strip():
            lines.append(line.strip())
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def extract_headings(body: str) -> List[Dict[str, Any]]:
    """Every heading outside a code fence, with a unique anchor and its line number."""
    headings: List[Dict[str, Any]] = []
    seen: Counter = Counter()
    in_fence = False
    for number, line in enumerate(body.splitlines()):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        base = slugify(match.group(2)) or "section"
        anchor = base if seen[base] == 0 else f"{base}-{seen[base]}"
        seen[base] += 1
        headings.append({
            "level": len(match.group(1)),
            "text": plain_heading(match.group(2)),
            "anchor": anchor,
            "line": number,
        })
    return headings


# --------------------------------------------------------------------------- #
# Service                                                                      #
# --------------------------------------------------------------------------- #

class DocsService:
    """Reads ``docs/`` and answers the dashboard's three questions about it.

    The tree is re-read whenever a file's size or modification time changes, so
    an edited page shows up on the next request without a restart.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()
        self._signature: Optional[Tuple[Tuple[str, int, int], ...]] = None
        self._pages: Dict[str, Dict[str, Any]] = {}
        self._chunks: List[Dict[str, Any]] = []

    # -- loading ------------------------------------------------------------ #

    def _files(self) -> List[Path]:
        files: List[Path] = []
        for section in SECTIONS:
            folder = self.root / section
            if folder.is_dir():
                files.extend(sorted(folder.rglob("*.md")))
        return files

    def _ensure_loaded(self) -> None:
        files = self._files()
        signature = tuple(
            (f.relative_to(self.root).as_posix(), f.stat().st_mtime_ns, f.stat().st_size)
            for f in files
        )
        with self._lock:
            if signature == self._signature:
                return
            pages: Dict[str, Dict[str, Any]] = {}
            chunks: List[Dict[str, Any]] = []
            for file in files:
                path = file.relative_to(self.root).as_posix()
                try:
                    text = file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                page = self._build_page(path, text)
                pages[path] = page
                chunks.extend(self._build_chunks(page))
            self._pages, self._chunks, self._signature = pages, chunks, signature

    @staticmethod
    def _build_page(path: str, text: str) -> Dict[str, Any]:
        meta, body = parse_frontmatter(text)
        body = _HTML_COMMENT.sub("", body).lstrip("\n")
        headings = extract_headings(body)
        title = meta.get("title") or (headings[0]["text"] if headings else path)
        try:
            order = int(meta.get("order", DEFAULT_ORDER))
        except (TypeError, ValueError):
            order = DEFAULT_ORDER
        return {
            "path": path,
            "section": path.split("/", 1)[0],
            "title": str(title),
            "summary": str(meta.get("summary") or ""),
            "order": order,
            "generated": str(meta.get("generated", "")).lower() == "true",
            # "migrated": moved from the earlier documentation, not yet re-checked against the code.
            "status": str(meta.get("status") or ""),
            "markdown": body,
            "headings": headings,
        }

    @staticmethod
    def _build_chunks(page: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Cut a page at its headings (levels 1-3) into separately searchable parts."""
        lines = page["markdown"].splitlines()
        cuts = [h for h in page["headings"] if h["level"] <= 3]
        # The page title and summary belong to the first part, which links to the top.
        bounds: List[Tuple[str, str, int]] = [("", "", 0)]
        for heading in cuts:
            if heading["level"] == 1 and len(bounds) == 1 and heading["line"] <= 1:
                continue  # the H1 repeats the title; keep it in the intro part
            bounds.append((heading["text"], heading["anchor"], heading["line"]))
        chunks: List[Dict[str, Any]] = []
        for index, (heading, anchor, start) in enumerate(bounds):
            end = bounds[index + 1][2] if index + 1 < len(bounds) else len(lines)
            text = to_plain_text("\n".join(lines[start + (1 if heading else 0):end]))
            if index == 0:
                text = f"{page['summary']} {text}".strip()
            if not text and not heading:
                continue
            chunks.append({
                "path": page["path"],
                "section": page["section"],
                "title": page["title"],
                "generated": page["generated"],
                "heading": heading,
                "anchor": anchor,
                "text": text,
                "title_tokens": set(tokenize(page["title"])) if index == 0 else set(),
                # Lets "rate limit block" find the section about blocking in the page
                # about rate limits, where the section itself never says "rate".
                "page_tokens": set(tokenize(page["title"])) if index else set(),
                "heading_tokens": set(tokenize(heading)),
                "body_tokens": Counter(tokenize(text)),
            })
        return chunks

    # -- queries ------------------------------------------------------------ #

    @staticmethod
    def _summary(page: Dict[str, Any]) -> Dict[str, Any]:
        return {k: page[k] for k in ("path", "section", "title", "summary", "order", "generated", "status")}

    def pages(self, sections: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """Every page (without its content), in section order then sidebar order."""
        self._ensure_loaded()
        wanted = set(sections) if sections else set(SECTIONS)
        listed = [self._summary(p) for p in self._pages.values() if p["section"] in wanted]
        return sorted(listed, key=lambda p: (SECTIONS.index(p["section"]), p["order"], p["title"]))

    def page(self, path: str) -> Optional[Dict[str, Any]]:
        """One page with its Markdown and headings, or ``None``.

        ``path`` is only ever used as a key into the pages already loaded from
        ``docs/``; it never reaches the filesystem, so it cannot escape the tree.
        """
        self._ensure_loaded()
        page = self._pages.get(path)
        if page is None:
            return None
        result = self._summary(page)
        result["markdown"] = page["markdown"]
        result["headings"] = [
            {"level": h["level"], "text": h["text"], "anchor": h["anchor"]}
            for h in page["headings"]
        ]
        return result

    def search(self, query: str, sections: Optional[Sequence[str]] = None,
               limit: int = 20) -> List[Dict[str, Any]]:
        """Parts of pages matching every term of ``query``, best first."""
        self._ensure_loaded()
        terms = list(dict.fromkeys(tokenize(query)))[:MAX_QUERY_TERMS]
        if not terms:
            return []
        wanted = set(sections) if sections else set(SECTIONS)
        phrase = fold(query).strip()

        scored: List[Tuple[float, int, Dict[str, Any]]] = []
        for position, chunk in enumerate(self._chunks):
            if chunk["section"] not in wanted:
                continue
            score = 0.0
            for term in terms:
                term_score = self._term_score(term, chunk)
                if term_score == 0:
                    score = 0.0
                    break
                score += term_score
            if score == 0:
                continue
            if len(terms) > 1 and phrase in fold(chunk["text"]):
                score += 4
            if chunk["generated"]:
                score *= 0.85  # a guide explains; the reference only lists
            scored.append((score, position, chunk))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            {
                "path": chunk["path"],
                "section": chunk["section"],
                "title": chunk["title"],
                "heading": chunk["heading"],
                "anchor": chunk["anchor"],
                "snippet": self._snippet(chunk["text"], terms, phrase),
                "score": round(score, 2),
            }
            for score, _, chunk in scored[:max(1, min(limit, 50))]
        ]

    @staticmethod
    def _term_score(term: str, chunk: Dict[str, Any]) -> float:
        def best(tokens: Any) -> float:
            if term in tokens:
                return 1.0
            if len(term) >= 2 and any(t.startswith(term) for t in tokens):
                return PREFIX_WEIGHT
            return 0.0

        score = (12 * best(chunk["title_tokens"]) + 8 * best(chunk["heading_tokens"])
                 + PAGE_TITLE_WEIGHT * best(chunk["page_tokens"]))
        body = chunk["body_tokens"]
        if term in body:
            score += min(body[term], 5)
        elif len(term) >= 2:
            score += PREFIX_WEIGHT * min(sum(n for t, n in body.items() if t.startswith(term)), 5)
        return score

    @staticmethod
    def _snippet(text: str, terms: Sequence[str], phrase: str = "") -> str:
        """A window of ``text`` around the query: the exact phrase if it occurs,
        otherwise the place where most of the terms occur together."""
        if not text:
            return ""
        folded = fold(text)
        position = 0
        # fold() changes the length of a few characters; only trust offsets when it did not.
        if len(folded) != len(text):
            pass
        elif phrase and phrase in folded:
            position = folded.index(phrase)
        else:
            hits: List[Tuple[int, int]] = []
            for index, term in enumerate(terms):
                pattern = re.compile(r"(?<![^\W_])" + re.escape(term))
                hits.extend((m.start(), index) for m in list(pattern.finditer(folded))[:200])
            hits.sort()
            best_count = 0
            for start, _ in hits:
                nearby = {i for pos, i in hits if start <= pos <= start + SNIPPET_RADIUS}
                if len(nearby) > best_count:
                    best_count, position = len(nearby), start
        start = max(0, position - SNIPPET_RADIUS // 2)
        end = min(len(text), position + SNIPPET_RADIUS + SNIPPET_RADIUS // 2)
        if start > 0:
            space = text.find(" ", start, position)
            start = space + 1 if space != -1 else start
        if end < len(text):
            space = text.rfind(" ", position, end)
            end = space if space > position else end
        snippet = text[start:end].strip()
        return ("… " if start > 0 else "") + snippet + (" …" if end < len(text) else "")


_service: Optional[DocsService] = None
_service_lock = threading.Lock()


def get_docs_service() -> DocsService:
    """The process-wide service over the repo's ``docs/`` folder."""
    global _service
    with _service_lock:
        if _service is None:
            _service = DocsService(Path(__file__).resolve().parent.parent.parent / "docs")
    return _service
