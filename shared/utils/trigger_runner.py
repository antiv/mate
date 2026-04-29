"""
TriggerRunner — Background scheduler and executor for AgentTrigger rows.

Uses APScheduler BackgroundScheduler for cron triggers.
Webhook triggers are fired on-demand via the /triggers/{id}/fire route.
file_watch and event_bus triggers are stored in DB but log "not yet implemented".
"""

import hashlib
import json
import logging
import os
import re
import secrets
import smtplib
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_runner_instance: Optional["TriggerRunner"] = None
_runner_lock = threading.Lock()


def get_trigger_runner() -> "TriggerRunner":
    """Return the process-singleton TriggerRunner."""
    global _runner_instance
    with _runner_lock:
        if _runner_instance is None:
            _runner_instance = TriggerRunner()
    return _runner_instance


def generate_webhook_path(name: str) -> str:
    """Generate a unique-enough webhook path slug from a trigger name."""
    slug = re.sub(r'[^a-z0-9-]', '-', name.lower().strip())[:40].strip('-') or 'trigger'
    suffix = secrets.token_hex(4)
    return f"{slug}-{suffix}"


class TriggerRunner:
    """
    Owns the APScheduler instance and provides:
      - start() / shutdown()        — lifecycle
      - sync_cron_jobs()            — reconcile scheduler jobs with DB state
      - execute_trigger(trigger_id) — run one trigger (webhook fire or test-fire)
      - generate_fire_key()         — create a raw key + its SHA-256 hash
      - verify_fire_key(raw, hash)  — constant-time key verification
    """

    def __init__(self) -> None:
        self._scheduler = None
        self._scheduler_lock = threading.Lock()
        self._job_ids: set = set()

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start APScheduler and load cron jobs from DB."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            logger.error("apscheduler not installed — install apscheduler>=3.10 to enable cron triggers")
            return

        with self._scheduler_lock:
            if self._scheduler and self._scheduler.running:
                return
            self._scheduler = BackgroundScheduler(
                timezone="UTC",
                job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
            )
            self._scheduler.start()
            logger.info("TriggerRunner: APScheduler started")

        self.sync_cron_jobs()

    def shutdown(self) -> None:
        """Stop the APScheduler gracefully."""
        with self._scheduler_lock:
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown(wait=False)
                logger.info("TriggerRunner: APScheduler stopped")

    # ------------------------------------------------------------------ #
    # Cron job reconciliation                                              #
    # ------------------------------------------------------------------ #

    def sync_cron_jobs(self) -> None:
        """
        Re-read all enabled cron triggers from DB and reconcile with APScheduler.
        Idempotent — safe to call after any create/update/toggle/delete.
        """
        if not self._scheduler:
            return
        try:
            from shared.utils.database_client import get_database_client
            from shared.utils.models import AgentTrigger
            db = get_database_client()
            session = db.get_session()
            if not session:
                return
            try:
                active = session.query(AgentTrigger).filter(
                    AgentTrigger.trigger_type == 'cron',
                    AgentTrigger.is_enabled.is_(True),
                ).all()
                active_ids = {str(t.id) for t in active}

                for job_id in list(self._job_ids):
                    if job_id not in active_ids:
                        with self._scheduler_lock:
                            job = self._scheduler.get_job(job_id)
                            if job:
                                job.remove()
                        self._job_ids.discard(job_id)

                for trigger in active:
                    self._upsert_cron_job(trigger)
            finally:
                session.close()
        except Exception as exc:
            logger.error("sync_cron_jobs failed: %s", exc)

    def _upsert_cron_job(self, trigger: Any) -> None:
        """Add or reschedule a single APScheduler job for a cron trigger."""
        try:
            from apscheduler.triggers.cron import CronTrigger as APSCronTrigger
        except ImportError:
            return

        job_id = str(trigger.id)
        expr = (trigger.cron_expression or "").strip()
        if not expr:
            logger.warning("Trigger %s has empty cron_expression — skipping", trigger.id)
            return

        parts = expr.split()
        if len(parts) != 5:
            logger.warning("Trigger %s: invalid cron_expression '%s' (expected 5 fields)", trigger.id, expr)
            return

        minute, hour, day, month, day_of_week = parts
        try:
            aps_trigger = APSCronTrigger(
                minute=minute, hour=hour, day=day, month=month,
                day_of_week=day_of_week, timezone="UTC",
            )
            with self._scheduler_lock:
                existing = self._scheduler.get_job(job_id)
                if existing:
                    existing.reschedule(trigger=aps_trigger)
                else:
                    self._scheduler.add_job(
                        func=self._run_trigger_by_id,
                        trigger=aps_trigger,
                        id=job_id,
                        args=[trigger.id],
                        name=trigger.name,
                    )
            self._job_ids.add(job_id)
        except Exception as exc:
            logger.error("_upsert_cron_job failed for trigger %s: %s", trigger.id, exc)

    # ------------------------------------------------------------------ #
    # Execution                                                            #
    # ------------------------------------------------------------------ #

    def _run_trigger_by_id(self, trigger_id: int) -> None:
        """APScheduler callback — runs in a background thread."""
        try:
            from shared.utils.database_client import get_database_client
            from shared.utils.models import AgentTrigger
            db = get_database_client()
            session = db.get_session()
            if not session:
                return
            try:
                trigger = session.query(AgentTrigger).filter(
                    AgentTrigger.id == trigger_id
                ).first()
                if not trigger or not trigger.is_enabled:
                    return
                result = self._execute_trigger_sync(trigger)
                trigger.last_fired_at = datetime.now(timezone.utc)
                trigger.set_last_result(result)
                session.commit()
            except Exception as exc:
                logger.error("_run_trigger_by_id %s error: %s", trigger_id, exc)
                session.rollback()
            finally:
                session.close()
        except Exception as exc:
            logger.error("_run_trigger_by_id outer %s error: %s", trigger_id, exc)

    def execute_trigger(self, trigger_id: int) -> Dict[str, Any]:
        """
        Execute one trigger immediately (webhook fire or dashboard test-fire).
        Persists last_fired_at and last_result. Returns result dict.
        """
        from shared.utils.database_client import get_database_client
        from shared.utils.models import AgentTrigger
        db = get_database_client()
        session = db.get_session()
        if not session:
            return {"status": "error", "message": "Database unavailable"}
        try:
            trigger = session.query(AgentTrigger).filter(
                AgentTrigger.id == trigger_id
            ).first()
            if not trigger:
                return {"status": "error", "message": "Trigger not found"}
            result = self._execute_trigger_sync(trigger)
            trigger.last_fired_at = datetime.now(timezone.utc)
            trigger.set_last_result(result)
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            logger.error("execute_trigger %s error: %s", trigger_id, exc)
            return {"status": "error", "message": str(exc)}
        finally:
            session.close()

    def _execute_trigger_sync(self, trigger: Any) -> Dict[str, Any]:
        """
        Core execution: invoke ADK agent then route output.
        Returns result dict with status, optional agent_response.
        """
        if trigger.trigger_type in ("file_watch", "event_bus"):
            logger.info("Trigger %s type='%s' — not yet implemented", trigger.id, trigger.trigger_type)
            return {"status": "skipped", "message": f"trigger_type '{trigger.trigger_type}' not yet implemented"}

        try:
            agent_response = self._invoke_agent(trigger.agent_name, trigger.prompt)
        except Exception as exc:
            return {"status": "error", "message": f"Agent invocation failed: {exc}"}

        try:
            self._route_output(trigger, agent_response)
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Output routing failed: {exc}",
                "agent_response": agent_response,
            }

        return {"status": "ok", "agent_response": agent_response}

    # ------------------------------------------------------------------ #
    # Agent invocation                                                     #
    # ------------------------------------------------------------------ #

    def _invoke_agent(self, agent_name: str, prompt: str) -> str:
        """
        Invoke an ADK agent synchronously via HTTP.
        Creates a session then streams /run_sse, collecting the last text response.
        Uses synchronous httpx because this runs in APScheduler background threads.
        """
        from shared.utils.utils import get_adk_config
        cfg = get_adk_config()
        host = cfg["adk_host"]
        port = cfg["adk_port"]
        user_id = "trigger_runner"

        session_url = f"http://{host}:{port}/apps/{agent_name}/users/{user_id}/sessions"
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(session_url, json={})
            if resp.status_code != 200:
                raise RuntimeError(f"Session creation failed: HTTP {resp.status_code}")
            session_id = resp.json().get("id", "")

        run_url = f"http://{host}:{port}/run_sse"
        payload = {
            "app_name": agent_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": prompt}]},
            "streaming": True,
        }

        last_text = ""
        last_author = ""
        buffer = ""

        with httpx.Client(timeout=120.0) as client:
            with client.stream(
                "POST", run_url, json=payload,
                headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            ) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"run_sse returned HTTP {r.status_code}")
                for chunk in r.iter_bytes():
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw == "[DONE]":
                            break
                        try:
                            evt = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        author = evt.get("author", "")
                        if author != last_author:
                            last_author = author
                            last_text = ""
                        parts = (evt.get("content") or {}).get("parts") or []
                        has_tool = any(
                            p.get("functionCall") or p.get("functionResponse")
                            or p.get("function_call") or p.get("function_response")
                            for p in parts
                        )
                        if has_tool:
                            last_text = ""
                            continue
                        for part in parts:
                            text = part.get("text")
                            if not text:
                                continue
                            if last_text and text.startswith(last_text):
                                last_text = text
                            elif not (last_text and last_text.startswith(text)):
                                last_text += text

        return last_text.strip()

    # ------------------------------------------------------------------ #
    # Output routing                                                       #
    # ------------------------------------------------------------------ #

    def _route_output(self, trigger: Any, agent_response: str) -> None:
        """Dispatch agent_response to the configured output destination."""
        output_type = trigger.output_type
        cfg = trigger.get_output_config()
        if output_type == "memory_block":
            self._output_memory_block(trigger, cfg, agent_response)
        elif output_type == "http_callback":
            self._output_http_callback(cfg, agent_response)
        elif output_type == "email":
            self._output_email(cfg, agent_response)
        else:
            logger.warning("Unknown output_type '%s' for trigger %s", output_type, trigger.id)

    def _output_memory_block(self, trigger: Any, cfg: dict, text: str) -> None:
        """Write agent response to a memory block (create if not exists)."""
        from shared.utils.database_client import get_database_client
        from shared.utils.memory_blocks_service import MemoryBlocksService
        label = cfg.get("label") or f"trigger_{trigger.id}_output"
        description = cfg.get("description") or f"Auto-updated by trigger '{trigger.name}'"
        svc = MemoryBlocksService(get_database_client())
        result = svc.modify_block(trigger.project_id, label, value=text, description=description)
        if result.get("status") == "error":
            svc.create_block(trigger.project_id, label, value=text, description=description)

    def _output_http_callback(self, cfg: dict, text: str) -> None:
        """POST agent response as JSON to a configured URL."""
        url = cfg.get("url", "")
        if not url:
            logger.warning("http_callback output_config missing 'url'")
            return
        headers = cfg.get("headers") or {}
        timeout = float(cfg.get("timeout", 30))
        payload = {"response": text, "source": "mate_trigger"}
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        logger.info("http_callback delivered to %s (%s)", url, resp.status_code)

    def _output_email(self, cfg: dict, text: str) -> None:
        """Send agent response via SMTP."""
        to_addr = cfg.get("to", "")
        subject = cfg.get("subject", "MATE Trigger Result")
        smtp_host = os.getenv("SMTP_HOST", "")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASS", "")
        from_addr = os.getenv("SMTP_FROM", smtp_user)
        if not to_addr or not smtp_host:
            logger.warning("Email output misconfigured: missing 'to' in config or SMTP_HOST env var")
            return
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(text)
        with smtplib.SMTP(smtp_host, smtp_port) as smtp:
            smtp.starttls()
            if smtp_user and smtp_pass:
                smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)
        logger.info("Email sent to %s", to_addr)

    # ------------------------------------------------------------------ #
    # Fire key helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def generate_fire_key() -> Tuple[str, str]:
        """Return (raw_key, hashed_key). Store hash in DB; give raw_key to caller once."""
        raw = secrets.token_urlsafe(32)
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        return raw, hashed

    @staticmethod
    def verify_fire_key(raw: str, stored_hash: str) -> bool:
        """Constant-time comparison of a raw fire key against its stored SHA-256 hash."""
        return secrets.compare_digest(
            hashlib.sha256(raw.encode()).hexdigest(),
            stored_hash,
        )
