"""Provision trial agents for the public Agent Builder Wizard.

A trial reuses the existing primitives: a ``Project`` (named ``trial_<tier>_<token>``),
agent(s) created by importing the tier's template with per-prospect ``{{...}}``
substitutions, and a ``widget_api_key`` so the wizard can embed the live test chat.
Nothing here charges money — provisioning just stands up something the prospect can try.
"""

import json
import logging
import os
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

    def _build_substitutions(self, tier: str, step_data: Dict[str, Any], analysis: Dict[str, Any] = None) -> Dict[str, str]:
        """Map collected step inputs to the ``{{KEY}}`` placeholders in the tier template."""
        analysis = analysis or {}
        site_url = (step_data.get("site_url") or "").strip()
        subs = {
            "SITE_URL": site_url,
            "AGENT_MODEL": os.getenv("WIZARD_AGENT_MODEL", "openrouter/google/gemini-2.5-flash"),
            "EXTRA_INSTRUCTIONS": (step_data.get("instructions") or "").strip() or "(none provided)",
            "SERVICES": self._format_services_block(tier, analysis.get("services") or []),
            "AGENT_DESCRIPTION": (analysis.get("description") or "").strip()
                                 or (f"AI assistant for {site_url}" if site_url else "AI assistant"),
            "WORKING_HOURS": (analysis.get("working_hours") or "").strip(),
            "SLOT_MINUTES": str(os.getenv("WIZARD_DEFAULT_SLOT_MIN", "30")),
        }
        if tier == "tier3":
            subs["STORE_DOMAIN"] = (step_data.get("store_domain") or "").strip()
            subs["STORE_TOKEN"] = (step_data.get("store_token") or "").strip()
        return subs

    @staticmethod
    def _format_services_block(tier: str, services: list) -> str:
        """Tier-aware instruction text for the services/booking-reasons list extracted from the site."""
        if services:
            joined = ", ".join(services)
            if tier == "tier2":
                return (f"This business offers these services: {joined}. When scheduling an appointment, "
                        f"offer the visitor this list and let them pick the reason; accept a free-text reason "
                        f"if none fits.")
            if tier == "tier3":
                return f"Products / services available: {joined}."
            return f"This business offers: {joined}. Use this when answering questions."
        # No services extracted
        if tier == "tier2":
            return "When scheduling an appointment, ask the visitor for the reason."
        return ""

    def provision_trial(self, tier: str, step_data: Dict[str, Any], session_token: str,
                        ttl_days: int = 7, origin: Optional[str] = None,
                        pages: list = None, analysis: Dict[str, Any] = None) -> Dict[str, Any]:
        """Create an isolated trial project + agent(s) + widget key for the given tier.

        ``pages`` (crawled site) and ``analysis`` (LLM-extracted {description, services}) are
        optional: ``analysis`` tailors the agent's description + instruction, and ``pages`` are
        stored as memory blocks for the agent to read.

        Returns ``{widget_api_key, chat_url, project_id, root_agent_name}`` on success,
        or ``{"error": ...}`` on failure.
        """
        template_id = TIER_TEMPLATES.get(tier)
        if not template_id:
            return {"error": f"Tier '{tier}' is not provisionable"}

        template = self.template_service.get_template(template_id)
        if not template:
            return {"error": f"Template not found for tier '{tier}': {template_id}"}

        substitutions = self._build_substitutions(tier, step_data, analysis)
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

        # Store crawled site content as memory blocks for the agent to lazy-load.
        if pages:
            try:
                self.store_site_memory_blocks(project_id, pages)
            except Exception as exc:
                logger.warning("Failed to store site memory blocks: %s", exc)

        api_key = f"wk_{secrets.token_urlsafe(32)}"
        widget_title = (analysis or {}).get("site_name", "").strip()
        if not widget_title:
            site_url = (step_data.get("site_url") or step_data.get("store_domain") or "").strip()
            if site_url:
                from urllib.parse import urlparse
                host = urlparse(site_url).netloc or site_url
                host = host.lstrip("www.").split(".")[0]
                widget_title = host.replace("-", " ").replace("_", " ").title()
        widget_config = {
            "title": widget_title or "",
            "greeting": "Hi! I'm your trial agent — ask me anything.",
            "theme": "light",  # always light in wizard preview so it looks like a website widget
            "show_attachments": False,
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

    def promote_trial(self, project_id: int, new_name: str = None) -> dict:
        """Convert a trial into a permanent agent: rename the project out of the ``trial_``
        namespace and detach it from TTL cleanup. The agent(s), memory blocks and widget key
        are kept as-is, so the customer's embedded widget keeps working.
        """
        from shared.utils.models import Project, WizardSession, AgentConfig, WidgetApiKey

        session = self.db_client.get_session()
        try:
            proj = session.query(Project).filter(Project.id == project_id).first()
            if not proj:
                return {"error": "Trial no longer exists (it may have expired)."}

            new_name = (new_name or "").strip()
            if not new_name:
                new_name = proj.name.replace("trial_", "agent_", 1) if proj.name.startswith("trial_") else proj.name
            if new_name.startswith("trial_"):
                new_name = "agent_" + new_name[len("trial_"):]

            clash = session.query(Project).filter(Project.name == new_name, Project.id != project_id).first()
            if clash:
                return {"error": f"A project named '{new_name}' already exists. Choose another name."}

            proj.name = new_name
            proj.template_id = None  # no longer tracked as a trial/template import

            # Detach any wizard session so cleanup won't touch this project.
            session.query(WizardSession).filter(WizardSession.trial_project_id == project_id).update(
                {"trial_project_id": None, "status": "converted", "expires_at": None},
                synchronize_session=False,
            )

            agents = session.query(AgentConfig).filter(AgentConfig.project_id == project_id).all()
            root = next((a.name for a in agents if not a.get_parent_agents()), agents[0].name if agents else None)
            wk = session.query(WidgetApiKey).filter(WidgetApiKey.project_id == project_id).first()
            widget_key = wk.api_key if wk else None

            session.commit()
            return {
                "success": True,
                "project_id": project_id,
                "project_name": new_name,
                "root_agent_name": root,
                "widget_api_key": widget_key,
            }
        except Exception as exc:
            session.rollback()
            logger.exception("promote_trial failed for project %s: %s", project_id, exc)
            return {"error": str(exc)}
        finally:
            session.close()

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
