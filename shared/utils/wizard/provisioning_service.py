"""Provision trial agents for the public Agent Builder Wizard.

A trial reuses the existing primitives: a ``Project`` (named ``trial_<tier>_<token>``),
agent(s) created by importing the tier's template with per-prospect ``{{...}}``
substitutions, and a ``widget_api_key`` so the wizard can embed the live test chat.
Nothing here charges money — provisioning just stands up something the prospect can try.
"""

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from shared.utils.models import WidgetApiKey, WizardSession

logger = logging.getLogger(__name__)

# Maps a wizard tier to the agent-template id that provisions it.
TIER_TEMPLATES = {
    "tier1": "wizard-tier1-website-support",
    "tier2": "wizard-tier2-support-scheduling",
    "tier3": "wizard-tier3-sales",
}


class WizardProvisioningService:
    """Stands up (and tracks) trial agents from wizard input.

    Depends on the already-initialised ``DashboardServer`` for its DB client,
    template service, and the shared ``_import_template`` code path.
    """

    def __init__(self, dashboard_server):
        self.dashboard = dashboard_server
        self.db_client = dashboard_server.db_client
        self.template_service = dashboard_server.template_service

    def _build_substitutions(self, tier: str, step_data: Dict[str, Any]) -> Dict[str, str]:
        """Map collected step inputs to the ``{{KEY}}`` placeholders in the tier template."""
        subs = {
            "SITE_URL": (step_data.get("site_url") or "").strip(),
            "EXTRA_INSTRUCTIONS": (step_data.get("instructions") or "").strip() or "(none provided)",
        }
        if tier == "tier3":
            subs["STORE_DOMAIN"] = (step_data.get("store_domain") or "").strip()
            subs["STORE_TOKEN"] = (step_data.get("store_token") or "").strip()
        return subs

    def provision_trial(self, tier: str, step_data: Dict[str, Any], session_token: str,
                        ttl_days: int = 7, origin: Optional[str] = None) -> Dict[str, Any]:
        """Create an isolated trial project + agent(s) + widget key for the given tier.

        Returns ``{widget_api_key, chat_url, project_id, root_agent_name}`` on success,
        or ``{"error": ...}`` on failure.
        """
        template_id = TIER_TEMPLATES.get(tier)
        if not template_id:
            return {"error": f"Tier '{tier}' is not provisionable"}

        template = self.template_service.get_template(template_id)
        if not template:
            return {"error": f"Template not found for tier '{tier}': {template_id}"}

        substitutions = self._build_substitutions(tier, step_data)
        project_name = f"trial_{tier}_{secrets.token_hex(4)}"

        result = self.dashboard._import_template(
            template_dict=template,
            substitutions=substitutions,
            project_name=project_name,
            changed_by="wizard",
        )
        if result.get("error"):
            logger.warning("Wizard provisioning failed for tier %s: %s", tier, result["error"])
            return result

        project_id = result["project_id"]
        root_agent_name = result.get("root_agent_name")
        if not root_agent_name:
            return {"error": "Template did not define a root_agent"}

        api_key = f"wk_{secrets.token_urlsafe(32)}"
        widget_config = {
            "greeting": "Hi! I'm your trial agent — ask me anything.",
            "theme": "auto",
        }

        session = self.db_client.get_session()
        try:
            wk = WidgetApiKey(
                api_key=api_key,
                project_id=project_id,
                agent_name=root_agent_name,
                label=f"wizard trial {tier}",
                allowed_origins=None,  # open for the trial; cleaned up on TTL expiry
                is_active=True,
                widget_config=json.dumps(widget_config),
            )
            session.add(wk)

            ws = session.query(WizardSession).filter(
                WizardSession.session_token == session_token
            ).first()
            if ws:
                ws.status = "provisioned"
                ws.trial_project_id = project_id
                ws.widget_api_key = api_key
                ws.root_agent_name = root_agent_name
                ws.expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("Failed to finalise trial provisioning: %s", exc)
            return {"error": f"Failed to create trial widget key: {exc}"}
        finally:
            session.close()

        return {
            "widget_api_key": api_key,
            "chat_url": f"/widget/chat?key={api_key}",
            "project_id": project_id,
            "root_agent_name": root_agent_name,
        }

    def store_site_memory_blocks(self, project_id: int, pages: list) -> int:
        """Store crawled site pages as memory blocks (one per page + a `site_index`).

        The trial agent lazy-loads these via list_shared_blocks / get_shared_block.
        Returns the number of page blocks created.
        """
        if not pages:
            return 0
        from shared.utils.memory_blocks_service import MemoryBlocksService
        mem = MemoryBlocksService(self.db_client)

        index_lines = []
        created = 0
        for i, pg in enumerate(pages, 1):
            label = f"site_page_{i}"
            value = f"URL: {pg.get('url', '')}\nTitle: {pg.get('title', '')}\n\n{pg.get('text', '')}"
            result = mem.create_block(
                project_id=project_id,
                label=label,
                value=value,
                description=(pg.get("url") or "")[:255],
            )
            if result.get("status") == "success":
                created += 1
                index_lines.append(f"- {label}: {pg.get('title') or '(no title)'} — {pg.get('url', '')}")

        mem.create_block(
            project_id=project_id,
            label="site_index",
            value=(
                "Preloaded website content. Each entry is a memory block you can read with "
                "get_shared_block(block_id=\"<label>\"):\n" + "\n".join(index_lines)
            ),
            description="Index of crawled website pages",
        )
        return created
