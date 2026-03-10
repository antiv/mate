"""
Template service: load and list agent templates from local disk and optional remote URL.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert project name to slug for agent name prefix."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "_", text)
    return text.strip("_") or "project"


class TemplateService:
    """Load agent templates from templates/agent_templates/ and optional remote URL."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.templates_dir = self.project_root / "templates" / "agent_templates"
        self.remote_url = os.getenv("TEMPLATES_REMOTE_URL")

    def list_templates(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List templates with optional category and search filters."""
        templates = []

        # Load from local directory
        if self.templates_dir.exists():
            for path in self.templates_dir.glob("*.json"):
                try:
                    data = self._load_json(path)
                    meta = data.get("template_meta") or {}
                    if not meta.get("id"):
                        meta["id"] = path.stem
                    templates.append(meta)
                except Exception as e:
                    logger.warning("Failed to load template %s: %s", path.name, e)

        # Load from remote URL
        if self.remote_url:
            try:
                remote = self._fetch_remote()
                for item in remote:
                    meta = item.get("template_meta") or item
                    if isinstance(meta, dict) and meta.get("id"):
                        templates.append(meta)
            except Exception as e:
                logger.warning("Failed to fetch remote templates: %s", e)

        # Deduplicate by id (local overrides remote)
        seen = set()
        unique = []
        for t in reversed(templates):
            tid = t.get("id", "")
            if tid and tid not in seen:
                seen.add(tid)
                unique.append(t)
        templates = list(reversed(unique))

        # Apply filters
        if category:
            templates = [t for t in templates if (t.get("category") or "").lower() == category.lower()]
        if search:
            search_lower = search.lower()
            templates = [
                t
                for t in templates
                if search_lower in (t.get("name") or "").lower()
                or search_lower in (t.get("description") or "").lower()
            ]

        return templates

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """Get full template JSON by id."""
        # Local first
        if self.templates_dir.exists():
            path = self.templates_dir / f"{template_id}.json"
            if path.exists():
                return self._load_json(path)
            for p in self.templates_dir.glob("*.json"):
                data = self._load_json(p)
                if (data.get("template_meta") or {}).get("id") == template_id:
                    return data

        # Remote
        if self.remote_url:
            remote = self._fetch_remote()
            for item in remote:
                if (item.get("template_meta") or {}).get("id") == template_id:
                    return item
                if item.get("id") == template_id:
                    return item

        return None

    def _load_json(self, path: Path) -> Dict[str, Any]:
        """Load and parse JSON file."""
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _fetch_remote(self) -> List[Dict[str, Any]]:
        """Fetch templates from remote URL. Expects JSON array."""
        import urllib.request

        with urllib.request.urlopen(self.remote_url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return data if isinstance(data, list) else [data]

    def save_template(self, template_id: str, data: Dict[str, Any]) -> str:
        """Save template JSON to templates/agent_templates/{template_id}.json. Returns path."""
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        path = self.templates_dir / f"{template_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return str(path)

    def delete_template(self, template_id: str) -> bool:
        """Delete template JSON from templates/agent_templates/. Returns True if deleted."""
        if not self.templates_dir.exists():
            return False
        
        path = self.templates_dir / f"{template_id}.json"
        
        # Security check: ensure the path is within templates_dir
        try:
            path.resolve().relative_to(self.templates_dir.resolve())
        except ValueError:
            logger.warning("Attempted to delete template outside templates directory: %s", template_id)
            return False

        if path.exists():
            try:
                path.unlink()
                logger.info("Deleted template: %s", path.name)
                return True
            except Exception as e:
                logger.error("Failed to delete template %s: %s", path.name, e)
                return False
        
        # Fallback search if id doesn't match stem
        for p in self.templates_dir.glob("*.json"):
            try:
                data = self._load_json(p)
                if (data.get("template_meta") or {}).get("id") == template_id:
                    p.unlink()
                    logger.info("Deleted template (by meta ID): %s", p.name)
                    return True
            except Exception:
                continue

        return False

    @staticmethod
    def slugify_project_name(name: str) -> str:
        """Convert project name to slug for agent prefix."""
        return _slugify(name)

    @staticmethod
    def longest_common_prefix(names: List[str]) -> str:
        """Longest common prefix of agent names for agent_prefix derivation."""
        if not names:
            return "tpl"
        prefix = names[0]
        for n in names[1:]:
            while not n.startswith(prefix) and prefix:
                prefix = prefix[:-1]
        return prefix
