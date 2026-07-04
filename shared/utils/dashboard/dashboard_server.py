"""
Dashboard Server Implementation
Basic dashboard endpoints without complex service dependencies
"""

import json
import logging
import os
import sys
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.exc import IntegrityError
from fastapi import FastAPI, Request, HTTPException, Depends, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from shared.utils import audit_service

logger = logging.getLogger(__name__)


def sanitize_agent_text(text: Optional[str]) -> Optional[str]:
    """
    Sanitizes agent descriptions and instructions by:
    1. Cleaning double/excessive backslash escapes before single and double quotes.
    2. Normalizing Unicode characters (arrows, em-dashes, curly quotes) to prevent DB encoding issues.
    """
    if not text:
        return text
    import re
    # 1. Clean escaped single and double quotes
    text = re.sub(r'\\+\'', "'", text)
    text = re.sub(r'\\+"', '"', text)
    # 2. Normalize common UTF-8 punctuation that can cause database encoding issues
    text = text.replace('→', '->')
    text = text.replace('←', '<-')
    text = text.replace('—', '-')
    text = text.replace('–', '-')
    text = text.replace('…', '...')
    text = text.replace('‘', "'").replace('’', "'")
    text = text.replace('“', '"').replace('”', '"')
    return text


class DashboardServer:
    """Dashboard Server with basic endpoint registration"""
    
    def __init__(self, app: FastAPI, project_root: Path):
        self.app = app
        self.project_root = project_root
        
        # Get ADK configuration for server control
        from shared.utils.utils import get_adk_config
        adk_config = get_adk_config()
        self.adk_host = adk_config["adk_host"]
        self.adk_port = adk_config["adk_port"]
        self.session_service_uri = adk_config["session_service_uri"]
        
        # Initialize database services
        self._initialize_services()
        
        # Initialize templates and static files
        self._setup_templates_and_static()
        
        # Initialize template service
        from shared.utils.template_service import TemplateService
        self.template_service = TemplateService(project_root)
        
        # Register all dashboard endpoints
        self._register_endpoints()
    
    def _initialize_services(self):
        """Initialize database and other services."""
        try:
            from shared.utils.database_client import get_database_client
            from shared.utils.user_service import UserService
            from shared.utils.token_usage_service import TokenUsageService
            from shared.utils.models import (
                AgentConfig, AgentConfigVersion, Project, User, TokenUsageLog,
                GuardrailLog, AuditLog, TestCase, EvalResult, AgentTrigger,
                FileSearchStore, AgentFileSearchStore, FileSearchDocument,
                MemoryBlock, WidgetApiKey, WizardSession
            )

            self.db_client = get_database_client()
            self.user_service = UserService()
            self.token_service = TokenUsageService()
            self.AgentConfig = AgentConfig
            self.AgentConfigVersion = AgentConfigVersion
            self.Project = Project
            self.User = User
            self.TokenUsageLog = TokenUsageLog
            self.GuardrailLog = GuardrailLog
            self.AuditLog = AuditLog
            self.TestCase = TestCase
            self.EvalResult = EvalResult
            self.AgentTrigger = AgentTrigger
            self.FileSearchStore = FileSearchStore
            self.AgentFileSearchStore = AgentFileSearchStore
            self.FileSearchDocument = FileSearchDocument
            self.MemoryBlock = MemoryBlock
            self.WidgetApiKey = WidgetApiKey
            self.WizardSession = WizardSession
            
            print("✅ Dashboard database services initialized successfully")
        except Exception as e:
            print(f"⚠️  Dashboard database services initialization error: {e}")
            # Set defaults if services fail to initialize
            self.db_client = None
            self.user_service = None
            self.token_service = None
    
    async def _invoke_agent_for_eval(self, agent_name: str, input_text: str, timeout: float = 120.0) -> str:
        """
        Create a fresh ADK session, send input_text to the agent, and collect
        the full text response by reading the /run_sse SSE stream.
        Uses the same partial/complete de-duplication logic as the frontend widget.
        """
        import httpx
        from shared.utils.utils import get_adk_config

        adk = get_adk_config()
        host, port = adk["adk_host"], adk["adk_port"]
        user_id = "eval_runner"

        # 1. Create a fresh session
        session_url = f"http://{host}:{port}/apps/{agent_name}/users/{user_id}/sessions"
        async with httpx.AsyncClient(timeout=30.0) as c:
            resp = await c.post(session_url, json={})
            if resp.status_code != 200:
                raise RuntimeError(f"ADK session creation failed: {resp.status_code} {resp.text[:200]}")
            session_id = resp.json().get("id", "")

        # 2. Stream /run_sse and collect text
        run_url = f"http://{host}:{port}/run_sse"
        payload = {
            "app_name": agent_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": input_text}]},
            "streaming": True,
        }
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}

        # Track the final text response: reset per author, skip tool call parts.
        # At stream end, last_text holds the final reply to the user.
        last_author = ""
        last_text = ""
        buffer = ""

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", run_url, json=payload, headers=headers) as r:
                if r.status_code != 200:
                    raise RuntimeError(f"ADK /run_sse returned {r.status_code}")
                async for chunk in r.aiter_bytes():
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.rstrip("\r")
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw == "[DONE]":
                            break
                        try:
                            evt = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        # Skip transfer/routing actions
                        actions = evt.get("actions") or {}
                        if actions.get("transfer_to_agent") or actions.get("escalate"):
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
                            # Tool interaction — reset text for this author turn
                            last_text = ""
                            continue

                        for part in parts:
                            t = part.get("text")
                            if not t:
                                continue
                            # De-duplicate partial vs complete events
                            if last_text and t.startswith(last_text):
                                last_text = t
                            elif last_text and last_text.startswith(t):
                                pass
                            else:
                                last_text += t

        return last_text.strip()

    def _get_usage_stats(self, days: int = 7) -> Dict[str, Any]:
        """Get usage statistics from database."""
        if not self.db_client:
            return {
                "total_requests": 0,
                "total_prompt_tokens": 0,
                "total_response_tokens": 0,
                "unique_users": 1,
                "unique_agents": 0,
                "top_agents": [],
                "daily_usage": [],
                "hourly_usage": [0] * 24,
                "database_info": {"type": "SQLITE", "filename": "my_agent_data.db"}
            }
        
        session = self.db_client.get_session()
        if not session:
            return {"error": "Database connection failed"}
        
        try:
            from sqlalchemy import func
            from datetime import datetime, timedelta
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Get token usage statistics
            stats = session.query(
                func.count(self.TokenUsageLog.id).label('total_requests'),
                func.sum(self.TokenUsageLog.prompt_tokens).label('total_prompt_tokens'),
                func.sum(self.TokenUsageLog.response_tokens).label('total_response_tokens'),
                func.count(func.distinct(self.TokenUsageLog.user_id)).label('unique_users'),
                func.count(func.distinct(self.TokenUsageLog.agent_name)).label('unique_agents')
            ).filter(
                self.TokenUsageLog.timestamp >= start_date,
                self.TokenUsageLog.timestamp <= end_date
            ).first()
            
            # Get top agents
            top_agents = session.query(
                self.TokenUsageLog.agent_name,
                func.count(self.TokenUsageLog.id).label('request_count')
            ).filter(
                self.TokenUsageLog.timestamp >= start_date,
                self.TokenUsageLog.timestamp <= end_date
            ).group_by(self.TokenUsageLog.agent_name).order_by(func.count(self.TokenUsageLog.id).desc()).limit(5).all()
            
            # Get daily usage
            daily_usage = session.query(
                func.date(self.TokenUsageLog.timestamp).label('date'),
                func.count(self.TokenUsageLog.id).label('requests'),
                func.sum(self.TokenUsageLog.prompt_tokens + self.TokenUsageLog.response_tokens).label('total_tokens')
            ).filter(
                self.TokenUsageLog.timestamp >= start_date,
                self.TokenUsageLog.timestamp <= end_date
            ).group_by(func.date(self.TokenUsageLog.timestamp)).order_by(func.date(self.TokenUsageLog.timestamp)).all()
            
            # Get hourly usage
            hourly_usage = session.query(
                func.extract('hour', self.TokenUsageLog.timestamp).label('hour'),
                func.count(self.TokenUsageLog.id).label('requests')
            ).filter(
                self.TokenUsageLog.timestamp >= start_date,
                self.TokenUsageLog.timestamp <= end_date
            ).group_by(func.extract('hour', self.TokenUsageLog.timestamp)).order_by(func.extract('hour', self.TokenUsageLog.timestamp)).all()
            
            # Create hourly data array (24 hours, 0-23)
            hourly_data = [0] * 24
            for hour_stat in hourly_usage:
                hour = int(hour_stat.hour)
                hourly_data[hour] = hour_stat.requests
            
            return {
                'total_requests': stats.total_requests or 0,
                'total_prompt_tokens': stats.total_prompt_tokens or 0,
                'total_response_tokens': stats.total_response_tokens or 0,
                'unique_users': stats.unique_users or 0,
                'unique_agents': stats.unique_agents or 0,
                'top_agents': [{'agent': agent.agent_name, 'requests': agent.request_count} for agent in top_agents],
                'daily_usage': [{'date': str(day.date), 'requests': day.requests, 'tokens': day.total_tokens or 0} for day in daily_usage],
                'hourly_usage': hourly_data,
                'database_info': self._get_database_info()
            }
        except Exception as e:
            print(f"Error getting usage stats: {e}")
            return {"error": str(e)}
        finally:
            session.close()
    
    def _get_database_info(self) -> dict:
        """Get database connection information."""
        db_type = os.getenv("DB_TYPE", "sqlite").upper()
        info = {
            "type": db_type,
            "hostname": None,
            "filename": None,
            "database": None,
            "port": None
        }
        
        if db_type == "SQLITE":
            db_path = os.getenv("DB_PATH", "my_agent_data.db")
            info["filename"] = os.path.basename(db_path)
        elif db_type == "POSTGRESQL":
            info["hostname"] = os.getenv("DB_HOST", "localhost")
            info["database"] = os.getenv("DB_NAME", "")
            info["port"] = os.getenv("DB_PORT", "5432")
        elif db_type == "MYSQL":
            info["hostname"] = os.getenv("DB_HOST", "localhost")
            info["database"] = os.getenv("DB_NAME", "")
            info["port"] = os.getenv("DB_PORT", "3306")
        
        return info
    
    def _get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users from database."""
        if not self.db_client:
            return []
        
        session = self.db_client.get_session()
        if not session:
            return []
        
        try:
            users = session.query(self.User).all()
            return [user.to_dict() for user in users]
        except Exception as e:
            print(f"Error getting users: {e}")
            return []
        finally:
            session.close()
    
    def _get_all_projects(self) -> List[Dict[str, Any]]:
        """Get all projects from database."""
        if not self.db_client:
            return []
        
        session = self.db_client.get_session()
        if not session:
            return []
        
        try:
            projects = session.query(self.Project).order_by(self.Project.name.asc()).all()
            return [project.to_dict() for project in projects]
        except Exception as exc:
            print(f"Error getting projects: {exc}")
            return []
        finally:
            session.close()
    
    def _create_project(self, name: str, description: Optional[str]) -> Dict[str, Any]:
        """Create a new project."""
        if not self.db_client:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        session = self.db_client.get_session()
        if not session:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        try:
            project = self.Project(name=name.strip(), description=(description or "").strip() or None)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project.to_dict()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=400, detail="Project name must be unique")
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to create project: {exc}")
        finally:
            session.close()
    
    def _update_project(self, project_id: int, name: str, description: Optional[str]) -> Dict[str, Any]:
        """Update an existing project."""
        if not self.db_client:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        session = self.db_client.get_session()
        if not session:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        try:
            project = session.query(self.Project).filter(self.Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            
            project.name = name.strip()
            project.description = (description or "").strip() or None
            session.commit()
            session.refresh(project)
            return project.to_dict()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=400, detail="Project name must be unique")
        except HTTPException:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to update project: {exc}")
        finally:
            session.close()
    
    def _delete_project(self, project_id: int) -> Dict[str, Any]:
        """Delete a project and associated agents, stores, triggers, memory blocks, and keys."""
        if not self.db_client:
            raise HTTPException(status_code=500, detail="Database connection failed")

        session = self.db_client.get_session()
        if not session:
            raise HTTPException(status_code=500, detail="Database connection failed")

        try:
            project = session.query(self.Project).filter(self.Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")

            # 1. Delete Agent Config Versions and Agent Configs in dependency order
            agent_ids = [a.id for a in session.query(self.AgentConfig.id).filter(
                self.AgentConfig.project_id == project_id
            ).all()]
            if agent_ids:
                session.query(self.AgentConfigVersion).filter(
                    self.AgentConfigVersion.agent_config_id.in_(agent_ids)
                ).delete(synchronize_session=False)
                session.query(self.AgentConfig).filter(
                    self.AgentConfig.id.in_(agent_ids)
                ).delete(synchronize_session=False)

            # 2. Delete Agent File Search Stores, Documents, and File Search Stores in dependency order
            store_ids = [s.id for s in session.query(self.FileSearchStore.id).filter(
                self.FileSearchStore.project_id == project_id
            ).all()]
            if store_ids:
                session.query(self.AgentFileSearchStore).filter(
                    self.AgentFileSearchStore.store_id.in_(store_ids)
                ).delete(synchronize_session=False)
                session.query(self.FileSearchDocument).filter(
                    self.FileSearchDocument.store_id.in_(store_ids)
                ).delete(synchronize_session=False)
                session.query(self.FileSearchStore).filter(
                    self.FileSearchStore.id.in_(store_ids)
                ).delete(synchronize_session=False)

            # 3. Delete Memory Blocks
            session.query(self.MemoryBlock).filter(
                self.MemoryBlock.project_id == project_id
            ).delete(synchronize_session=False)

            # 4. Delete Widget API Keys
            session.query(self.WidgetApiKey).filter(
                self.WidgetApiKey.project_id == project_id
            ).delete(synchronize_session=False)

            # 5. Delete Agent Triggers
            session.query(self.AgentTrigger).filter(
                self.AgentTrigger.project_id == project_id
            ).delete(synchronize_session=False)

            # 6. Disassociate public wizard sessions (set trial_project_id to NULL)
            session.query(self.WizardSession).filter(
                self.WizardSession.trial_project_id == project_id
            ).update({self.WizardSession.trial_project_id: None}, synchronize_session=False)

            # 7. Delete the Project itself
            session.delete(project)
            session.commit()
            return {"success": True}
        except HTTPException:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to delete project: {exc}")
        finally:
            session.close()
    
    def _get_all_agent_configs(self, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get agent configurations from database, optionally filtered by project."""
        if not self.db_client:
            return []
        
        session = self.db_client.get_session()
        if not session:
            return []
        
        try:
            query = session.query(self.AgentConfig)
            if project_id is not None:
                query = query.filter(self.AgentConfig.project_id == project_id)
            configs = query.all()
            result = []
            for config in configs:
                config_dict = config.to_dict()
                # Always use the database object's ID directly to ensure it's correct
                # This prevents any issues where to_dict() might return wrong ID
                db_id = config.id
                config_dict['id'] = db_id
                
                # Verify the ID is valid (should be an integer)
                if not isinstance(db_id, int):
                    print(f"Warning: Agent '{config_dict.get('name', 'unknown')}' has non-integer ID: {db_id} (type: {type(db_id)})")
                    # Try to convert to int if it's a numeric string
                    try:
                        config_dict['id'] = int(db_id)
                    except (ValueError, TypeError):
                        print(f"Error: Agent '{config_dict.get('name')}' has invalid ID: {db_id}. Cannot convert to integer.")
                        # Skip this config or use a fallback?
                        continue
                
                # Ensure project metadata is serialized for the frontend
                project = config.project.to_dict() if getattr(config, "project", None) else None
                config_dict['project'] = project
                # Normalize parent agents to list
                if isinstance(config_dict.get('parent_agents'), str):
                    try:
                        config_dict['parent_agents'] = json.loads(config_dict['parent_agents']) if config_dict['parent_agents'] else []
                    except json.JSONDecodeError:
                        pass
                result.append(config_dict)
            return result
        except Exception as e:
            print(f"Error getting agent configs: {e}")
            import traceback
            traceback.print_exc()
            return []
        finally:
            session.close()
    
    def _get_schema_migrations(self) -> List[Dict[str, Any]]:
        """Get all schema migrations."""
        if not self.db_client:
            return []
        
        session = self.db_client.get_session()
        if not session:
            return []
        
        try:
            from sqlalchemy import text
            # Query the schema_migrations table directly with explicit column names
            result = session.execute(text("""
                SELECT id, version, name, applied_at, checksum 
                FROM schema_migrations 
                ORDER BY version
            """))
            migrations = []
            for row in result:
                migrations.append({
                    'id': row[0],
                    'version': row[1],
                    'name': row[2],
                    'applied_at': str(row[3]) if row[3] else None,
                    'checksum': row[4] or ""
                })
            return migrations
        except Exception as e:
            print(f"Error getting schema migrations: {e}")
            return []
        finally:
            session.close()
    
    def _get_token_usage_logs(self, hours: int = 24, limit: int = 100, page: int = 1) -> Dict[str, Any]:
        """Get paginated token usage logs."""
        if not self.db_client:
            return {"error": "Database connection failed"}
        
        session = self.db_client.get_session()
        if not session:
            return {"error": "Database connection failed"}
        
        try:
            from sqlalchemy import func
            from datetime import datetime, timedelta
            
            # Calculate time threshold
            time_threshold = datetime.now() - timedelta(hours=hours)
            
            # Get total count
            total_count = session.query(func.count(self.TokenUsageLog.id)).filter(
                self.TokenUsageLog.timestamp >= time_threshold
            ).scalar()
            
            # Calculate pagination
            total_pages = (total_count + limit - 1) // limit  # Ceiling division
            offset = (page - 1) * limit
            
            # Get paginated logs
            logs = session.query(self.TokenUsageLog).filter(
                self.TokenUsageLog.timestamp >= time_threshold
            ).order_by(
                self.TokenUsageLog.timestamp.desc()
            ).offset(offset).limit(limit).all()
            
            return {
                "logs": [{
                    'id': log.id,
                    'request_id': log.request_id,
                    'session_id': log.session_id,
                    'user_id': log.user_id,
                    'agent_name': log.agent_name,
                    'model_name': log.model_name,
                    'prompt_tokens': log.prompt_tokens,
                    'response_tokens': log.response_tokens,
                    'thoughts_tokens': log.thoughts_tokens,
                    'tool_use_tokens': log.tool_use_tokens,
                    'status': log.status,
                    'error_description': log.error_description,
                    'timestamp': log.timestamp.isoformat() if log.timestamp else None
                } for log in logs],
                "total_records": total_count,
                "current_page": page,
                "total_pages": total_pages,
                "page_size": limit
            }
        except Exception as e:
            print(f"Error getting usage logs: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    def _get_traces(self, hours: int = 24, limit: int = 50, trace_id: Optional[str] = None) -> Dict[str, Any]:
        """Get traces from trace_spans table. Returns list of traces with span trees."""
        if not self.db_client:
            return {"traces": [], "error": "Database connection failed"}

        session = self.db_client.get_session()
        if not session:
            return {"traces": [], "error": "Database connection failed"}

        try:
            from sqlalchemy import text
            from datetime import datetime, timedelta

            time_threshold = datetime.now() - timedelta(hours=hours)

            if trace_id:
                rows = session.execute(
                    text("""
                        SELECT trace_id, span_id, parent_span_id, name, kind, start_time, end_time, duration_ms, attributes, status, error_message
                        FROM trace_spans WHERE trace_id = :tid AND start_time >= :threshold
                        ORDER BY start_time ASC
                    """),
                    {"tid": trace_id, "threshold": time_threshold},
                ).fetchall()
            else:
                # Fetch all spans in time range, group by trace_id, take top N traces by most recent
                rows = session.execute(
                    text("""
                        SELECT trace_id, span_id, parent_span_id, name, kind, start_time, end_time, duration_ms, attributes, status, error_message
                        FROM trace_spans
                        WHERE start_time >= :threshold
                        ORDER BY start_time DESC
                    """),
                    {"threshold": time_threshold},
                ).fetchall()
                # Build trace_id -> max(start_time) to sort traces, keep top N
                trace_max_time = {}
                for r in rows:
                    tid, _, _, _, _, st, _, _, _, _, _ = r
                    if tid not in trace_max_time or (st and (not trace_max_time[tid] or st > trace_max_time[tid])):
                        trace_max_time[tid] = st
                sorted_traces = sorted(
                    trace_max_time.keys(),
                    key=lambda t: trace_max_time[t] if trace_max_time[t] else time_threshold,
                    reverse=True,
                )[:limit]
                keep = set(sorted_traces)
                rows = [r for r in rows if r[0] in keep]

            # Group by trace_id and build span list
            def _ts(val):
                if val is None:
                    return None
                if hasattr(val, "isoformat"):
                    return val.isoformat()
                return str(val)

            def _parse_attrs(attrs):
                if not attrs:
                    return {}
                try:
                    return json.loads(attrs)
                except (json.JSONDecodeError, TypeError):
                    return {}

            traces = {}
            for row in rows:
                tid, sid, pid, name, kind, st, et, dur, attrs, status, err = row
                span_data = {
                    "span_id": sid,
                    "parent_span_id": pid,
                    "name": name,
                    "kind": kind,
                    "start_time": _ts(st),
                    "end_time": _ts(et),
                    "duration_ms": dur,
                    "attributes": _parse_attrs(attrs),
                    "status": status,
                    "error_message": err,
                }
                if tid not in traces:
                    traces[tid] = {"trace_id": tid, "spans": [], "latest_start": None}
                traces[tid]["spans"].append(span_data)
                if st:
                    prev = traces[tid]["latest_start"]
                    traces[tid]["latest_start"] = st if prev is None else (st if st > prev else prev)

            # Compute root span and total duration per trace, sort by latest activity
            result = []
            for tid, data in traces.items():
                spans = data["spans"]
                root = next((s for s in spans if not s["parent_span_id"]), spans[0] if spans else None)
                total_dur = max((s["duration_ms"] or 0) for s in spans) if spans else 0
                result.append({
                    "trace_id": tid,
                    "root_name": root["name"] if root else "unknown",
                    "root_duration_ms": root.get("duration_ms") if root else 0,
                    "total_duration_ms": total_dur,
                    "span_count": len(spans),
                    "spans": spans,
                    "_latest_start": data["latest_start"],
                })
            result.sort(key=lambda t: (t["_latest_start"] or time_threshold), reverse=True)
            for t in result:
                t.pop("_latest_start", None)
            return {"traces": result}
        except Exception as e:
            logger.warning("Error getting traces (trace_spans table may not exist): %s", e)
            return {"traces": [], "error": str(e)}
        finally:
            session.close()

    def _create_user(self, user_id: str, roles: List[str]) -> bool:
        """Create a new user."""
        if not self.db_client:
            return False
        
        session = self.db_client.get_session()
        if not session:
            return False
        
        try:
            import json
            user = self.User(user_id=user_id, roles=json.dumps(roles))
            session.add(user)
            session.commit()
            return True
        except Exception as e:
            print(f"Error creating user: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def _update_user(self, user_id: str, roles: List[str], profile_data: Optional[str] = None) -> bool:
        """Update user roles and profile data."""
        if not self.user_service:
            return False
        
        try:
            # Update roles
            roles_success = self.user_service.update_user_roles(user_id, roles)
            
            # Update profile data if provided
            if profile_data is not None:
                profile_success = self.user_service.update_user_profile(user_id, profile_data)
                return roles_success and profile_success
            
            return roles_success
        except Exception as e:
            print(f"Error updating user: {e}")
            return False
    
    def _delete_user(self, user_id: str) -> bool:
        """Delete a user."""
        if not self.db_client:
            return False
        
        session = self.db_client.get_session()
        if not session:
            return False
        
        try:
            user = session.query(self.User).filter(self.User.user_id == user_id).first()
            if user:
                session.delete(user)
                session.commit()
                return True
            return False
        except Exception as e:
            print(f"Error deleting user: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def _copy_template_agent(self, agent_name: str) -> Dict[str, Any]:
        """Copy template_agent folder to agents/{agent_name}/ directory."""
        try:
            # Define source and destination paths
            template_path = self.project_root / "shared" / "template_agent"
            agents_dir = self.project_root / "agents"
            dest_path = agents_dir / agent_name
            
            # Check if template exists
            if not template_path.exists():
                return {
                    "success": False,
                    "message": f"Template agent folder not found at {template_path}",
                    "skipped": False
                }
            
            # Check if destination already exists
            if dest_path.exists():
                return {
                    "success": True,
                    "message": f"Agent folder '{agent_name}' already exists, skipping copy",
                    "skipped": True
                }
            
            # Create agents directory if it doesn't exist
            agents_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy template folder
            shutil.copytree(template_path, dest_path)
            
            return {
                "success": True,
                "message": f"Agent folder '{agent_name}' created successfully from template",
                "skipped": False,
                "path": str(dest_path)
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error copying template: {str(e)}",
                "skipped": False
            }
    
    def _delete_agent_folder(self, agent_name: str) -> Dict[str, Any]:
        """Delete agent folder from agents/{agent_name}/ directory."""
        try:
            agents_dir = self.project_root / "agents"
            folder_path = agents_dir / agent_name
            
            # Check if folder exists
            if not folder_path.exists():
                return {
                    "success": True,
                    "message": f"Agent folder '{agent_name}' does not exist, nothing to delete",
                    "folder_deleted": False,
                    "folder_path": None
                }
            
            # Make sure it's a directory
            if not folder_path.is_dir():
                return {
                    "success": False,
                    "message": f"Path exists but is not a directory: {folder_path}",
                    "folder_deleted": False,
                    "folder_path": str(folder_path),
                    "error": True
                }
            
            # Delete the folder
            shutil.rmtree(folder_path)
            
            return {
                "success": True,
                "message": f"Agent folder '{agent_name}' deleted successfully",
                "folder_deleted": True,
                "folder_path": str(folder_path)
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Error deleting folder: {str(e)}",
                "folder_deleted": False,
                "folder_path": str(folder_path) if 'folder_path' in locals() else None,
                "error": True
            }
    
    def _create_agent_config(self, config_data: Dict[str, Any], changed_by: str = None) -> bool:
        """Create a new agent configuration."""
        if not self.db_client:
            return False
        
        session = self.db_client.get_session()
        if not session:
            return False
        
        try:
            import json
            # Convert list fields to JSON strings for database storage
            processed_data = config_data.copy()
            if 'description' in processed_data:
                processed_data['description'] = sanitize_agent_text(processed_data['description'])
            if 'instruction' in processed_data:
                processed_data['instruction'] = sanitize_agent_text(processed_data['instruction'])
            if 'parent_agents' in processed_data and isinstance(processed_data['parent_agents'], list):
                processed_data['parent_agents'] = json.dumps(processed_data['parent_agents']) if processed_data['parent_agents'] else None
            if 'allowed_for_roles' in processed_data and isinstance(processed_data['allowed_for_roles'], list):
                processed_data['allowed_for_roles'] = json.dumps(processed_data['allowed_for_roles']) if processed_data['allowed_for_roles'] else None
            if 'mcp_servers_config' in processed_data and isinstance(processed_data['mcp_servers_config'], dict):
                processed_data['mcp_servers_config'] = json.dumps(processed_data['mcp_servers_config']) if processed_data['mcp_servers_config'] else None
            if 'tool_config' in processed_data and isinstance(processed_data['tool_config'], dict):
                processed_data['tool_config'] = json.dumps(processed_data['tool_config']) if processed_data['tool_config'] else None
            if 'planner_config' in processed_data and isinstance(processed_data['planner_config'], dict):
                processed_data['planner_config'] = json.dumps(processed_data['planner_config']) if processed_data['planner_config'] else None
            if 'generate_content_config' in processed_data and isinstance(processed_data['generate_content_config'], dict):
                processed_data['generate_content_config'] = json.dumps(processed_data['generate_content_config']) if processed_data['generate_content_config'] else None
            if 'guardrail_config' in processed_data and isinstance(processed_data['guardrail_config'], dict):
                processed_data['guardrail_config'] = json.dumps(processed_data['guardrail_config']) if processed_data['guardrail_config'] else None
            if 'input_schema' in processed_data and isinstance(processed_data['input_schema'], dict):
                processed_data['input_schema'] = json.dumps(processed_data['input_schema']) if processed_data['input_schema'] else None
            if 'output_schema' in processed_data and isinstance(processed_data['output_schema'], dict):
                processed_data['output_schema'] = json.dumps(processed_data['output_schema']) if processed_data['output_schema'] else None
            if 'include_contents' in processed_data and isinstance(processed_data['include_contents'], list):
                processed_data['include_contents'] = json.dumps(processed_data['include_contents']) if processed_data['include_contents'] else None
            project_id = processed_data.get('project_id')
            processed_data['project_id'] = int(project_id) if project_id is not None else 1
            
            config = self.AgentConfig(**processed_data)
            session.add(config)
            session.commit()
            self._snapshot_agent_config(session, config, change_type='create', changed_by=changed_by)
            return True
        except Exception as e:
            print(f"Error creating agent config: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def _update_agent_config(self, config_id: int, config_data: Dict[str, Any], changed_by: str = None) -> bool:
        """Update an agent configuration."""
        if not self.db_client:
            return False
        
        session = self.db_client.get_session()
        if not session:
            return False
        
        # Ensure session is clean
        session.rollback()
        
        try:
            import json
            config = session.query(self.AgentConfig).filter(self.AgentConfig.id == config_id).first()
            if config:
                original_parents = config.get_parent_agents() if hasattr(config, 'get_parent_agents') else []
                original_project_id = config.project_id
                updated_project_id = config_data.get('project_id', original_project_id)

                for key, value in config_data.items():
                    if hasattr(config, key):
                        # Handle JSON fields specially - convert lists/dicts to JSON strings
                        if key in ['parent_agents', 'allowed_for_roles', 'include_contents'] and isinstance(value, list):
                            setattr(config, key, json.dumps(value) if value else None)
                        elif key in ['mcp_servers_config', 'tool_config', 'planner_config', 'generate_content_config', 'input_schema', 'output_schema', 'guardrail_config'] and isinstance(value, dict):
                            setattr(config, key, json.dumps(value) if value else None)
                        elif key == 'project_id' and value is not None:
                            try:
                                setattr(config, key, int(value))
                            except (TypeError, ValueError):
                                pass
                        else:
                            if key in ['description', 'instruction']:
                                value = sanitize_agent_text(value)
                            setattr(config, key, value)

                session.flush()

                def _propagate_project_to_descendants(parent_name: str, new_project_id: int, visited=None):
                    if visited is None:
                        visited = set()
                    if parent_name in visited:
                        return
                    visited.add(parent_name)

                    child_agents = session.query(self.AgentConfig).filter(
                        self.AgentConfig.parent_agents.isnot(None),
                        self.AgentConfig.parent_agents.like(f'%"{parent_name}"%')
                    ).all()

                    for child in child_agents:
                        child.project_id = new_project_id
                        session.flush()
                        _propagate_project_to_descendants(child.name, new_project_id, visited)

                try:
                    updated_parents = json.loads(config.parent_agents) if config.parent_agents else []
                except json.JSONDecodeError:
                    updated_parents = original_parents

                project_changed = (original_project_id != config.project_id)
                is_root_agent = len(updated_parents) == 0

                if project_changed and is_root_agent:
                    _propagate_project_to_descendants(config.name, config.project_id)

                session.commit()
                self._snapshot_agent_config(session, config, change_type='update', changed_by=changed_by)
                return True
            return False
        except Exception as e:
            print(f"Error updating agent config: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def _delete_agent_config(self, config_id: int) -> bool:
        """Delete an agent configuration."""
        if not self.db_client:
            return False
        
        session = self.db_client.get_session()
        if not session:
            return False
        
        try:
            config = session.query(self.AgentConfig).filter(self.AgentConfig.id == config_id).first()
            if config:
                from shared.utils.models import AgentConfigVersion
                session.query(AgentConfigVersion).filter(AgentConfigVersion.agent_config_id == config_id).delete(synchronize_session=False)
                session.delete(config)
                session.commit()
                return True
            return False
        except Exception as e:
            print(f"Error deleting agent config: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    # ─── Agent Config Versioning ──────────────────────────────────────────

    def _build_config_snapshot(self, config) -> dict:
        """Build a plain dict snapshot of an AgentConfig (no relationships)."""
        return {
            'name': config.name,
            'type': config.type,
            'model_name': config.model_name,
            'description': config.description,
            'instruction': config.instruction,
            'mcp_servers_config': config.mcp_servers_config,
            'parent_agents': config.parent_agents,
            'allowed_for_roles': config.allowed_for_roles,
            'tool_config': config.tool_config,
            'planner_config': config.planner_config,
            'max_iterations': config.max_iterations,
            'generate_content_config': config.generate_content_config,
            'input_schema': config.input_schema,
            'output_schema': config.output_schema,
            'include_contents': config.include_contents,
            'guardrail_config': config.guardrail_config,
            'disabled': config.disabled,
            'hardcoded': config.hardcoded,
            'project_id': config.project_id,
        }

    def _snapshot_agent_config(self, session, config, change_type: str = 'update', changed_by: str = None):
        """Create a versioned snapshot of the given AgentConfig inside an existing session."""
        try:
            from sqlalchemy import func
            max_ver = session.query(func.max(self.AgentConfigVersion.version_number)).filter(
                self.AgentConfigVersion.agent_config_id == config.id
            ).scalar() or 0

            version = self.AgentConfigVersion(
                agent_config_id=config.id,
                version_number=max_ver + 1,
                config_snapshot=json.dumps(self._build_config_snapshot(config)),
                changed_by=changed_by,
                change_type=change_type,
            )
            session.add(version)
            session.commit()
        except Exception as e:
            logger.error(f"Error creating config version snapshot: {e}")
            session.rollback()

    def _get_agent_versions(self, agent_config_id: int) -> List[Dict[str, Any]]:
        """Return all version snapshots for an agent, newest first."""
        if not self.db_client:
            return []
        session = self.db_client.get_session()
        if not session:
            return []
        try:
            versions = (
                session.query(self.AgentConfigVersion)
                .filter(self.AgentConfigVersion.agent_config_id == agent_config_id)
                .order_by(self.AgentConfigVersion.version_number.desc())
                .all()
            )
            return [v.to_dict() for v in versions]
        except Exception as e:
            logger.error(f"Error fetching agent versions: {e}")
            return []
        finally:
            session.close()

    def _rollback_agent_config(self, agent_config_id: int, version_id: int, changed_by: str = None) -> Optional[Dict[str, Any]]:
        """Restore an agent config to the state captured in *version_id*.
        Returns the updated config dict on success, None on failure."""
        if not self.db_client:
            return None
        session = self.db_client.get_session()
        if not session:
            return None
        session.rollback()
        try:
            version = session.query(self.AgentConfigVersion).filter(
                self.AgentConfigVersion.id == version_id,
                self.AgentConfigVersion.agent_config_id == agent_config_id,
            ).first()
            if not version:
                return None

            config = session.query(self.AgentConfig).filter(self.AgentConfig.id == agent_config_id).first()
            if not config:
                return None

            snapshot = version.get_snapshot()
            for key, value in snapshot.items():
                if hasattr(config, key) and key not in ('id', 'project'):
                    setattr(config, key, value)

            session.commit()
            self._snapshot_agent_config(session, config, change_type='rollback', changed_by=changed_by)
            return config.to_dict()
        except Exception as e:
            logger.error(f"Error rolling back agent config: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def _tag_agent_version(self, version_id: int, tag: str) -> bool:
        """Set or clear a tag on a config version."""
        if not self.db_client:
            return False
        session = self.db_client.get_session()
        if not session:
            return False
        session.rollback()
        try:
            version = session.query(self.AgentConfigVersion).filter(
                self.AgentConfigVersion.id == version_id
            ).first()
            if not version:
                return False
            version.tag = tag if tag else None
            session.commit()
            return True
        except Exception as e:
            logger.error(f"Error tagging agent version: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def _export_agent_configs(self, search: str = None, root_agent: str = None, project_id: Optional[int] = None) -> Dict[str, Any]:
        """Export agent configurations to JSON format with optional filtering."""
        if not self.db_client:
            return {"error": "Database connection failed"}
        
        session = self.db_client.get_session()
        if not session:
            return {"error": "Database connection failed"}
        
        try:
            from datetime import datetime
            # Get all configs first
            query = session.query(self.AgentConfig)
            if project_id is not None:
                query = query.filter(self.AgentConfig.project_id == project_id)
            all_configs = query.all()
            
            # Apply the same filtering logic as the frontend
            filtered_configs = self._apply_agent_filters(all_configs, search, root_agent)
            
            export_data = {
                "export_info": {
                    "timestamp": datetime.now().isoformat(),
                    "version": "1.0",
                    "total_agents": len(filtered_configs),
                    "filtered": search is not None or root_agent is not None,
                    "search_term": search,
                    "root_agent": root_agent,
                    "project_id": project_id
                },
                "agents": []
            }
            
            memory_blocks_project_ids = set()
            
            for config in filtered_configs:
                agent_data = {
                    "name": config.name,
                    "type": config.type,
                    "model_name": config.model_name,
                    "description": config.description,
                    "instruction": config.instruction,
                    "mcp_servers_config": config.mcp_servers_config,
                    "parent_agents": config.get_parent_agents(),
                    "allowed_for_roles": config.allowed_for_roles,
                    "tool_config": config.tool_config,
                    "guardrail_config": config.guardrail_config,
                    "project_id": config.project_id,
                    "disabled": config.disabled,
                    "hardcoded": config.hardcoded
                }
                export_data["agents"].append(agent_data)
                
                if config.tool_config:
                    try:
                        tc = json.loads(config.tool_config) if isinstance(config.tool_config, str) else config.tool_config
                        if self._has_memory_blocks(tc):
                            memory_blocks_project_ids.add(config.project_id)
                    except (json.JSONDecodeError, TypeError):
                        pass
            
            if memory_blocks_project_ids:
                from shared.utils.models import MemoryBlock
                all_blocks = []
                for pid in memory_blocks_project_ids:
                    rows = session.query(MemoryBlock).filter(
                        MemoryBlock.project_id == pid
                    ).all()
                    for row in rows:
                        block = row.to_dict()
                        block["project_id"] = pid
                        all_blocks.append(block)
                if all_blocks:
                    export_data["memory_blocks"] = all_blocks

            # Export triggers for each exported project
            trigger_project_ids = {c.project_id for c in filtered_configs if c.project_id}
            if trigger_project_ids:
                all_triggers = []
                for pid in trigger_project_ids:
                    rows = session.query(self.AgentTrigger).filter(
                        self.AgentTrigger.project_id == pid
                    ).all()
                    all_triggers.extend(r.to_dict() for r in rows)
                if all_triggers:
                    export_data["triggers"] = all_triggers

            return export_data
        except Exception as e:
            print(f"Error exporting agent configs: {e}")
            return {"error": str(e)}
        finally:
            session.close()
    
    def _apply_agent_filters(self, configs: List, search: str = None, root_agent: str = None) -> List:
        """Apply the same filtering logic as the frontend."""
        filtered_configs = []
        
        # Build hierarchy for root agent filtering
        hierarchy = self._build_agent_hierarchy(configs)
        allowed_agents = None
        
        if root_agent:
            allowed_agents = self._get_all_descendants(root_agent, hierarchy)
        
        for config in configs:
            # Text search filter
            search_matches = True
            if search:
                search_lower = search.lower()
                search_matches = (
                    search_lower in config.name.lower() or
                    search_lower in config.type.lower() or
                    search_lower in (config.model_name or "").lower() or
                    search_lower in (config.description or "").lower() or
                    any(search_lower in parent.lower() for parent in config.get_parent_agents())
                )
            
            # Root agent hierarchy filter
            hierarchy_matches = True
            if allowed_agents is not None:
                hierarchy_matches = config.name in allowed_agents
            
            # Include config if both filters match
            if search_matches and hierarchy_matches:
                filtered_configs.append(config)
        
        return filtered_configs
    
    def _build_agent_hierarchy(self, configs: List) -> Dict[str, List[str]]:
        """Build agent hierarchy map (same logic as frontend)."""
        hierarchy = {}
        for config in configs:
            parents = config.get_parent_agents()
            if parents:
                for parent in parents:
                    if parent not in hierarchy:
                        hierarchy[parent] = []
                    hierarchy[parent].append(config.name)
        return hierarchy
    
    def _get_all_descendants(self, root_agent: str, hierarchy: Dict[str, List[str]]) -> set:
        """Get all descendants of a root agent (same logic as frontend)."""
        descendants = set()
        to_process = [root_agent]
        
        while to_process:
            current = to_process.pop(0)
            if current in hierarchy:
                for child in hierarchy[current]:
                    if child not in descendants:
                        descendants.add(child)
                        to_process.append(child)
        
        # Include the root agent itself
        descendants.add(root_agent)
        return descendants
    
    def _get_adk_status(self) -> Dict[str, Any]:
        """Check if ADK server is running."""
        from shared.utils.server_control_service import ServerControlService
        service = ServerControlService(
            adk_host=self.adk_host,
            adk_port=self.adk_port,
            session_service_uri=self.session_service_uri
        )
        return service.get_adk_status()
    
    def _start_adk_server(self) -> Dict[str, Any]:
        """Start ADK server."""
        from shared.utils.server_control_service import ServerControlService
        service = ServerControlService(
            adk_host=self.adk_host,
            adk_port=self.adk_port,
            session_service_uri=self.session_service_uri
        )
        return service.start_adk_server()
    
    def _stop_adk_server(self) -> Dict[str, Any]:
        """Stop ADK server."""
        from shared.utils.server_control_service import ServerControlService
        service = ServerControlService(
            adk_host=self.adk_host,
            adk_port=self.adk_port,
            session_service_uri=self.session_service_uri
        )
        return service.stop_adk_server()
    
    def _restart_adk_server(self) -> Dict[str, Any]:
        """Restart ADK server."""
        from shared.utils.server_control_service import ServerControlService
        service = ServerControlService(
            adk_host=self.adk_host,
            adk_port=self.adk_port,
            session_service_uri=self.session_service_uri
        )
        return service.restart_adk_server()
    
    def _import_agent_configs(self, import_data: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
        """Import agent configurations from JSON format."""
        if not self.db_client:
            return {"error": "Database connection failed"}
        
        session = self.db_client.get_session()
        if not session:
            return {"error": "Database connection failed"}
        
        # Ensure session is clean
        session.rollback()
        
        try:
            import json
            if "agents" not in import_data:
                return {"error": "Invalid import format: missing 'agents' array"}
            
            imported_count = 0
            skipped_count = 0
            errors = []
            
            for agent_data in import_data["agents"]:
                try:
                    # Check if agent already exists
                    existing_agent = session.query(self.AgentConfig).filter_by(name=agent_data["name"]).first()
                    
                    if existing_agent and not overwrite:
                        skipped_count += 1
                        errors.append(f"Agent '{agent_data['name']}' already exists (use overwrite=true to replace)")
                        continue
                    
                    if existing_agent and overwrite:
                        # Update existing agent
                        for key, value in agent_data.items():
                            if hasattr(existing_agent, key):
                                # Handle parent_agents field specially - convert list to JSON string
                                if key == "parent_agents" and isinstance(value, list):
                                    setattr(existing_agent, key, json.dumps(value) if value else None)
                                elif key == "project_id":
                                    try:
                                        setattr(existing_agent, key, int(value) if value is not None else 1)
                                    except (TypeError, ValueError):
                                        setattr(existing_agent, key, 1)
                                else:
                                    if key in ["description", "instruction"]:
                                        value = sanitize_agent_text(value)
                                    setattr(existing_agent, key, value)
                        imported_count += 1
                    else:
                        # Create new agent
                        parent_agents = agent_data.get("parent_agents", [])
                        parent_agents_json = json.dumps(parent_agents) if parent_agents else None
                        
                        new_agent = self.AgentConfig(
                            name=agent_data["name"],
                            type=agent_data["type"],
                            model_name=agent_data.get("model_name"),
                            description=sanitize_agent_text(agent_data.get("description")),
                            instruction=sanitize_agent_text(agent_data.get("instruction")),
                            mcp_servers_config=agent_data.get("mcp_servers_config"),
                            parent_agents=parent_agents_json,
                            allowed_for_roles=agent_data.get("allowed_for_roles"),
                            tool_config=agent_data.get("tool_config"),
                            guardrail_config=agent_data.get("guardrail_config"),
                            project_id=int(agent_data.get("project_id") or 1),
                            disabled=agent_data.get("disabled", False),
                            hardcoded=agent_data.get("hardcoded", False)
                        )
                        session.add(new_agent)
                        imported_count += 1
                        
                except Exception as e:
                    errors.append(f"Error importing agent '{agent_data.get('name', 'Unknown')}': {str(e)}")
            
            session.commit()
            
            memory_blocks_imported = 0
            memory_blocks_skipped = 0
            if "memory_blocks" in import_data and import_data["memory_blocks"]:
                try:
                    from shared.utils.models import MemoryBlock
                    for block_data in import_data["memory_blocks"]:
                        try:
                            block_project_id = int(block_data.get("project_id") or 1)
                            block_label = block_data.get("label", "").strip()
                            if not block_label:
                                continue
                            
                            existing = session.query(MemoryBlock).filter(
                                MemoryBlock.project_id == block_project_id,
                                MemoryBlock.label == block_label,
                            ).first()
                            
                            if existing:
                                if overwrite:
                                    existing.value = block_data.get("value", "")
                                    existing.description = block_data.get("description")
                                    md = block_data.get("metadata")
                                    existing.set_metadata(md if isinstance(md, dict) else None)
                                    memory_blocks_imported += 1
                                else:
                                    memory_blocks_skipped += 1
                            else:
                                new_block = MemoryBlock(
                                    project_id=block_project_id,
                                    label=block_label,
                                    value=block_data.get("value", ""),
                                    description=block_data.get("description"),
                                )
                                md = block_data.get("metadata")
                                if isinstance(md, dict):
                                    new_block.set_metadata(md)
                                session.add(new_block)
                                memory_blocks_imported += 1
                        except Exception as e:
                            errors.append(f"Error importing memory block '{block_data.get('label', 'Unknown')}': {str(e)}")
                    
                    session.commit()
                except Exception as e:
                    session.rollback()
                    errors.append(f"Error importing memory blocks: {str(e)}")

            # Import triggers — start disabled, clear webhook path and fire key (security)
            triggers_imported = 0
            if "triggers" in import_data and import_data["triggers"]:
                try:
                    for tdata in import_data["triggers"]:
                        try:
                            trigger = self.AgentTrigger(
                                name=tdata.get("name", "imported_trigger"),
                                description=tdata.get("description"),
                                trigger_type=tdata.get("trigger_type", "cron"),
                                agent_name=tdata.get("agent_name", ""),
                                project_id=1,  # will be overridden below if project mapping exists
                                prompt=tdata.get("prompt", ""),
                                cron_expression=tdata.get("cron_expression"),
                                webhook_path=None,    # regenerate — never carry over old path
                                fire_key_hash=None,   # regenerate when user enables the trigger
                                output_type=tdata.get("output_type", "memory_block"),
                                is_enabled=False,     # imported triggers start disabled
                            )
                            trigger.set_output_config(tdata.get("output_config") or {})
                            session.add(trigger)
                            triggers_imported += 1
                        except Exception as e:
                            errors.append(f"Error importing trigger '{tdata.get('name', 'Unknown')}': {str(e)}")
                    session.commit()
                except Exception as e:
                    session.rollback()
                    errors.append(f"Error importing triggers: {str(e)}")

            result = {
                "success": True,
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "errors": errors
            }
            if memory_blocks_imported or memory_blocks_skipped:
                result["memory_blocks_imported"] = memory_blocks_imported
                result["memory_blocks_skipped"] = memory_blocks_skipped
            if triggers_imported:
                result["triggers_imported"] = triggers_imported
            return result
            
        except Exception as e:
            session.rollback()
            print(f"Error importing agent configs: {e}")
            return {"error": str(e)}
        finally:
            session.close()
    
    def _import_template(self, template_id: Optional[str] = None, project_name: Optional[str] = None,
                         changed_by: str = None, template_dict: Optional[Dict[str, Any]] = None,
                         substitutions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Import a template: create project, agents, memory blocks. Apply name prefix to avoid collisions.

        Either ``template_id`` (loaded from the template library) or a pre-loaded ``template_dict``
        must be provided. ``substitutions`` is an optional map applied to every ``{{KEY}}`` placeholder
        in all string fields before import (used by the public wizard to inject per-prospect values
        such as the website URL or store domain).
        """
        if template_dict is not None:
            template = template_dict
        else:
            template = self.template_service.get_template(template_id)
        if not template:
            return {"error": f"Template not found: {template_id}"}

        # Apply per-prospect substitutions ({{KEY}} placeholders) across all string fields
        if substitutions:
            def _apply_subs(obj):
                if isinstance(obj, str):
                    for k, v in substitutions.items():
                        obj = obj.replace("{{" + k + "}}", str(v))
                    return obj
                if isinstance(obj, list):
                    return [_apply_subs(x) for x in obj]
                if isinstance(obj, dict):
                    return {k: _apply_subs(val) for k, val in obj.items()}
                return obj
            template = _apply_subs(template)

        project_data = template.get("project") or {}
        proj_name = (project_name or project_data.get("name") or "Imported Project").strip()
        if not proj_name:
            return {"error": "Project name is required"}
        
        # Create project
        try:
            project = self._create_project(proj_name, project_data.get("description"))
        except HTTPException as e:
            return {"error": str(e.detail)}
        
        project_id = project["id"]
        slug = self.template_service.slugify_project_name(proj_name)
        template_meta = template.get("template_meta") or {}
        agent_prefix = template_meta.get("agent_prefix") or "tpl"
        replace_with = f"{slug}_" if agent_prefix.endswith("_") else slug
        
        # Store template provenance on the project for future sync/upgrade
        try:
            session = self.db_client.get_session()
            proj = session.query(self.Project).filter(self.Project.id == project_id).first()
            if proj:
                proj.template_id = template_id or template_meta.get("id")
                proj.template_version = template_meta.get("version")
                proj.template_prefix = agent_prefix
                session.commit()
            session.close()
        except Exception as e:
            logger.warning(f"Failed to store template provenance for project {project_id}: {e}")
        
        # Build replacement map: old_agent_name -> new_agent_name
        agents_data = template.get("agents") or []
        name_map = {}
        for a in agents_data:
            old_name = a.get("name", "")
            if old_name:
                new_name = old_name.replace(agent_prefix, replace_with, 1) if agent_prefix in old_name else f"{slug}_{old_name}"
                name_map[old_name] = new_name

        # Resolve the mapped root agent name (entry point for widget keys / ADK)
        root_agent_raw = template_meta.get("root_agent")
        root_agent_name = name_map.get(root_agent_raw, root_agent_raw)

        # Substitute in agent names and parent_agents
        def sub_names(obj):
            if isinstance(obj, str):
                for old, new in name_map.items():
                    obj = obj.replace(old, new)
                return obj
            if isinstance(obj, list):
                return [sub_names(x) for x in obj]
            if isinstance(obj, dict):
                return {k: sub_names(v) for k, v in obj.items()}
            return obj
        
        # Create agents in order: root first (no parents), then children
        agents_created = 0
        ordered = sorted(agents_data, key=lambda a: (len(a.get("parent_agents") or []), a.get("name", "")))
        
        for agent_data in ordered:
            old_name = agent_data.get("name", "")
            new_name = name_map.get(old_name, old_name)
            config_data = {
                "name": new_name,
                "type": agent_data.get("type", "llm"),
                "model_name": agent_data.get("model_name"),
                "description": agent_data.get("description"),
                "instruction": sub_names(agent_data.get("instruction") or ""),
                "parent_agents": sub_names(agent_data.get("parent_agents") or []),
                "allowed_for_roles": agent_data.get("allowed_for_roles"),
                "tool_config": agent_data.get("tool_config"),
                "mcp_servers_config": agent_data.get("mcp_servers_config"),
                "guardrail_config": agent_data.get("guardrail_config"),
                "project_id": project_id,
                "disabled": agent_data.get("disabled", False),
                "hardcoded": agent_data.get("hardcoded", False),
                "expose_as_model": agent_data.get("expose_as_model", False),
            }
            if isinstance(config_data["allowed_for_roles"], str):
                try:
                    config_data["allowed_for_roles"] = json.loads(config_data["allowed_for_roles"])
                except json.JSONDecodeError:
                    pass
            if self._create_agent_config(config_data, changed_by=changed_by):
                agents_created += 1
                # Create agent folder for root agent only (ADK entry point)
                if not (agent_data.get("parent_agents") or []):
                    self._copy_template_agent(new_name)
        
        # Create memory blocks
        memory_blocks_created = 0
        from shared.utils.memory_blocks_service import MemoryBlocksService
        mem_service = MemoryBlocksService(self.db_client)
        for block in template.get("memory_blocks") or []:
            label = sub_names(block.get("label", "").strip())
            if not label:
                continue
            value = sub_names(block.get("value", ""))
            desc = block.get("description")
            result = mem_service.create_block(project_id=project_id, label=label, value=value, description=desc)
            if result.get("status") == "success":
                memory_blocks_created += 1
        
        # Reload all agents
        try:
            from shared.utils.utils import get_adk_config
            import httpx
            adk_config = get_adk_config()
            adk_url = f"http://{adk_config['adk_host']}:{adk_config['adk_port']}/api/reload-all-agents"
            with httpx.Client(timeout=30.0) as client:
                client.post(adk_url)
        except Exception:
            pass
        
        return {
            "success": True,
            "project_id": project_id,
            "project_name": proj_name,
            "root_agent_name": root_agent_name,
            "agents_created": agents_created,
            "memory_blocks_created": memory_blocks_created,
        }
    
    def _create_template_from_agents(
        self,
        project_id: int,
        root_agent: str,
        template_id: str,
        template_name: str,
        description: str = "",
        category: str = "custom",
    ) -> Dict[str, Any]:
        """Create a template JSON file from existing agents (project + root agent hierarchy)."""
        export_data = self._export_agent_configs(project_id=project_id, root_agent=root_agent)
        if "error" in export_data:
            return {"error": export_data["error"]}
        
        agents = export_data.get("agents") or []
        memory_blocks = export_data.get("memory_blocks") or []
        if not agents:
            return {"error": "No agents found for the selected project and root agent"}
        
        # Get project info
        session = self.db_client.get_session()
        if not session:
            return {"error": "Database connection failed"}
        try:
            project = session.query(self.Project).filter(self.Project.id == project_id).first()
            project_name = project.name if project else "Imported Project"
            project_desc = project.description if project else ""
        finally:
            session.close()
        
        # Derive agent_prefix (longest common prefix of agent names)
        agent_names = [a.get("name", "") for a in agents if a.get("name")]
        agent_prefix = self.template_service.longest_common_prefix(agent_names)
        if not agent_prefix:
            agent_prefix = "tpl"
        
        # Build template (strip project_id from agents/blocks - template uses it at import time)
        template_agents = []
        for a in agents:
            agent_copy = {k: v for k, v in a.items() if k != "project_id"}
            template_agents.append(agent_copy)
        
        template_blocks = []
        for b in memory_blocks:
            block_copy = {k: v for k, v in b.items() if k != "project_id"}
            template_blocks.append(block_copy)
        
        template_data = {
            "template_meta": {
                "id": template_id,
                "name": template_name,
                "description": description or f"Template created from {project_name}",
                "category": category,
                "version": "1.0",
                "compatibility_tags": ["memory_blocks"],
                "root_agent": root_agent,
                "agent_prefix": agent_prefix,
            },
            "project": {
                "name": project_name,
                "description": project_desc,
            },
            "agents": template_agents,
            "memory_blocks": template_blocks,
        }
        
        # Sanitize template_id for filename
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in template_id).strip("_") or "template"
        
        try:
            path = self.template_service.save_template(safe_id, template_data)
            return {"success": True, "template_id": safe_id, "path": path}
        except Exception as e:
            return {"error": f"Failed to save template: {str(e)}"}
    
    def _get_template_sync_status(self, project_id: int) -> Dict[str, Any]:
        """Get sync status: compare project agents with source template to find differences."""
        if not self.db_client:
            return {"error": "Database connection failed"}
        
        session = self.db_client.get_session()
        if not session:
            return {"error": "Database connection failed"}
        
        try:
            project = session.query(self.Project).filter(self.Project.id == project_id).first()
            if not project:
                return {"error": f"Project not found: {project_id}"}
            
            if not project.template_id:
                return {"error": "Project was not created from a template"}
            
            template = self.template_service.get_template(project.template_id)
            if not template:
                return {"error": f"Template not found: {project.template_id}"}
            
            template_meta = template.get("template_meta") or {}
            latest_version = template_meta.get("version", "unknown")
            current_version = project.template_version or "unknown"
            
            # Build name map (template name -> project name)
            slug = self.template_service.slugify_project_name(project.name)
            tpl_prefix = project.template_prefix or template_meta.get("agent_prefix") or "tpl"
            replace_with = f"{slug}_" if tpl_prefix.endswith("_") else slug
            
            template_agents = template.get("agents") or []
            name_map = {}
            for a in template_agents:
                old_name = a.get("name", "")
                if old_name:
                    new_name = old_name.replace(tpl_prefix, replace_with, 1) if tpl_prefix in old_name else f"{slug}_{old_name}"
                    name_map[old_name] = new_name
            
            # Get existing DB agents for this project
            db_agents = session.query(self.AgentConfig).filter(
                self.AgentConfig.project_id == project_id
            ).all()
            db_agent_names = {a.name for a in db_agents}
            
            agents_to_add = []
            agents_to_update = []
            
            def sub_names(text):
                if isinstance(text, str):
                    for old, new in name_map.items():
                        text = text.replace(old, new)
                return text

            for tpl_agent in template_agents:
                tpl_name = tpl_agent.get("name", "")
                proj_name = name_map.get(tpl_name, tpl_name)
                
                if proj_name not in db_agent_names:
                    agents_to_add.append({"template_name": tpl_name, "project_name": proj_name})
                else:
                    # Check if instruction/description/mcp changed
                    db_agent = next((a for a in db_agents if a.name == proj_name), None)
                    if db_agent:
                        changes = []
                        
                        def to_json_str(val):
                            if val is None:
                                return ""
                            if isinstance(val, (dict, list)):
                                return json.dumps(val, sort_keys=True)
                            return str(val)
                            
                        # Apply name mapping to template strings before comparing
                        tpl_instr = sub_names(tpl_agent.get("instruction") or "")
                        db_instr = db_agent.instruction or ""
                        if tpl_instr != db_instr:
                            changes.append("instruction")
                            
                        tpl_desc = sub_names(tpl_agent.get("description") or "")
                        db_desc = db_agent.description or ""
                        if tpl_desc != db_desc:
                            changes.append("description")
                            
                        # Compare JSON structure properly, applying mapping
                        tpl_mcp_raw = tpl_agent.get("mcp_servers_config")
                        if isinstance(tpl_mcp_raw, str) and tpl_mcp_raw:
                            try:
                                tpl_mcp_raw = json.loads(sub_names(tpl_mcp_raw))
                            except json.JSONDecodeError:
                                tpl_mcp_raw = sub_names(tpl_mcp_raw)
                        tpl_mcp = to_json_str(tpl_mcp_raw)
                        
                        db_mcp_raw = db_agent.mcp_servers_config if isinstance(db_agent.mcp_servers_config, (dict, list)) else json.loads(db_agent.mcp_servers_config) if db_agent.mcp_servers_config else ""
                        db_mcp = to_json_str(db_mcp_raw)
                        if tpl_mcp != db_mcp:
                            changes.append("mcp_servers_config")
                            
                        tpl_tool_raw = tpl_agent.get("tool_config")
                        if isinstance(tpl_tool_raw, str) and tpl_tool_raw:
                            try:
                                tpl_tool_raw = json.loads(sub_names(tpl_tool_raw))
                            except json.JSONDecodeError:
                                tpl_tool_raw = sub_names(tpl_tool_raw)
                        tpl_tool = to_json_str(tpl_tool_raw)
                        
                        db_tool_raw = db_agent.tool_config if isinstance(db_agent.tool_config, (dict, list)) else json.loads(db_agent.tool_config) if db_agent.tool_config else ""
                        db_tool = to_json_str(db_tool_raw)
                        if tpl_tool != db_tool:
                            changes.append("tool_config")
                            
                        if changes:
                            # Return simple string so JS UI can render it directly
                            agents_to_update.append(f"{proj_name} (changes: {', '.join(changes)})")
            
            # Check memory blocks
            template_blocks = template.get("memory_blocks") or []
            from shared.utils.models import MemoryBlock
            db_blocks = session.query(MemoryBlock).filter(
                MemoryBlock.project_id == project_id
            ).all()
            db_block_labels = {b.label for b in db_blocks}
            
            def sub_names(text):
                if isinstance(text, str):
                    for old, new in name_map.items():
                        text = text.replace(old, new)
                return text
            
            blocks_to_add = []
            blocks_to_update = []
            for block in template_blocks:
                label = sub_names(block.get("label", "").strip())
                if not label:
                    continue
                if label not in db_block_labels:
                    blocks_to_add.append(label)
                else:
                    db_block = next((b for b in db_blocks if b.label == label), None)
                    if db_block and sub_names(block.get("value", "")) != (db_block.value or ""):
                        blocks_to_update.append(label)
            
            return {
                "project_id": project_id,
                "project_name": project.name,
                "template_id": project.template_id,
                "current_version": current_version,
                "latest_version": latest_version,
                "up_to_date": current_version == latest_version and not agents_to_add and not agents_to_update and not blocks_to_add and not blocks_to_update,
                "agents_to_add": agents_to_add,
                "agents_to_update": agents_to_update,
                "memory_blocks_to_add": blocks_to_add,
                "memory_blocks_to_update": blocks_to_update,
            }
        except Exception as e:
            logger.error(f"Error getting template sync status: {e}")
            return {"error": str(e)}
        finally:
            session.close()
    
    def _sync_template(self, project_id: int, changed_by: str = None) -> Dict[str, Any]:
        """Sync a project with its source template: add new agents, update changed agents, add/update memory blocks."""
        if not self.db_client:
            return {"error": "Database connection failed"}
        
        session = self.db_client.get_session()
        if not session:
            return {"error": "Database connection failed"}
        
        session.rollback()
        
        try:
            project = session.query(self.Project).filter(self.Project.id == project_id).first()
            if not project:
                return {"error": f"Project not found: {project_id}"}
            if not project.template_id:
                return {"error": "Project was not created from a template"}
            
            template = self.template_service.get_template(project.template_id)
            if not template:
                return {"error": f"Template not found: {project.template_id}"}
            
            template_meta = template.get("template_meta") or {}
            
            # Build name map
            slug = self.template_service.slugify_project_name(project.name)
            tpl_prefix = project.template_prefix or template_meta.get("agent_prefix") or "tpl"
            replace_with = f"{slug}_" if tpl_prefix.endswith("_") else slug
            
            template_agents = template.get("agents") or []
            name_map = {}
            for a in template_agents:
                old_name = a.get("name", "")
                if old_name:
                    new_name = old_name.replace(tpl_prefix, replace_with, 1) if tpl_prefix in old_name else f"{slug}_{old_name}"
                    name_map[old_name] = new_name
            
            def sub_names(obj):
                if isinstance(obj, str):
                    for old, new in name_map.items():
                        obj = obj.replace(old, new)
                    return obj
                if isinstance(obj, list):
                    return [sub_names(x) for x in obj]
                if isinstance(obj, dict):
                    return {k: sub_names(v) for k, v in obj.items()}
                return obj
            
            # Get existing DB agents
            db_agents = session.query(self.AgentConfig).filter(
                self.AgentConfig.project_id == project_id
            ).all()
            db_agent_map = {a.name: a for a in db_agents}
            
            agents_added = 0
            agents_updated = 0
            
            # Sort: root agents first
            ordered = sorted(template_agents, key=lambda a: (len(a.get("parent_agents") or []), a.get("name", "")))
            
            for tpl_agent in ordered:
                tpl_name = tpl_agent.get("name", "")
                proj_name = name_map.get(tpl_name, tpl_name)
                
                if proj_name not in db_agent_map:
                    # Add new agent
                    config_data = {
                        "name": proj_name,
                        "type": tpl_agent.get("type", "llm"),
                        "model_name": tpl_agent.get("model_name"),
                        "description": tpl_agent.get("description"),
                        "instruction": sub_names(tpl_agent.get("instruction") or ""),
                        "parent_agents": sub_names(tpl_agent.get("parent_agents") or []),
                        "allowed_for_roles": tpl_agent.get("allowed_for_roles"),
                        "tool_config": tpl_agent.get("tool_config"),
                        "mcp_servers_config": tpl_agent.get("mcp_servers_config"),
                        "guardrail_config": tpl_agent.get("guardrail_config"),
                        "project_id": project_id,
                        "disabled": tpl_agent.get("disabled", False),
                        "hardcoded": tpl_agent.get("hardcoded", False),
                    }
                    if isinstance(config_data["allowed_for_roles"], str):
                        try:
                            config_data["allowed_for_roles"] = json.loads(config_data["allowed_for_roles"])
                        except json.JSONDecodeError:
                            pass
                    if self._create_agent_config(config_data, changed_by=changed_by):
                        agents_added += 1
                        # Create agent folder for root agent only
                        if not (tpl_agent.get("parent_agents") or []):
                            self._copy_template_agent(proj_name)
                else:
                    # Update existing agent — preserve user customizations
                    db_agent = db_agent_map[proj_name]
                    changed = False
                    
                    def to_json_str(val):
                        if val is None:
                            return ""
                        if isinstance(val, (dict, list)):
                            return json.dumps(val, sort_keys=True)
                        return str(val)
                    
                    new_instruction = sub_names(tpl_agent.get("instruction") or "")
                    if new_instruction and new_instruction != (db_agent.instruction or ""):
                        db_agent.instruction = new_instruction
                        changed = True
                    
                    new_desc = sub_names(tpl_agent.get("description") or "")
                    if new_desc and new_desc != (db_agent.description or ""):
                        db_agent.description = new_desc
                        changed = True
                    
                    # Convert template MCP to string, map names, and set
                    tpl_mcp_raw = tpl_agent.get("mcp_servers_config")
                    if isinstance(tpl_mcp_raw, str):
                        tpl_mcp_raw = sub_names(tpl_mcp_raw)
                    new_mcp = to_json_str(tpl_mcp_raw)
                    
                    db_mcp_raw = db_agent.mcp_servers_config if isinstance(db_agent.mcp_servers_config, (dict, list)) else json.loads(db_agent.mcp_servers_config) if db_agent.mcp_servers_config else ""
                    db_mcp = to_json_str(db_mcp_raw)
                    if new_mcp and new_mcp != db_mcp:
                        db_agent.mcp_servers_config = new_mcp
                        changed = True
                    
                    # Convert template Tool to string, map names, and set
                    tpl_tool_raw = tpl_agent.get("tool_config")
                    if isinstance(tpl_tool_raw, str):
                        tpl_tool_raw = sub_names(tpl_tool_raw)
                    new_tool = to_json_str(tpl_tool_raw)
                    
                    db_tool_raw = db_agent.tool_config if isinstance(db_agent.tool_config, (dict, list)) else json.loads(db_agent.tool_config) if db_agent.tool_config else ""
                    db_tool = to_json_str(db_tool_raw)
                    if new_tool and new_tool != db_tool:
                        db_agent.tool_config = new_tool
                        changed = True
                    
                    # Update parent_agents if new agents were added
                    new_parents = sub_names(tpl_agent.get("parent_agents") or [])
                    if new_parents:
                        new_parents_json = json.dumps(new_parents) if isinstance(new_parents, list) else new_parents
                        if new_parents_json != (db_agent.parent_agents or ""):
                            db_agent.parent_agents = new_parents_json
                            changed = True
                    
                    if changed:
                        agents_updated += 1
            
            session.commit()
            
            # Sync memory blocks
            memory_blocks_added = 0
            memory_blocks_updated = 0
            from shared.utils.memory_blocks_service import MemoryBlocksService
            from shared.utils.models import MemoryBlock
            mem_service = MemoryBlocksService(self.db_client)
            
            db_blocks = session.query(MemoryBlock).filter(
                MemoryBlock.project_id == project_id
            ).all()
            db_block_map = {b.label: b for b in db_blocks}
            
            for block in template.get("memory_blocks") or []:
                label = sub_names(block.get("label", "").strip())
                if not label:
                    continue
                value = sub_names(block.get("value", ""))
                desc = block.get("description")
                
                if label not in db_block_map:
                    result = mem_service.create_block(project_id=project_id, label=label, value=value, description=desc)
                    if result.get("status") == "success":
                        memory_blocks_added += 1
                else:
                    # Update existing block value
                    existing = db_block_map[label]
                    if value != (existing.value or ""):
                        existing.value = value
                        if desc:
                            existing.description = desc
                        memory_blocks_updated += 1
            
            # Update project template version
            project.template_version = template_meta.get("version")
            session.commit()
            
            # Reload agents
            try:
                from shared.utils.utils import get_adk_config
                import httpx
                adk_config = get_adk_config()
                adk_url = f"http://{adk_config['adk_host']}:{adk_config['adk_port']}/api/reload-all-agents"
                with httpx.Client(timeout=30.0) as client:
                    client.post(adk_url)
            except Exception:
                pass
            
            return {
                "success": True,
                "project_id": project_id,
                "template_id": project.template_id,
                "synced_to_version": template_meta.get("version"),
                "agents_added": agents_added,
                "agents_updated": agents_updated,
                "memory_blocks_added": memory_blocks_added,
                "memory_blocks_updated": memory_blocks_updated,
            }
        except Exception as e:
            session.rollback()
            logger.error(f"Error syncing template: {e}")
            return {"error": str(e)}
        finally:
            session.close()
    
    def _setup_templates_and_static(self):
        """Setup templates and static file serving"""
        templates_dir = self.project_root / "templates"
        static_dir = self.project_root / "static"
        
        self.templates = Jinja2Templates(directory=str(templates_dir))
        
        # Mount static files
        self.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    def _get_auth_user_dependency(self, request: Request):
        """Get authenticated user for dashboard routes without triggering browser popup.
        Uses auth function from auth_server."""
        # Import here to avoid circular imports
        from server.auth import require_dashboard_auth
        return require_dashboard_auth(request)

    def _get_is_admin(self, request: Request) -> bool:
        """Return True if the current request comes from an admin user."""
        from server.auth import is_admin_user
        return is_admin_user(request)

    def _has_memory_blocks(self, tool_config_dict):
        """Return True if agent has memory_blocks tool enabled."""
        memory_blocks = tool_config_dict.get('memory_blocks')
        if not memory_blocks:
            return False
        if memory_blocks is True:
            return True
        if isinstance(memory_blocks, dict):
            return memory_blocks.get('enabled', True) is not False
        return False

    def _register_endpoints(self):
        """Register all dashboard endpoints"""
        
        # Dashboard page endpoints
        @self.app.get("/dashboard", tags=["Dashboard - Pages"], include_in_schema=False)
        async def dashboard_index(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Redirect to Work Room (default space)."""
            return RedirectResponse(url="/dashboard/workroom", status_code=302)

        @self.app.get("/dashboard/overview", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_overview(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Platform Overview — Control Room landing page."""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            stats = self._get_usage_stats(7)
            return self.templates.TemplateResponse(request, "dashboard/index.html", {
                "request": request,
                "page_title": "Platform Overview",
                "username": username,
                "stats": stats,
                "is_admin": True,
            })

        @self.app.get("/dashboard/users", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_users(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard users page"""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            users = self._get_all_users()
            return self.templates.TemplateResponse(request, "dashboard/users.html", {
                "request": request,
                "page_title": "User Management",
                "username": username,
                "users": users,
                "is_admin": True,
            })

        @self.app.get("/dashboard/agents", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_agents(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard agents page"""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            project_param = request.query_params.get("project_id")
            try:
                selected_project_id = int(project_param) if project_param else None
            except (TypeError, ValueError):
                selected_project_id = None

            projects = self._get_all_projects()
            configs = self._get_all_agent_configs(selected_project_id) if selected_project_id else []
            return self.templates.TemplateResponse(request, "dashboard/agents.html", {
                "request": request,
                "page_title": "Agent Management",
                "username": username,
                "configs": configs,
                "projects": projects,
                "selected_project_id": selected_project_id,
                "is_admin": True,
            })

        @self.app.get("/dashboard/agents/visual", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_agents_visual(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard visual agent builder page"""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            project_param = request.query_params.get("project_id")
            try:
                selected_project_id = int(project_param) if project_param else None
            except (TypeError, ValueError):
                selected_project_id = None

            projects = self._get_all_projects()
            configs = self._get_all_agent_configs(selected_project_id) if selected_project_id else []
            return self.templates.TemplateResponse(request, "dashboard/agents_visual.html", {
                "request": request,
                "page_title": "Agent Visual Builder",
                "username": username,
                "configs": configs,
                "projects": projects,
                "selected_project_id": selected_project_id,
                "is_admin": True,
            })

        @self.app.get("/dashboard/templates", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_templates(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard templates gallery page"""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/templates.html", {
                "request": request,
                "page_title": "Template Library",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/migrations", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_migrations(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard migrations page"""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            migrations = self._get_schema_migrations()
            return self.templates.TemplateResponse(request, "dashboard/migrations.html", {
                "request": request,
                "page_title": "Database Migrations",
                "username": username,
                "migrations": migrations,
                "is_admin": True,
            })

        @self.app.get("/dashboard/usage", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_usage(request: Request, days: int = 30, view: str = "analytics", username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard usage page"""
            is_admin = self._get_is_admin(request)
            stats = self._get_usage_stats(days)
            logs = self._get_token_usage_logs(24, limit=1000) if view == "logs" else {"logs": []}
            # Non-admin users see only their own usage
            current_user_id = username
            return self.templates.TemplateResponse(request, "dashboard/usage.html", {
                "request": request,
                "page_title": "Usage Analytics",
                "username": username,
                "stats": stats,
                "logs": logs.get("logs", []) if isinstance(logs, dict) else [],
                "days": days,
                "view": view,
                "is_admin": is_admin,
                "current_user_id": current_user_id,
            })

        @self.app.get("/dashboard/rate-limits", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_rate_limits(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard rate limits and budgets page"""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/rate_limits.html", {
                "request": request,
                "page_title": "Rate Limits & Budgets",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/traces", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_traces(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard traces page - OpenTelemetry distributed tracing viewer"""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/traces.html", {
                "request": request,
                "page_title": "Traces",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/docs", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_docs(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard docs page"""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            # Extract hostname from request
            host = request.headers.get("host", "localhost").split(":")[0]
            return self.templates.TemplateResponse(request, "dashboard/docs.html", {
                "request": request,
                "page_title": "Documentation",
                "username": username,
                "adk_host": host,
                "adk_port": 8000,
                "is_admin": True,
            })

        @self.app.get("/dashboard/audit-logs", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_audit_logs(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard audit log viewer (EU AI Act compliance)."""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/audit_logs.html", {
                "request": request,
                "page_title": "Audit Logs",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/guardrail-logs", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_guardrail_logs(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard guardrail trigger log viewer."""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/guardrail_logs.html", {
                "request": request,
                "page_title": "Guardrail Logs",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/wizard-leads", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_wizard_leads(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard page listing leads captured by the public Agent Builder Wizard."""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/wizard_leads.html", {
                "request": request,
                "page_title": "Wizard Leads",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/api/wizard/leads", tags=["Dashboard - Wizard"])
        async def get_wizard_leads(request: Request, username: str = Depends(self._get_auth_user_dependency),
                                   status: Optional[str] = None, search: Optional[str] = None,
                                   partner: Optional[str] = None, limit: int = 200):
            """List wizard leads: NEW first, then newest first. Optional status/partner filter + text search."""
            from shared.utils.models import WizardLead, WizardSession, WidgetApiKey
            from sqlalchemy import case, or_
            session = self.db_client.get_session()
            try:
                q = session.query(WizardLead)
                if status:
                    q = q.filter(WizardLead.status == status)
                if partner:
                    q = q.filter(WizardLead.partner_key == partner)
                if search:
                    like = f"%{search.strip()}%"
                    q = q.filter(or_(
                        WizardLead.name.ilike(like),
                        WizardLead.email.ilike(like),
                        WizardLead.company.ilike(like),
                    ))
                new_first = case((WizardLead.status == 'new', 0), else_=1)
                leads = q.order_by(new_first, WizardLead.created_at.desc()).limit(limit).all()

                # Flag whether each trial's widget key is still live (not yet TTL-cleaned).
                keys = [l.trial_widget_key for l in leads if l.trial_widget_key]
                alive = set()
                if keys:
                    rows = session.query(WidgetApiKey.api_key).filter(
                        WidgetApiKey.api_key.in_(keys), WidgetApiKey.is_active == True  # noqa: E712
                    ).all()
                    alive = {r[0] for r in rows}

                # Fetch root_agent_name from the linked wizard sessions in one query.
                session_ids = [l.wizard_session_id for l in leads if l.wizard_session_id]
                agent_name_by_session: dict = {}
                if session_ids:
                    ws_rows = session.query(WizardSession.id, WizardSession.root_agent_name).filter(
                        WizardSession.id.in_(session_ids)
                    ).all()
                    agent_name_by_session = {r[0]: r[1] for r in ws_rows}

                out = []
                for l in leads:
                    d = l.to_dict()
                    d["trial_alive"] = bool(l.trial_widget_key and l.trial_widget_key in alive)
                    d["trial_agent_name"] = agent_name_by_session.get(l.wizard_session_id)
                    out.append(d)
                return {"leads": out}
            finally:
                session.close()

        @self.app.patch("/dashboard/api/wizard/leads/{lead_id}", tags=["Dashboard - Wizard"])
        async def update_wizard_lead(lead_id: int, request: Request,
                                     username: str = Depends(self._get_auth_user_dependency)):
            """Update a lead's status (new|contacted|converted|archived)."""
            from shared.utils.models import WizardLead
            body = await request.json()
            new_status = (body.get("status") or "").strip()
            if new_status not in {"new", "contacted", "converted", "archived"}:
                raise HTTPException(status_code=400, detail="Invalid status")
            session = self.db_client.get_session()
            try:
                lead = session.query(WizardLead).filter(WizardLead.id == lead_id).first()
                if not lead:
                    raise HTTPException(status_code=404, detail="Lead not found")
                lead.status = new_status

                # Archiving frees the trial immediately — but snapshot the agent first so
                # it isn't lost, then delete the trial project/agent/key. The lead survives.
                release_pid = None
                if new_status == "archived" and lead.trial_project_id:
                    if not lead.agent_snapshot:
                        try:
                            export = self._export_agent_configs(project_id=lead.trial_project_id)
                            if export and "error" not in export:
                                lead.agent_snapshot = json.dumps(export)
                        except Exception as exc:
                            logger.warning("Snapshot before archive failed: %s", exc)
                    release_pid = lead.trial_project_id

                session.commit()

                released = False
                if release_pid:
                    from shared.utils.wizard.cleanup import release_trial
                    released = bool(release_trial(release_pid).get("released"))
                    session.refresh(lead)

                return {"success": True, "lead": lead.to_dict(), "trial_released": released}
            except HTTPException:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(exc))
            finally:
                session.close()

        @self.app.get("/dashboard/wizard-pricing", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_wizard_pricing(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard page to edit Agent Builder Wizard tier prices and currencies."""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/wizard_pricing.html", {
                "request": request,
                "page_title": "Wizard Pricing",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/api/wizard/pricing", tags=["Dashboard - Wizard"])
        async def get_wizard_pricing(request: Request, username: str = Depends(self._get_auth_user_dependency),
                                     partner: Optional[str] = None):
            """Return the effective wizard pricing config (global or for a partner)."""
            from shared.utils.wizard import pricing
            return pricing.get_pricing_config_for_admin(partner or None)

        @self.app.put("/dashboard/api/wizard/pricing", tags=["Dashboard - Wizard"])
        async def update_wizard_pricing(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Save wizard tier prices + default currency, global or for a partner (admin only)."""
            if not self._get_is_admin(request):
                raise HTTPException(status_code=403, detail="Admin only")
            from shared.utils.wizard import pricing
            body = await request.json()
            try:
                saved = pricing.save_pricing_config(
                    self.db_client,
                    default_currency=body.get("default_currency"),
                    prices=body.get("prices") or {},
                    show_from=body.get("show_from") or {},
                    partner=(body.get("partner") or "").strip() or None,
                )
                return {"success": True, **saved}
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

        @self.app.get("/dashboard/api/wizard/partners", tags=["Dashboard - Wizard"])
        async def list_wizard_partners(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """List embedding partners/sites."""
            from shared.utils.wizard import partners as partners_svc
            return {"partners": partners_svc.list_partners()}

        @self.app.post("/dashboard/api/wizard/partners", tags=["Dashboard - Wizard"])
        async def upsert_wizard_partner(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Create or update a partner (admin only)."""
            if not self._get_is_admin(request):
                raise HTTPException(status_code=403, detail="Admin only")
            from shared.utils.wizard import partners as partners_svc
            body = await request.json()
            res = partners_svc.upsert_partner(
                partner_key=(body.get("partner_key") or "").strip(),
                name=body.get("name"),
                allowed_origins=body.get("allowed_origins") if body.get("allowed_origins") is not None else None,
                contact_email=body.get("contact_email"),
                default_lang=body.get("default_lang"),
                is_active=body.get("is_active", True),
            )
            if res.get("error"):
                raise HTTPException(status_code=400, detail=res["error"])
            return {"success": True, "partner": res}

        @self.app.delete("/dashboard/api/wizard/partners/{partner_key}", tags=["Dashboard - Wizard"])
        async def delete_wizard_partner(partner_key: str, request: Request,
                                        username: str = Depends(self._get_auth_user_dependency)):
            """Delete a partner (admin only)."""
            if not self._get_is_admin(request):
                raise HTTPException(status_code=403, detail="Admin only")
            from shared.utils.wizard import partners as partners_svc
            return partners_svc.delete_partner(partner_key)

        @self.app.get("/dashboard/shop-orders", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_shop_orders(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard page listing orders placed through the e-commerce MCP, grouped by partner."""
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/wizard_orders.html", {
                "request": request,
                "page_title": "Shop Orders",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/api/shop/orders", tags=["Dashboard - Wizard"])
        async def get_shop_orders(request: Request, username: str = Depends(self._get_auth_user_dependency),
                                  status: Optional[str] = None, partner: Optional[str] = None,
                                  search: Optional[str] = None, limit: int = 200):
            """List shop orders: NEW first, then newest first. Optional status/partner filter + text search."""
            from shared.utils.models import ShopOrder
            from sqlalchemy import case, or_
            session = self.db_client.get_session()
            try:
                q = session.query(ShopOrder)
                if status:
                    q = q.filter(ShopOrder.status == status)
                if partner:
                    q = q.filter(ShopOrder.partner_key == partner)
                if search:
                    like = f"%{search.strip()}%"
                    q = q.filter(or_(
                        ShopOrder.customer_name.ilike(like),
                        ShopOrder.customer_email.ilike(like),
                        ShopOrder.order_id.ilike(like),
                    ))
                new_first = case((ShopOrder.status == 'new', 0), else_=1)
                orders = q.order_by(new_first, ShopOrder.created_at.desc()).limit(limit).all()
                return {"orders": [o.to_dict() for o in orders]}
            finally:
                session.close()

        @self.app.patch("/dashboard/api/shop/orders/{order_pk}", tags=["Dashboard - Wizard"])
        async def update_shop_order(order_pk: int, request: Request,
                                    username: str = Depends(self._get_auth_user_dependency)):
            """Update an order's status (new|contacted|fulfilled|cancelled)."""
            from shared.utils.models import ShopOrder
            body = await request.json()
            new_status = (body.get("status") or "").strip()
            if new_status not in {"new", "contacted", "fulfilled", "cancelled"}:
                raise HTTPException(status_code=400, detail="Invalid status")
            session = self.db_client.get_session()
            try:
                order = session.query(ShopOrder).filter(ShopOrder.id == order_pk).first()
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")
                order.status = new_status
                session.commit()
                return {"success": True}
            finally:
                session.close()

        @self.app.delete("/dashboard/api/shop/orders/{order_pk}", tags=["Dashboard - Wizard"])
        async def delete_shop_order(order_pk: int, request: Request,
                                    username: str = Depends(self._get_auth_user_dependency)):
            """Delete an order (admin only)."""
            if not self._get_is_admin(request):
                raise HTTPException(status_code=403, detail="Admin only")
            from shared.utils.models import ShopOrder
            session = self.db_client.get_session()
            try:
                order = session.query(ShopOrder).filter(ShopOrder.id == order_pk).first()
                if not order:
                    raise HTTPException(status_code=404, detail="Order not found")
                session.delete(order)
                session.commit()
                return {"success": True}
            finally:
                session.close()

        @self.app.post("/dashboard/api/wizard/leads/{lead_id}/convert", tags=["Dashboard - Wizard"])
        async def convert_wizard_lead(lead_id: int, request: Request,
                                      username: str = Depends(self._get_auth_user_dependency)):
            """Convert a lead's live trial into a permanent agent (rename project + keep widget)."""
            if not self._get_is_admin(request):
                raise HTTPException(status_code=403, detail="Admin only")
            from shared.utils.models import WizardLead
            from shared.utils.wizard.provisioning_service import WizardProvisioningService
            body = await request.json()
            session = self.db_client.get_session()
            try:
                lead = session.query(WizardLead).filter(WizardLead.id == lead_id).first()
                if not lead:
                    raise HTTPException(status_code=404, detail="Lead not found")
                if not lead.trial_project_id:
                    raise HTTPException(status_code=400, detail="This lead has no live trial to convert.")
                project_name = (body.get("project_name") or "").strip() or (lead.company or "").strip() or None

                svc = WizardProvisioningService(self)
                result = svc.promote_trial(lead.trial_project_id, project_name)
                if result.get("error"):
                    raise HTTPException(status_code=400, detail=result["error"])

                lead.status = "converted"
                session.commit()
                # The trial project is now permanent; refresh ADK so the agent stays loaded.
                try:
                    from shared.utils.utils import get_adk_config
                    import httpx
                    adk = get_adk_config()
                    with httpx.Client(timeout=30.0) as client:
                        client.post(f"http://{adk['adk_host']}:{adk['adk_port']}/api/reload-all-agents")
                except Exception:
                    pass
                return {"success": True, **result}
            except HTTPException:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(exc))
            finally:
                session.close()

        @self.app.get("/dashboard/api/wizard/leads/{lead_id}/snapshot", tags=["Dashboard - Wizard"])
        async def download_wizard_lead_snapshot(lead_id: int, request: Request,
                                                username: str = Depends(self._get_auth_user_dependency)):
            """Download the captured trial agent JSON snapshot for a lead."""
            from shared.utils.models import WizardLead
            from fastapi.responses import Response
            session = self.db_client.get_session()
            try:
                lead = session.query(WizardLead).filter(WizardLead.id == lead_id).first()
                if not lead or not lead.agent_snapshot:
                    raise HTTPException(status_code=404, detail="No snapshot for this lead")
                return Response(
                    content=lead.agent_snapshot,
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="wizard_lead_{lead_id}_agent.json"'},
                )
            finally:
                session.close()

        @self.app.delete("/dashboard/api/wizard/leads/{lead_id}", tags=["Dashboard - Wizard"])
        async def delete_wizard_lead(lead_id: int, request: Request,
                                     username: str = Depends(self._get_auth_user_dependency)):
            """Permanently delete an archived lead and any remaining trial assets (agent, project, memory blocks)."""
            if not self._get_is_admin(request):
                raise HTTPException(status_code=403, detail="Admin only")
            from shared.utils.models import WizardLead
            session = self.db_client.get_session()
            try:
                lead = session.query(WizardLead).filter(WizardLead.id == lead_id).first()
                if not lead:
                    raise HTTPException(status_code=404, detail="Lead not found")
                if lead.status != "archived":
                    raise HTTPException(status_code=400, detail="Only archived leads can be deleted")

                release_pid = lead.trial_project_id
                session.delete(lead)
                session.commit()
            except HTTPException:
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(exc))
            finally:
                session.close()

            if release_pid:
                from shared.utils.wizard.cleanup import release_trial
                release_trial(release_pid)

            return {"success": True}

        # API Endpoints for Dashboard
        @self.app.get("/dashboard/api/stats", tags=["Dashboard - Usage Analytics"])
        async def get_stats(request: Request, username: str = Depends(self._get_auth_user_dependency), days: int = 7):
            """Get usage statistics."""
            return self._get_usage_stats(days)

        @self.app.get("/dashboard/api/usage/logs", tags=["Dashboard - Usage Analytics"])
        async def get_usage_logs_api(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            hours: int = 24,
            limit: int = 100,
            page: int = 1
        ):
            """Get paginated token usage logs."""
            return self._get_token_usage_logs(hours, limit, page)

        @self.app.get("/dashboard/api/rate-limits", tags=["Dashboard - Rate Limits"])
        async def get_rate_limits_api(request: Request, username: str = Depends(self._get_auth_user_dependency), scope: Optional[str] = None):
            """Get rate limit configs, optionally filtered by scope (user, agent, project)."""
            from shared.utils.rate_limit_service import get_rate_limit_service
            svc = get_rate_limit_service()
            configs = svc.get_configs(scope=scope)
            return {"configs": configs}

        @self.app.get("/dashboard/api/rate-limits/usage", tags=["Dashboard - Rate Limits"])
        async def get_rate_limit_usage_api(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            user_id: Optional[str] = None,
            agent_name: Optional[str] = None,
            project_id: Optional[int] = None,
        ):
            """Get current usage vs limits for user/agent/project."""
            from shared.utils.rate_limit_service import get_rate_limit_service
            svc = get_rate_limit_service()
            usage = svc.get_usage_snapshot(
                user_id=user_id,
                agent_name=agent_name,
                project_id=project_id,
            )
            return {
                "requests_last_min": usage.requests_last_min,
                "tokens_last_hour": usage.tokens_last_hour,
                "tokens_last_day": usage.tokens_last_day,
                "tokens_last_month": usage.tokens_last_month,
            }

        @self.app.post("/dashboard/api/rate-limits", tags=["Dashboard - Rate Limits"])
        async def upsert_rate_limit_api(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Create or update rate limit config."""
            from shared.utils.rate_limit_service import get_rate_limit_service
            body = await request.json()
            scope = body.get("scope")
            scope_id = body.get("scope_id")
            if not scope or not scope_id:
                raise HTTPException(status_code=400, detail="scope and scope_id required")
            if scope not in ("user", "agent", "project"):
                raise HTTPException(status_code=400, detail="scope must be user, agent, or project")
            svc = get_rate_limit_service()
            result = svc.upsert_config(
                scope=scope,
                scope_id=str(scope_id),
                requests_per_minute=body.get("requests_per_minute"),
                tokens_per_hour=body.get("tokens_per_hour"),
                tokens_per_day=body.get("tokens_per_day"),
                tokens_per_month=body.get("tokens_per_month"),
                max_tokens_per_request=body.get("max_tokens_per_request"),
                action_on_limit=body.get("action_on_limit", "block"),
                alert_thresholds=body.get("alert_thresholds", [80, 90, 100]),
                alert_webhook_url=body.get("alert_webhook_url"),
            )
            if not result:
                raise HTTPException(status_code=500, detail="Failed to save config")
            return result

        @self.app.delete("/dashboard/api/rate-limits/{scope}/{scope_id:path}", tags=["Dashboard - Rate Limits"])
        async def delete_rate_limit_api(
            scope: str,
            scope_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Delete rate limit config."""
            from shared.utils.rate_limit_service import get_rate_limit_service
            if scope not in ("user", "agent", "project"):
                raise HTTPException(status_code=400, detail="scope must be user, agent, or project")
            svc = get_rate_limit_service()
            if not svc.delete_config(scope=scope, scope_id=scope_id):
                raise HTTPException(status_code=404, detail="Config not found")
            return {"deleted": True}

        @self.app.get("/dashboard/api/traces", tags=["Dashboard - Traces"])
        async def get_traces_api(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            hours: int = 24,
            limit: int = 50,
            trace_id: Optional[str] = None,
        ):
            """Get traces from trace_spans table for dashboard viewer."""
            return self._get_traces(hours, limit, trace_id)

        @self.app.get("/dashboard/api/users", tags=["Dashboard - Users"])
        async def get_users(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Get all users."""
            return {"users": self._get_all_users()}

        @self.app.get("/dashboard/api/projects", tags=["Dashboard - Projects"])
        async def get_projects_api(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Get all projects."""
            return {"projects": self._get_all_projects()}

        @self.app.post("/dashboard/api/projects", tags=["Dashboard - Projects"])
        async def create_project_api(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            name: str = Form(...),
            description: str = Form("")
        ):
            """Create a new project."""
            project = self._create_project(name, description)
            return {"success": True, "project": project}

        @self.app.put("/dashboard/api/projects/{project_id}", tags=["Dashboard - Projects"])
        async def update_project_api(
            project_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            name: str = Form(...),
            description: str = Form("")
        ):
            """Update an existing project."""
            project = self._update_project(project_id, name, description)
            return {"success": True, "project": project}

        @self.app.delete("/dashboard/api/projects/{project_id}", tags=["Dashboard - Projects"])
        async def delete_project_api(
            project_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Delete a project."""
            result = self._delete_project(project_id)
            return {"success": True, **result}

        @self.app.post("/dashboard/api/users", tags=["Dashboard - Users"])
        async def create_user(request: Request, username: str = Depends(self._get_auth_user_dependency), user_id: str = Form(...), roles: str = Form(...)):
            """Create a new user."""
            try:
                import json
                roles_list = json.loads(roles) if roles else ["user"]
                success = self._create_user(user_id, roles_list)
                if success:
                    audit_service.log(username, audit_service.ACTION_USER_CREATE, audit_service.RESOURCE_USER, resource_id=user_id, details={"roles": roles_list}, request=request)
                return {"success": success, "message": "User created successfully" if success else "Failed to create user"}
            except json.JSONDecodeError:
                return {"success": False, "message": "Invalid roles format"}

        @self.app.put("/dashboard/api/users/{user_id}", tags=["Dashboard - Users"])
        async def update_user(user_id: str, request: Request, username: str = Depends(self._get_auth_user_dependency), roles: str = Form(...), profile_data: str = Form(None)):
            """Update user roles and profile data."""
            try:
                import json
                roles_list = json.loads(roles) if roles else ["user"]
                profile_data_value = profile_data if profile_data else None
                success = self._update_user(user_id, roles_list, profile_data_value)
                if success:
                    audit_service.log(username, audit_service.ACTION_USER_UPDATE, audit_service.RESOURCE_USER, resource_id=user_id, details={"roles": roles_list}, request=request)
                return {"success": success, "message": "User updated successfully" if success else "Failed to update user"}
            except json.JSONDecodeError:
                return {"success": False, "message": "Invalid roles format"}

        @self.app.delete("/dashboard/api/users/{user_id}", tags=["Dashboard - Users"])
        async def delete_user(user_id: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Delete a user."""
            success = self._delete_user(user_id)
            if success:
                audit_service.log(username, audit_service.ACTION_USER_DELETE, audit_service.RESOURCE_USER, resource_id=user_id, request=request)
            return {"success": success, "message": "User deleted successfully" if success else "Failed to delete user"}

        @self.app.post("/dashboard/api/users/cleanup", tags=["Dashboard - Users"])
        async def trigger_user_cleanup(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Manually trigger temporary user cleanup."""
            if not self._get_is_admin(request):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Forbidden")
            from shared.utils.user_cleanup import cleanup_inactive_users
            try:
                ttl_param = request.query_params.get("ttl_days")
                ttl_days = int(ttl_param) if ttl_param else None
                result = cleanup_inactive_users(ttl_days=ttl_days)
                if "error" in result:
                    return {"success": False, "error": result["error"]}
                return {"success": True, "removed_users": result["removed_users"]}
            except Exception as e:
                return {"success": False, "error": str(e)}

        # ---------- Personal Access Tokens API ----------
        @self.app.get("/dashboard/api/tokens", tags=["Dashboard - Users"])
        async def list_user_tokens(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Get all personal access tokens for the logged-in user."""
            from shared.utils.models import PersonalAccessToken
            db = self.db_client
            session = db.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database unavailable")
            try:
                tokens = session.query(PersonalAccessToken).filter_by(user_id=username).all()
                return {"success": True, "tokens": [t.to_dict() for t in tokens]}
            finally:
                session.close()

        @self.app.post("/dashboard/api/tokens", tags=["Dashboard - Users"])
        async def create_user_token(
            request: Request,
            body: Dict[str, Any],
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Create a new personal access token."""
            from shared.utils.models import PersonalAccessToken
            import secrets
            import hashlib
            from datetime import datetime, timezone
            
            name = body.get("name", "Unnamed Token")
            expires_in_days = body.get("expires_in_days")
            
            raw_token = f"mate_pat_{secrets.token_urlsafe(32)}"
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            token_prefix = raw_token[:13]
            
            expires_at = None
            if expires_in_days:
                from datetime import timedelta
                expires_at = datetime.now(timezone.utc) + timedelta(days=float(expires_in_days))
                
            db = self.db_client
            session = db.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database unavailable")
            try:
                pat = PersonalAccessToken(
                    token_hash=token_hash,
                    token_prefix=token_prefix,
                    name=name,
                    user_id=username,
                    expires_at=expires_at
                )
                session.add(pat)
                session.commit()
                
                audit_service.log(username, "pat_create", "personal_access_token", resource_id=str(pat.id), details={"name": name}, request=request)
                
                return {
                    "success": True,
                    "token": raw_token,
                    "pat": pat.to_dict()
                }
            except Exception as e:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.delete("/dashboard/api/tokens/{token_id}", tags=["Dashboard - Users"])
        async def revoke_user_token(
            token_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Revoke a personal access token."""
            from shared.utils.models import PersonalAccessToken
            db = self.db_client
            session = db.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database unavailable")
            try:
                pat = session.query(PersonalAccessToken).filter_by(id=token_id, user_id=username).first()
                if not pat:
                    raise HTTPException(status_code=404, detail="Token not found")
                
                session.delete(pat)
                session.commit()
                
                audit_service.log(username, "pat_revoke", "personal_access_token", resource_id=str(token_id), request=request)
                return {"success": True, "message": "Token revoked successfully"}
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.put("/dashboard/api/agents/{config_id}/expose", tags=["Dashboard - Agents"])
        async def expose_agent(
            config_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Toggle or set agent's expose_as_model flag."""
            body = await request.json()
            expose = body.get("expose", False)
            db = self.db_client
            session = db.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database unavailable")
            try:
                config = session.query(self.AgentConfig).filter(self.AgentConfig.id == config_id).first()
                if not config:
                    raise HTTPException(status_code=404, detail="Agent not found")
                
                # Check if it has parent agents (should only expose root agents)
                parents = config.get_parent_agents() if hasattr(config, 'get_parent_agents') else []
                if expose and parents:
                    raise HTTPException(
                        status_code=400,
                        detail="Only root agents (agents without parent agents) can be exposed as models."
                    )
                
                config.expose_as_model = expose
                session.commit()
                
                self._snapshot_agent_config(session, config, change_type='expose_toggle', changed_by=username)
                audit_service.log(username, "agent_expose_toggle", audit_service.RESOURCE_AGENT, resource_id=config.name, details={"expose": expose}, request=request)
                return {"success": True, "expose_as_model": config.expose_as_model}
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.get("/dashboard/api/agents", tags=["Dashboard - Agents"])
        async def get_agents(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            project_id: Optional[int] = None
        ):
            """Get all agent configurations."""
            return {"configs": self._get_all_agent_configs(project_id)}

        @self.app.get("/dashboard/api/agents/export", tags=["Dashboard - Agents"])
        async def export_agents(
            request: Request, 
            username: str = Depends(self._get_auth_user_dependency),
            search: str = None,
            root_agent: str = None,
            project_id: Optional[int] = None
        ):
            """Export agent configurations as JSON with optional filtering."""
            export_data = self._export_agent_configs(search=search, root_agent=root_agent, project_id=project_id)
            
            if "error" in export_data:
                raise HTTPException(status_code=500, detail=export_data["error"])
            
            return export_data

        # ---- Async Binary Build System ----
        # Stores build state in memory: { build_id: { status, progress, error, build_dir, binary_path, filename } }
        _active_builds: Dict[str, Dict[str, Any]] = {}

        def _run_build_worker(build_id: str, export_data: dict, root_agent: str, app_name: str):
            """Background worker that runs the build process."""
            import tempfile
            import subprocess
            import platform

            build_entry = _active_builds[build_id]
            build_dir = build_entry["build_dir"]
            json_path = os.path.join(build_dir, "agent_export.json")
            output_dir = os.path.join(build_dir, "output")

            try:
                build_entry["progress"] = "Writing agent export..."

                import json as json_mod
                with open(json_path, "w", encoding="utf-8") as f:
                    json_mod.dump(export_data, f)

                project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                build_script = os.path.join(project_root, "build_standalone_agent.py")

                if not os.path.exists(build_script):
                    build_entry["status"] = "failed"
                    build_entry["error"] = "Build script not found"
                    return

                cmd = [
                    sys.executable, build_script, json_path,
                    "--output-dir", output_dir,
                    "--agent-name", root_agent,
                    "--app-name", app_name,
                    "--build",
                ]

                build_entry["progress"] = "Running PyInstaller build (this may take several minutes)..."
                logger.info(f"[Build {build_id}] Starting binary build for '{root_agent}'")

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    cwd=project_root,
                )

                if result.returncode != 0:
                    error_msg = result.stderr[-2000:] if result.stderr else result.stdout[-2000:]
                    logger.error(f"[Build {build_id}] Build failed: {error_msg}")
                    build_entry["status"] = "failed"
                    build_entry["error"] = error_msg
                    return

                # Find the built binary
                build_entry["progress"] = "Locating and zipping built bundle..."
                dist_dir = os.path.join(output_dir, "dist")
                if not os.path.isdir(dist_dir):
                    build_entry["status"] = "failed"
                    build_entry["error"] = "Build completed but dist/ directory not found"
                    return

                binary_path = None
                system = platform.system().lower()

                import shutil
                import zipfile

                if system == "darwin":
                    # macOS: One-File build ensures Gatekeeper only prompts once
                    app_file_mac = os.path.join(dist_dir, app_name)
                    if os.path.isfile(app_file_mac):
                        zip_path = os.path.join(build_dir, f"{app_name}_mac.zip")
                        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                            # Preserve executable permissions
                            info = zipfile.ZipInfo(app_name)
                            info.external_attr = 0o755 << 16  # -rwxr-xr-x
                            with open(app_file_mac, 'rb') as f:
                                zipf.writestr(info, f.read())
                        binary_path = zip_path
                else:
                    # Windows/Linux: Fast One-Dir builds (dist/AppName folder)
                    app_folder = os.path.join(dist_dir, app_name)
                    if os.path.isdir(app_folder):
                        zip_suffix = "win" if system == "windows" else "linux"
                        zip_path = os.path.join(build_dir, f"{app_name}_{zip_suffix}.zip")
                        shutil.make_archive(
                            os.path.join(build_dir, f"{app_name}_{zip_suffix}"), "zip",
                            dist_dir, app_name
                        )
                        binary_path = zip_path

                if not binary_path or not os.path.exists(binary_path):
                    dist_files = os.listdir(dist_dir)
                    if dist_files:
                        candidate = os.path.join(dist_dir, dist_files[0])
                        # If the only output is a directory, zip it
                        if os.path.isdir(candidate):
                            zip_path = os.path.join(build_dir, f"{app_name}.zip")
                            shutil.make_archive(os.path.join(build_dir, app_name), "zip", dist_dir, dist_files[0])
                            binary_path = zip_path
                        elif os.path.isfile(candidate):
                            binary_path = candidate

                if not binary_path or not os.path.exists(binary_path):
                    build_entry["status"] = "failed"
                    build_entry["error"] = "Build completed but binary not found in dist/"
                    return

                filename = os.path.basename(binary_path)
                file_size = os.path.getsize(binary_path)
                logger.info(f"[Build {build_id}] Success: {filename} ({file_size} bytes)")

                build_entry["status"] = "completed"
                build_entry["binary_path"] = binary_path
                build_entry["filename"] = filename
                build_entry["file_size"] = file_size
                build_entry["progress"] = "Build completed successfully!"

            except subprocess.TimeoutExpired:
                build_entry["status"] = "failed"
                build_entry["error"] = "Build timed out (10-minute limit)"
            except Exception as e:
                logger.error(f"[Build {build_id}] Error: {e}")
                build_entry["status"] = "failed"
                build_entry["error"] = str(e)

        @self.app.post("/dashboard/api/agents/build-binary", tags=["Dashboard - Agents"])
        async def start_build_binary(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Start a standalone binary build in the background. Returns a build_id for polling."""
            import tempfile
            import threading
            import uuid

            body = await request.json()
            root_agent = body.get("root_agent")
            project_id = body.get("project_id")
            app_name = body.get("app_name", "MATEAgent")

            if not project_id:
                raise HTTPException(status_code=400, detail="project_id is required")

            export_data = self._export_agent_configs(root_agent=root_agent, project_id=project_id)
            if "error" in export_data:
                raise HTTPException(status_code=500, detail=export_data["error"])
            if not export_data.get("agents"):
                raise HTTPException(status_code=400, detail="No agents found to build")

            if not root_agent:
                roots = [a for a in export_data["agents"]
                         if not a.get("parent_agents") or a["parent_agents"] == []]
                root_agent = roots[0]["name"] if roots else export_data["agents"][0]["name"]

            build_id = str(uuid.uuid4())[:8]
            build_dir = tempfile.mkdtemp(prefix=f"mate_build_{build_id}_")

            _active_builds[build_id] = {
                "status": "building",
                "progress": "Starting build...",
                "error": None,
                "build_dir": build_dir,
                "binary_path": None,
                "filename": None,
                "file_size": None,
                "root_agent": root_agent,
                "app_name": app_name,
            }

            thread = threading.Thread(
                target=_run_build_worker,
                args=(build_id, export_data, root_agent, app_name),
                daemon=True,
            )
            thread.start()

            logger.info(f"[Build {build_id}] Started background build for '{root_agent}'")
            return {"build_id": build_id, "status": "building", "message": f"Build started for agent '{root_agent}'"}

        @self.app.get("/dashboard/api/agents/build-binary/{build_id}/status", tags=["Dashboard - Agents"])
        async def get_build_status(
            build_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Poll the status of a background binary build."""
            entry = _active_builds.get(build_id)
            if not entry:
                raise HTTPException(status_code=404, detail="Build not found")

            return {
                "build_id": build_id,
                "status": entry["status"],
                "progress": entry["progress"],
                "error": entry["error"],
                "filename": entry["filename"],
                "file_size": entry["file_size"],
            }

        @self.app.get("/dashboard/api/agents/build-binary/{build_id}/download", tags=["Dashboard - Agents"])
        async def download_build(
            build_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Download the completed binary."""
            from fastapi.responses import FileResponse

            entry = _active_builds.get(build_id)
            if not entry:
                raise HTTPException(status_code=404, detail="Build not found")
            if entry["status"] != "completed":
                raise HTTPException(status_code=400, detail=f"Build is not complete (status: {entry['status']})")
            if not entry["binary_path"] or not os.path.exists(entry["binary_path"]):
                raise HTTPException(status_code=500, detail="Binary file not found")

            filename = entry["filename"]
            media_type = "application/zip" if filename.endswith(".zip") else "application/octet-stream"

            return FileResponse(
                path=entry["binary_path"],
                filename=filename,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        @self.app.post("/dashboard/api/agents/import", tags=["Dashboard - Agents"])
        async def import_agents(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            overwrite: bool = False
        ):
            """Import agent configurations from JSON."""
            try:
                import_data = await request.json()
                result = self._import_agent_configs(import_data, overwrite)
                
                if "error" in result:
                    raise HTTPException(status_code=400, detail=result["error"])
                
                return result
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid JSON data: {str(e)}")

        @self.app.get("/dashboard/api/templates", tags=["Dashboard - Templates"])
        async def get_templates(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            category: Optional[str] = None,
            search: Optional[str] = None,
        ):
            """List available agent templates."""
            templates = self.template_service.list_templates(category=category, search=search)
            return {"templates": templates}

        @self.app.get("/dashboard/api/templates/{template_id}", tags=["Dashboard - Templates"])
        async def get_template(
            template_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Get single template by id."""
            template = self.template_service.get_template(template_id)
            if not template:
                raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
            return template

        @self.app.delete("/dashboard/api/templates/{template_id}", tags=["Dashboard - Templates"])
        async def delete_template(
            template_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Delete a template."""
            success = self.template_service.delete_template(template_id)
            if not success:
                raise HTTPException(status_code=404, detail=f"Template not found or could not be deleted: {template_id}")
            
            # Audit the deletion
            if hasattr(self, 'AuditLog'):
                try:
                    audit_service.log_event(
                        action="delete_template",
                        actor=username,
                        target_type="template",
                        target_id=template_id,
                        description=f"Deleted template: {template_id}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to audit template deletion: {e}")

            return {"success": True}

        @self.app.post("/dashboard/api/templates/import", tags=["Dashboard - Templates"])
        async def import_template(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """One-click import: create project, agents, memory blocks from template."""
            try:
                body = await request.json()
                template_id = body.get("template_id")
                project_name = body.get("project_name")
                if not template_id:
                    raise HTTPException(status_code=400, detail="template_id is required")
                result = self._import_template(template_id, project_name=project_name, changed_by=username)
                if "error" in result:
                    raise HTTPException(status_code=400, detail=result["error"])
                return result
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.post("/dashboard/api/templates/create-from-agents", tags=["Dashboard - Templates"])
        async def create_template_from_agents(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Create a template from existing agents (project + root agent hierarchy)."""
            try:
                body = await request.json()
                project_id = body.get("project_id")
                root_agent = body.get("root_agent")
                template_id = body.get("template_id")
                template_name = body.get("template_name") or template_id
                description = body.get("description", "")
                category = body.get("category", "custom")
                if not project_id or not root_agent or not template_id:
                    raise HTTPException(
                        status_code=400,
                        detail="project_id, root_agent, and template_id are required",
                    )
                result = self._create_template_from_agents(
                    project_id=int(project_id),
                    root_agent=str(root_agent).strip(),
                    template_id=str(template_id).strip(),
                    template_name=str(template_name).strip(),
                    description=str(description).strip(),
                    category=str(category).strip() or "custom",
                )
                if "error" in result:
                    raise HTTPException(status_code=400, detail=result["error"])
                return result
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.get("/dashboard/api/templates/sync-status/{project_id}", tags=["Dashboard - Templates"])
        async def get_template_sync_status(
            project_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Get sync status: what changes would be applied if syncing with the source template."""
            result = self._get_template_sync_status(project_id)
            if "error" in result:
                raise HTTPException(status_code=400, detail=result["error"])
            return result

        @self.app.post("/dashboard/api/templates/sync", tags=["Dashboard - Templates"])
        async def sync_template(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Sync a project with its source template: add new agents, update changed agents."""
            try:
                body = await request.json()
                project_id = body.get("project_id")
                if not project_id:
                    raise HTTPException(status_code=400, detail="project_id is required")
                result = self._sync_template(int(project_id), changed_by=username)
                if "error" in result:
                    raise HTTPException(status_code=400, detail=result["error"])
                return result
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        @self.app.get("/dashboard/api/migrations", tags=["Dashboard - Migrations"])
        async def get_migrations(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Get all schema migrations."""
            return {"migrations": self._get_schema_migrations()}

        @self.app.delete("/dashboard/api/migrations/{version}", tags=["Dashboard - Migrations"])
        async def delete_migration(version: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Delete a migration from the schema_migrations table."""
            if not self.db_client:
                raise HTTPException(status_code=500, detail="Database connection failed")
            
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database connection failed")
            
            try:
                from sqlalchemy import text
                result = session.execute(text("DELETE FROM schema_migrations WHERE version = :version"), {"version": version})
                session.commit()
                
                if result.rowcount == 0:
                    raise HTTPException(status_code=404, detail=f"Migration {version} not found")
                
                return {"message": f"Migration {version} deleted successfully"}
            except Exception as e:
                session.rollback()
                raise HTTPException(status_code=500, detail=f"Error deleting migration: {str(e)}")
            finally:
                session.close()

        @self.app.post("/dashboard/api/migrations/{version}/rerun", tags=["Dashboard - Migrations"])
        async def rerun_migration(version: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Re-run a specific migration by deleting it first, then running migrations."""
            if not self.db_client:
                raise HTTPException(status_code=500, detail="Database connection failed")
            
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database connection failed")
            
            try:
                from sqlalchemy import text
                from shared.utils.migration_system import MigrationSystem
                
                # First, delete the migration record
                result = session.execute(text("DELETE FROM schema_migrations WHERE version = :version"), {"version": version})
                session.commit()
                
                if result.rowcount == 0:
                    raise HTTPException(status_code=404, detail=f"Migration {version} not found")
                
                # Then run migrations to re-apply it
                migration_system = MigrationSystem(self.db_client)
                success = migration_system.run_migrations()
                
                if success:
                    return {"message": f"Migration {version} re-run successfully"}
                else:
                    return {"message": f"Migration {version} deleted but re-run failed. Check logs for details."}
                    
            except Exception as e:
                session.rollback()
                raise HTTPException(status_code=500, detail=f"Error re-running migration: {str(e)}")
            finally:
                session.close()

        @self.app.post("/dashboard/api/migrations/run", tags=["Dashboard - Migrations"])
        async def run_all_migrations(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Run all pending migrations."""
            try:
                from shared.utils.migration_system import MigrationSystem
                
                migration_system = MigrationSystem(self.db_client)
                success = migration_system.run_migrations()
                
                if success:
                    return {"message": "All migrations completed successfully"}
                else:
                    return {"message": "Some migrations failed. Check logs for details."}
                    
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error running migrations: {str(e)}")

        @self.app.post("/dashboard/api/agents", tags=["Dashboard - Agents"])
        async def create_agent(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            name: str = Form(...),
            type: str = Form(...),
            project_id: str = Form(...),
            model_name: str = Form(None),
            description: str = Form(None),
            instruction: str = Form(None),
            parent_agents: str = Form(None),
            allowed_for_roles: str = Form(None),
            tool_config: str = Form(""),
            mcp_servers_config: str = Form(""),
            planner_config: str = Form(""),
            generate_content_config: str = Form(""),
            input_schema: str = Form(""),
            output_schema: str = Form(""),
            include_contents: str = Form(""),
            guardrail_config: str = Form(""),
            max_iterations: str = Form(""),
            disabled: bool = Form(False),
            hardcoded: bool = Form(False),
            expose_as_model: bool = Form(False),
            debug_mode: bool = Form(False)
        ):
            """Create a new agent configuration."""
            if not description or not description.strip():
                raise HTTPException(status_code=400, detail="Description is required")
            if not instruction or not instruction.strip():
                raise HTTPException(status_code=400, detail="Instruction is required")
            # Parse parent_agents JSON string to list
            import json
            try:
                parent_agents_list = json.loads(parent_agents) if parent_agents else []
            except json.JSONDecodeError:
                parent_agents_list = []
            
            config_data = {
                "name": name,
                "type": type,
                "project_id": int(project_id) if project_id else None,
                "model_name": model_name,
                "description": description,
                "instruction": instruction,
                "parent_agents": parent_agents_list,
                "allowed_for_roles": allowed_for_roles,
                "tool_config": tool_config,
                "mcp_servers_config": mcp_servers_config,
                "planner_config": planner_config,
                "generate_content_config": generate_content_config,
                "input_schema": input_schema,
                "output_schema": output_schema,
                "include_contents": include_contents,
                "guardrail_config": guardrail_config,
                "max_iterations": int(max_iterations) if max_iterations else None,
                "disabled": disabled,
                "hardcoded": hardcoded,
                "expose_as_model": expose_as_model,
                "debug_mode": debug_mode
            }
            success = self._create_agent_config(config_data, changed_by=username)
            if success:
                audit_service.log(username, audit_service.ACTION_AGENT_CREATE, audit_service.RESOURCE_AGENT, resource_id=name, details={"project_id": config_data.get("project_id")}, request=request)
            return {"success": success, "message": "Agent created successfully" if success else "Failed to create agent"}

        @self.app.put("/dashboard/api/agents/{config_id}", tags=["Dashboard - Agents"])
        async def update_agent(
            config_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            name: str = Form(...),
            type: str = Form(...),
            project_id: str = Form(...),
            model_name: str = Form(None),
            description: str = Form(None),
            instruction: str = Form(None),
            parent_agents: str = Form(None),
            allowed_for_roles: str = Form(None),
            tool_config: str = Form(""),
            mcp_servers_config: str = Form(""),
            planner_config: str = Form(""),
            generate_content_config: str = Form(""),
            input_schema: str = Form(""),
            output_schema: str = Form(""),
            include_contents: str = Form(""),
            guardrail_config: str = Form(""),
            max_iterations: str = Form(""),
            disabled: bool = Form(False),
            hardcoded: bool = Form(False),
            expose_as_model: bool = Form(False),
            debug_mode: bool = Form(False)
        ):
            """Update an agent configuration."""
            if not description or not description.strip():
                raise HTTPException(status_code=400, detail="Description is required")
            if not instruction or not instruction.strip():
                raise HTTPException(status_code=400, detail="Instruction is required")
            # Parse parent_agents JSON string to list
            import json
            try:
                parent_agents_list = json.loads(parent_agents) if parent_agents else []
            except json.JSONDecodeError:
                parent_agents_list = []
            
            config_data = {
                "name": name,
                "type": type,
                "project_id": int(project_id) if project_id else None,
                "model_name": model_name,
                "description": description,
                "instruction": instruction,
                "parent_agents": parent_agents_list,
                "allowed_for_roles": allowed_for_roles,
                "tool_config": tool_config,
                "mcp_servers_config": mcp_servers_config,
                "planner_config": planner_config,
                "generate_content_config": generate_content_config,
                "input_schema": input_schema,
                "output_schema": output_schema,
                "include_contents": include_contents,
                "guardrail_config": guardrail_config,
                "max_iterations": int(max_iterations) if max_iterations else None,
                "disabled": disabled,
                "hardcoded": hardcoded,
                "expose_as_model": expose_as_model,
                "debug_mode": debug_mode
            }
            success = self._update_agent_config(config_id, config_data, changed_by=username)
            if success:
                audit_service.log(username, audit_service.ACTION_AGENT_UPDATE, audit_service.RESOURCE_AGENT, resource_id=name, details={"config_id": config_id}, request=request)
            return {"success": success, "message": "Agent updated successfully" if success else "Failed to update agent"}

        @self.app.delete("/dashboard/api/agents/{config_id}", tags=["Dashboard - Agents"])
        async def delete_agent(
            config_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            delete_folder: bool = False
        ):
            """Delete an agent configuration and optionally its folder if not hardcoded."""
            # Fetch config to know name and hardcoded flag
            agent_name = None
            agent_hardcoded = False
            try:
                if self.db_client:
                    session = self.db_client.get_session()
                    if session:
                        try:
                            config = session.query(self.AgentConfig).filter(self.AgentConfig.id == config_id).first()
                            if config:
                                agent_name = config.name
                                agent_hardcoded = bool(getattr(config, 'hardcoded', False))
                        finally:
                            session.close()
            except Exception as e:
                print(f"Error pre-reading agent before delete: {e}")

            success = self._delete_agent_config(config_id)

            # Optionally delete folder if requested and agent is not hardcoded
            folder_deleted = False
            folder_path = None
            if success and delete_folder and agent_name and not agent_hardcoded:
                try:
                    agents_dir = self.project_root / "agents"
                    dest_path = agents_dir / agent_name
                    folder_path = str(dest_path)
                    if dest_path.exists() and dest_path.is_dir():
                        shutil.rmtree(dest_path)
                        folder_deleted = True
                except Exception as e:
                    print(f"Error deleting agent folder '{agent_name}': {e}")

            if success:
                audit_service.log(username, audit_service.ACTION_AGENT_DELETE, audit_service.RESOURCE_AGENT, resource_id=agent_name or str(config_id), details={"config_id": config_id, "folder_deleted": folder_deleted}, request=request)

            return {
                "success": success,
                "message": "Agent deleted successfully" if success else "Failed to delete agent",
                "folder_deleted": folder_deleted,
                "folder_path": folder_path,
                "hardcoded": agent_hardcoded
            }

        @self.app.post("/dashboard/api/agents/{agent_name}/create-folder", tags=["Dashboard - Agents"])
        async def create_agent_folder(agent_name: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Create agent folder from template for a specific agent."""
            result = self._copy_template_agent(agent_name)
            
            if not result["success"] and not result["skipped"]:
                raise HTTPException(status_code=500, detail=result["message"])
            
            return result

        @self.app.delete("/dashboard/api/agents/{agent_name}/delete-folder", tags=["Dashboard - Agents"])
        async def delete_agent_folder(agent_name: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Delete agent folder for a specific agent."""
            result = self._delete_agent_folder(agent_name)
            
            if not result["success"] and result.get("error"):
                raise HTTPException(status_code=500, detail=result["message"])
            
            return result

        @self.app.post("/dashboard/api/agents/{agent_name}/reinitialize", tags=["Dashboard - Agents"])
        async def reinitialize_agent(agent_name: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Reinitialize a specific agent with fresh configuration from database by proxying to ADK server."""
            import httpx
            from shared.utils.utils import get_adk_config
            
            print(f"🔄 [Dashboard] Reload request for agent '{agent_name}', proxying to ADK server")
            
            try:
                adk_config = get_adk_config()
                adk_url = f"http://{adk_config['adk_host']}:{adk_config['adk_port']}/api/reload-agent/{agent_name}"
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(adk_url)
                    result = response.json()
                    
                    print(f"🔄 [Dashboard] ADK server response: {result}")
                    return result
                    
            except Exception as e:
                print(f"❌ [Dashboard] Error calling ADK reload endpoint: {e}")
                import traceback
                traceback.print_exc()
                return {
                    "success": False,
                    "message": f"Error communicating with ADK server: {str(e)}",
                    "agent_name": agent_name
                }
        
        # ── Agent Config Versioning Endpoints ────────────────────────────────

        @self.app.get("/dashboard/api/agents/{config_id}/versions", tags=["Dashboard - Agent Versions"])
        async def get_agent_versions(config_id: int, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Return all version snapshots for an agent config."""
            versions = self._get_agent_versions(config_id)
            return {"success": True, "versions": versions}

        @self.app.post("/dashboard/api/agents/{config_id}/rollback/{version_id}", tags=["Dashboard - Agent Versions"])
        async def rollback_agent(config_id: int, version_id: int, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Rollback an agent config to a previous version snapshot."""
            result = self._rollback_agent_config(config_id, version_id, changed_by=username)
            if result:
                return {"success": True, "message": "Agent rolled back successfully", "config": result}
            return {"success": False, "message": "Failed to rollback agent"}

        @self.app.put("/dashboard/api/agents/versions/{version_id}/tag", tags=["Dashboard - Agent Versions"])
        async def tag_agent_version(version_id: int, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Set or clear a tag on a specific version."""
            body = await request.json()
            tag = body.get("tag", "")
            success = self._tag_agent_version(version_id, tag)
            return {
                "success": success,
                "message": "Version tagged successfully" if success else "Failed to tag version",
            }

        # File Search API Endpoints
        @self.app.get("/dashboard/api/agents/{agent_name}/file-search/config", tags=["Dashboard - File Search"])
        async def get_agent_file_search_config(
            agent_name: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Get File Search configuration for an agent (diagnostic endpoint)."""
            if not self.db_client:
                return {"success": False, "error": "Database not available"}
            
            session = self.db_client.get_session()
            if not session:
                return {"success": False, "error": "Database session not available"}
            
            try:
                agent = session.query(self.AgentConfig).filter_by(name=agent_name).first()
                if not agent:
                    return {"success": False, "error": f"Agent {agent_name} not found"}
                
                # Get stores from database
                from shared.utils.file_search_service import FileSearchService
                service = FileSearchService(self.db_client)
                stores = service.get_stores_for_agent(agent_name)
                store_names = [s['store_name'] for s in stores]
                
                # Parse tool_config
                tool_config = agent.tool_config
                tool_config_dict = {}
                if tool_config:
                    try:
                        tool_config_dict = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
                    except json.JSONDecodeError:
                        pass
                
                file_search_config = tool_config_dict.get('file_search', {})
                
                return {
                    "success": True,
                    "agent_name": agent_name,
                    "stores_in_db": stores,
                    "store_names": store_names,
                    "tool_config_raw": tool_config,
                    "tool_config_parsed": tool_config_dict,
                    "file_search_config": file_search_config,
                    "file_search_enabled": file_search_config.get('enabled', False),
                    "file_search_store_names": file_search_config.get('store_names', []),
                    "note": "tool_config is auto-populated from database during agent initialization"
                }
            finally:
                session.close()
        
        @self.app.get("/dashboard/api/agents/{agent_name}/file-search/stores", tags=["Dashboard - File Search"])
        async def get_agent_file_search_stores(
            agent_name: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Get all file search stores assigned to an agent."""
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            stores = service.get_stores_for_agent(agent_name)
            return {"success": True, "stores": stores}
        
        @self.app.get("/dashboard/api/agents/{agent_name}/file-search/files", tags=["Dashboard - File Search"])
        async def get_agent_file_search_files(
            agent_name: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Get all files accessible to an agent from all its stores."""
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            files = service.list_files_for_agent(agent_name)
            return {"success": True, "files": files}
        
        @self.app.post("/dashboard/api/agents/{agent_name}/file-search/stores/assign", tags=["Dashboard - File Search"])
        async def assign_file_search_store(
            agent_name: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            store_name: str = Form(...),
            is_primary: bool = Form(False)
        ):
            """Assign a file search store to an agent."""
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            success = service.assign_store_to_agent(
                agent_name=agent_name,
                store_name=store_name,
                is_primary=is_primary
            )
            message = "Store assigned successfully" if success else "Failed to assign store"
            if success:
                message += ". Note: Agent will need to be reinitialized to use File Search."
            return {"success": success, "message": message, "needs_reload": success}
        
        @self.app.post("/dashboard/api/agents/{agent_name}/file-search/stores/unassign", tags=["Dashboard - File Search"])
        async def unassign_file_search_store(
            agent_name: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            store_name: str = Form(...)
        ):
            """Unassign a file search store from an agent."""
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            success = service.unassign_store_from_agent(
                agent_name=agent_name,
                store_name=store_name
            )
            message = "Store unassigned successfully" if success else "Failed to unassign store"
            if success:
                message += ". Note: Agent will need to be reinitialized to apply changes."
            return {"success": success, "message": message, "needs_reload": success}
        
        @self.app.post("/dashboard/api/file-search/stores/create", tags=["Dashboard - File Search"])
        async def create_file_search_store(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Create a new file search store and optionally assign it to an agent."""
            from shared.utils.file_search_service import FileSearchService
            from shared.utils.tools.file_search_tools import create_file_search_store
            service = FileSearchService(self.db_client)
            
            # Parse JSON body
            try:
                data = await request.json()
                display_name = data.get("display_name")
                project_id = data.get("project_id")
                description = data.get("description")
                agent_name = data.get("agent_name")
                is_primary = data.get("is_primary", False)
                
                if not display_name:
                    return {"success": False, "error": "display_name is required"}
                if not project_id:
                    return {"success": False, "error": "project_id is required"}
            except Exception as e:
                return {"success": False, "error": f"Invalid JSON: {str(e)}"}
            
            # First create the store in Gemini API
            gemini_result = create_file_search_store(display_name=display_name)
            if not gemini_result.get("success"):
                return {"success": False, "error": gemini_result.get("error", "Failed to create store in Gemini API")}
            
            actual_store_name = gemini_result.get("store_name")
            
            # Create store record in database
            if agent_name:
                # Create and assign in one operation
                result = service.create_store_and_assign(
                    store_name=actual_store_name,
                    display_name=display_name,
                    agent_name=agent_name,
                    project_id=project_id,
                    description=description,
                    is_primary=is_primary
                )
            else:
                # Just create the store
                result = service.create_store(
                    store_name=actual_store_name,
                    display_name=display_name,
                    project_id=project_id,
                    description=description
                )
            
            return result
        
        @self.app.post("/dashboard/api/file-search/stores/upload", tags=["Dashboard - File Search"])
        async def upload_file_to_store(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            file: UploadFile = File(...),
            store_name: str = Form(...),
            display_name: str = Form(None),
            agent_name: str = Form(None)
        ):
            """Upload a file to a file search store."""
            import tempfile
            from shared.utils.tools.file_search_tools import upload_file_to_store
            from shared.utils.file_search_service import FileSearchService
            
            # Save uploaded file to temp location
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp_file:
                content = await file.read()
                tmp_file.write(content)
                tmp_path = tmp_file.name
            
            try:
                # Upload to Gemini File Search
                result = upload_file_to_store(
                    file_path=tmp_path,
                    store_name=store_name,
                    display_name=display_name or file.filename
                )
                
                if result.get("success"):
                    # Record document in database (always save, even if agent_name is not provided)
                    service = FileSearchService(self.db_client)
                    doc_name = result.get("document_name")
                    # Ensure we have a document_name - use fallback if needed
                    if not doc_name:
                        import hashlib
                        file_hash = hashlib.md5(tmp_path.encode()).hexdigest()[:16]
                        doc_name = f"fileSearchDocuments/{file_hash}"
                    
                    try:
                        doc_result = service.add_document(
                            store_name=store_name,
                            document_name=doc_name,
                            display_name=display_name or file.filename,
                            file_path=tmp_path,
                            file_size=len(content),
                            mime_type=file.content_type,
                            status="completed",
                            uploaded_by_agent=agent_name
                        )
                        
                        # If database save failed, log it but don't fail the upload
                        if not doc_result.get("success"):
                            logger.warning(f"Failed to save document to database: {doc_result.get('error')}")
                            # Still return success since Gemini upload worked, but include warning
                            result["warning"] = "File uploaded but database save failed"
                            result["db_error"] = doc_result.get("error")
                        else:
                            logger.info(f"Document saved to database: {doc_name}")
                    except Exception as e:
                        logger.error(f"Exception saving document to database: {e}")
                        result["warning"] = f"File uploaded but database save failed: {str(e)}"
                
                return result
            finally:
                # Clean up temp file
                try:
                    os.unlink(tmp_path)
                except:
                    pass
        
        @self.app.get("/dashboard/api/file-search/stores/{store_name}/files", tags=["Dashboard - File Search"])
        async def list_store_files(
            store_name: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """List all files in a file search store."""
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            files = service.list_files_in_store(store_name)
            return {"success": True, "files": files}
        
        # Memory Blocks API Endpoints
        @self.app.get("/dashboard/api/agents/{agent_name}/memory-blocks", tags=["Dashboard - Memory Blocks"])
        async def list_agent_memory_blocks(
            agent_name: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            label_search: Optional[str] = None,
            value_search: Optional[str] = None
        ):
            """List memory blocks for an agent."""
            if not self.db_client:
                return {"success": False, "error": "Database not available"}
            
            session = self.db_client.get_session()
            if not session:
                return {"success": False, "error": "Database session not available"}
            
            try:
                agent = session.query(self.AgentConfig).filter_by(name=agent_name).first()
                if not agent:
                    return {"success": False, "error": f"Agent {agent_name} not found"}
                
                # Parse tool_config to check for memory tools
                tool_config = agent.tool_config
                tool_config_dict = {}
                if tool_config:
                    try:
                        tool_config_dict = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
                    except json.JSONDecodeError:
                        pass
                
                if not self._has_memory_blocks(tool_config_dict):
                    return {"success": False, "error": "Agent does not have memory blocks tool configured"}
                
                from shared.utils.memory_blocks_service import MemoryBlocksService
                svc = MemoryBlocksService(self.db_client)
                result = svc.list_blocks(
                    project_id=agent.project_id,
                    limit=1000,
                    label_search=label_search,
                    value_search=value_search,
                )
                
                if result.get("status") == "success":
                    return {"success": True, "blocks": result.get("blocks", []), "block_count": result.get("block_count", 0)}
                else:
                    return {"success": False, "error": result.get("error_message", "Failed to list blocks")}
                    
            finally:
                session.close()
        
        @self.app.get("/dashboard/api/agents/{agent_name}/memory-blocks/{block_id}", tags=["Dashboard - Memory Blocks"])
        async def get_agent_memory_block(
            agent_name: str,
            block_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Get a specific memory block."""
            if not self.db_client:
                return {"success": False, "error": "Database not available"}
            
            session = self.db_client.get_session()
            if not session:
                return {"success": False, "error": "Database session not available"}
            
            try:
                agent = session.query(self.AgentConfig).filter_by(name=agent_name).first()
                if not agent:
                    return {"success": False, "error": f"Agent {agent_name} not found"}
                
                tool_config = agent.tool_config
                tool_config_dict = {}
                if tool_config:
                    try:
                        tool_config_dict = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
                    except json.JSONDecodeError:
                        pass
                
                if not self._has_memory_blocks(tool_config_dict):
                    return {"success": False, "error": "Agent does not have memory blocks tool configured"}
                
                from shared.utils.memory_blocks_service import MemoryBlocksService
                svc = MemoryBlocksService(self.db_client)
                result = svc.get_block(project_id=agent.project_id, block_id=block_id)
                
                if result.get("status") == "success":
                    return {"success": True, "block": result}
                else:
                    return {"success": False, "error": result.get("error_message", "Failed to get block")}
                    
            finally:
                session.close()
        
        @self.app.post("/dashboard/api/agents/{agent_name}/memory-blocks", tags=["Dashboard - Memory Blocks"])
        async def create_agent_memory_block(
            agent_name: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            label: str = Form(...),
            value: str = Form(...),
            description: Optional[str] = Form(None),
            character_limit: Optional[int] = Form(None),
            read_only: bool = Form(False),
            preserve_on_migration: bool = Form(False)
        ):
            """Create a new memory block."""
            if not self.db_client:
                return {"success": False, "error": "Database not available"}
            
            session = self.db_client.get_session()
            if not session:
                return {"success": False, "error": "Database session not available"}
            
            try:
                agent = session.query(self.AgentConfig).filter_by(name=agent_name).first()
                if not agent:
                    return {"success": False, "error": f"Agent {agent_name} not found"}
                
                tool_config = agent.tool_config
                tool_config_dict = {}
                if tool_config:
                    try:
                        tool_config_dict = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
                    except json.JSONDecodeError:
                        pass
                
                if not self._has_memory_blocks(tool_config_dict):
                    return {"success": False, "error": "Agent does not have memory blocks tool configured"}
                
                metadata = {}
                if character_limit:
                    metadata['limit'] = character_limit
                if read_only:
                    metadata['read_only'] = True
                if preserve_on_migration:
                    metadata['preserve_on_migration'] = True
                
                from shared.utils.memory_blocks_service import MemoryBlocksService
                svc = MemoryBlocksService(self.db_client)
                result = svc.create_block(
                    project_id=agent.project_id,
                    label=label,
                    value=value,
                    description=description,
                    metadata=metadata if metadata else None,
                )
                
                if result.get("status") == "success":
                    return {"success": True, "block": result}
                else:
                    return {"success": False, "error": result.get("error_message", "Failed to create block")}
                    
            finally:
                session.close()
        
        @self.app.put("/dashboard/api/agents/{agent_name}/memory-blocks/{block_id}", tags=["Dashboard - Memory Blocks"])
        async def update_agent_memory_block(
            agent_name: str,
            block_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            label: Optional[str] = Form(None),
            value: Optional[str] = Form(None),
            description: Optional[str] = Form(None),
            character_limit: Optional[int] = Form(None),
            read_only: Optional[bool] = Form(None),
            preserve_on_migration: Optional[bool] = Form(None)
        ):
            """Update a memory block."""
            if not self.db_client:
                return {"success": False, "error": "Database not available"}
            
            session = self.db_client.get_session()
            if not session:
                return {"success": False, "error": "Database session not available"}
            
            try:
                agent = session.query(self.AgentConfig).filter_by(name=agent_name).first()
                if not agent:
                    return {"success": False, "error": f"Agent {agent_name} not found"}
                
                tool_config = agent.tool_config
                tool_config_dict = {}
                if tool_config:
                    try:
                        tool_config_dict = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
                    except json.JSONDecodeError:
                        pass
                
                if not self._has_memory_blocks(tool_config_dict):
                    return {"success": False, "error": "Agent does not have memory blocks tool configured"}
                
                if value is None:
                    return {"success": False, "error": "value is required"}
                
                from shared.utils.memory_blocks_service import MemoryBlocksService
                svc = MemoryBlocksService(self.db_client)
                result = svc.modify_block(
                    project_id=agent.project_id,
                    block_id=block_id,
                    value=value,
                    description=description,
                )
                
                if result.get("status") == "success":
                    return {"success": True, "block": result}
                else:
                    return {"success": False, "error": result.get("error_message", "Failed to update block")}
                    
            finally:
                session.close()
        
        @self.app.delete("/dashboard/api/agents/{agent_name}/memory-blocks/{block_id}", tags=["Dashboard - Memory Blocks"])
        async def delete_agent_memory_block(
            agent_name: str,
            block_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Delete a memory block."""
            if not self.db_client:
                return {"success": False, "error": "Database not available"}
            
            session = self.db_client.get_session()
            if not session:
                return {"success": False, "error": "Database session not available"}
            
            try:
                agent = session.query(self.AgentConfig).filter_by(name=agent_name).first()
                if not agent:
                    return {"success": False, "error": f"Agent {agent_name} not found"}
                
                tool_config = agent.tool_config
                tool_config_dict = {}
                if tool_config:
                    try:
                        tool_config_dict = json.loads(tool_config) if isinstance(tool_config, str) else tool_config
                    except json.JSONDecodeError:
                        pass
                
                if not self._has_memory_blocks(tool_config_dict):
                    return {"success": False, "error": "Agent does not have memory blocks tool configured"}
                
                from shared.utils.memory_blocks_service import MemoryBlocksService
                svc = MemoryBlocksService(self.db_client)
                result = svc.delete_block(project_id=agent.project_id, block_id=block_id)
                
                if result.get("status") == "success":
                    return {"success": True, "message": result.get("message", "Block deleted successfully")}
                else:
                    return {"success": False, "error": result.get("error_message", "Failed to delete block")}
                    
            finally:
                session.close()
        
        @self.app.get("/dashboard/api/file-search/stores/agents", tags=["Dashboard - File Search"])
        async def get_store_agents(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            store_name: str = None
        ):
            """Get all agents using a specific store."""
            # Get store_name from query parameter to handle slashes
            if not store_name:
                from fastapi import Query
                store_name = request.query_params.get('store_name')
            
            if not store_name:
                return {"success": False, "error": "store_name parameter is required"}
            
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            agents = service.get_agents_using_store(store_name)
            return {"success": True, "agents": agents}
        
        @self.app.get("/dashboard/api/projects/{project_id}/file-search/stores", tags=["Dashboard - File Search"])
        async def get_project_file_search_stores(
            project_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Get all file search stores in a project."""
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            stores = service.get_all_stores_for_project(project_id)
            return {"success": True, "stores": stores}
        
        @self.app.post("/dashboard/api/file-search/stores/files/delete", tags=["Dashboard - File Search"])
        async def delete_file_from_store(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            store_name: str = Form(...),
            document_name: str = Form(...)
        ):
            """Delete a file from a file search store."""
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            success = service.delete_document(store_name, document_name)
            if success:
                return {"success": True, "message": "File deleted successfully"}
            else:
                return {"success": False, "error": "Failed to delete file"}
        
        @self.app.post("/dashboard/api/file-search/stores/delete", tags=["Dashboard - File Search"])
        async def delete_file_search_store(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            store_name: str = Form(...)
        ):
            """Delete a file search store and remove it from all agents."""
            from shared.utils.file_search_service import FileSearchService
            service = FileSearchService(self.db_client)
            result = service.delete_store(store_name)
            return result

        @self.app.post("/dashboard/api/agents/reinitialize-all", tags=["Dashboard - Agents"])
        async def reinitialize_all_agents(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Clear all cached agents to force reinitialization by proxying to ADK server."""
            import httpx
            from shared.utils.utils import get_adk_config
            
            print(f"🔄 [Dashboard] Reload all agents request, proxying to ADK server")
            
            try:
                adk_config = get_adk_config()
                adk_url = f"http://{adk_config['adk_host']}:{adk_config['adk_port']}/api/reload-all-agents"
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(adk_url)
                    result = response.json()
                    
                    print(f"🔄 [Dashboard] ADK server response: {result}")
                    return result
                    
            except Exception as e:
                print(f"❌ [Dashboard] Error calling ADK reload endpoint: {e}")
                import traceback
                traceback.print_exc()
                return {
                    "success": False,
                    "message": f"Error communicating with ADK server: {str(e)}"
                }
        
        # Server Control API Endpoints
        @self.app.get("/dashboard/api/server/status", tags=["Dashboard - Server Control"])
        async def get_server_status(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Get ADK server status."""
            return self._get_adk_status()

        @self.app.post("/dashboard/api/server/start", tags=["Dashboard - Server Control"])
        async def start_server(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Start ADK server."""
            return self._start_adk_server()

        @self.app.post("/dashboard/api/server/stop", tags=["Dashboard - Server Control"])
        async def stop_server(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Stop ADK server."""
            return self._stop_adk_server()

        @self.app.post("/dashboard/api/server/restart", tags=["Dashboard - Server Control"])
        async def restart_server(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Restart ADK server."""
            return self._restart_adk_server()

        # ─── Guardrail Logs API ─────────────────────────────────────────

        @self.app.get("/dashboard/api/guardrail-logs", tags=["Dashboard - Guardrails"])
        async def get_guardrail_logs(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            agent_name: str = None,
            guardrail_type: str = None,
            action_taken: str = None,
            phase: str = None,
            limit: int = 100,
            offset: int = 0,
        ):
            """Get guardrail trigger logs with optional filters."""
            if not self.db_client:
                return {"logs": [], "total": 0}
            session = self.db_client.get_session()
            if not session:
                return {"logs": [], "total": 0}
            try:
                from sqlalchemy import func
                query = session.query(self.GuardrailLog)
                if agent_name:
                    query = query.filter(self.GuardrailLog.agent_name == agent_name)
                if guardrail_type:
                    query = query.filter(self.GuardrailLog.guardrail_type == guardrail_type)
                if action_taken:
                    query = query.filter(self.GuardrailLog.action_taken == action_taken)
                if phase:
                    query = query.filter(self.GuardrailLog.phase == phase)
                total = query.count()
                logs = (
                    query.order_by(self.GuardrailLog.timestamp.desc())
                    .offset(offset)
                    .limit(limit)
                    .all()
                )
                return {"logs": [l.to_dict() for l in logs], "total": total}
            except Exception as e:
                logger.error(f"Error fetching guardrail logs: {e}")
                return {"logs": [], "total": 0, "error": str(e)}
            finally:
                session.close()

        # ─── Audit Logs API (EU AI Act compliance) ─────────────────────

        @self.app.get("/dashboard/api/audit-logs", tags=["Dashboard - Audit"])
        async def get_audit_logs(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            actor: Optional[str] = None,
            action: Optional[str] = None,
            resource_type: Optional[str] = None,
            resource_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            limit: int = 100,
            offset: int = 0,
        ):
            """List audit logs with filters. Append-only, immutable."""
            if not self.db_client:
                return {"logs": [], "total": 0}
            session = self.db_client.get_session()
            if not session:
                return {"logs": [], "total": 0}
            try:
                from datetime import datetime as dt
                query = session.query(self.AuditLog)
                if actor:
                    query = query.filter(self.AuditLog.actor == actor)
                if action:
                    query = query.filter(self.AuditLog.action == action)
                if resource_type:
                    query = query.filter(self.AuditLog.resource_type == resource_type)
                if resource_id:
                    query = query.filter(self.AuditLog.resource_id == resource_id)
                if date_from:
                    try:
                        query = query.filter(self.AuditLog.timestamp >= dt.fromisoformat(date_from.replace("Z", "+00:00")))
                    except ValueError:
                        pass
                if date_to:
                    try:
                        query = query.filter(self.AuditLog.timestamp <= dt.fromisoformat(date_to.replace("Z", "+00:00")))
                    except ValueError:
                        pass
                total = query.count()
                logs = (
                    query.order_by(self.AuditLog.timestamp.desc())
                    .offset(offset)
                    .limit(limit)
                    .all()
                )
                return {"logs": [l.to_dict() for l in logs], "total": total}
            except Exception as e:
                logger.error("Error fetching audit logs: %s", e)
                return {"logs": [], "total": 0, "error": str(e)}
            finally:
                session.close()

        @self.app.get("/dashboard/api/audit-logs/export", tags=["Dashboard - Audit"])
        async def export_audit_logs(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            format: str = "json",
            actor: Optional[str] = None,
            action: Optional[str] = None,
            resource_type: Optional[str] = None,
            resource_id: Optional[str] = None,
            date_from: Optional[str] = None,
            date_to: Optional[str] = None,
            limit: int = 10000,
        ):
            """Export audit logs as JSON or CSV for compliance reporting."""
            if not self.db_client:
                raise HTTPException(status_code=503, detail="Database unavailable")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=503, detail="Database unavailable")
            try:
                from datetime import datetime as dt
                query = session.query(self.AuditLog)
                if actor:
                    query = query.filter(self.AuditLog.actor == actor)
                if action:
                    query = query.filter(self.AuditLog.action == action)
                if resource_type:
                    query = query.filter(self.AuditLog.resource_type == resource_type)
                if resource_id:
                    query = query.filter(self.AuditLog.resource_id == resource_id)
                if date_from:
                    try:
                        query = query.filter(self.AuditLog.timestamp >= dt.fromisoformat(date_from.replace("Z", "+00:00")))
                    except ValueError:
                        pass
                if date_to:
                    try:
                        query = query.filter(self.AuditLog.timestamp <= dt.fromisoformat(date_to.replace("Z", "+00:00")))
                    except ValueError:
                        pass
                rows = query.order_by(self.AuditLog.timestamp.asc()).limit(limit).all()
                data = [r.to_dict() for r in rows]
                if format.lower() == "csv":
                    import csv
                    from io import StringIO
                    out = StringIO()
                    if data:
                        writer = csv.DictWriter(out, fieldnames=["id", "timestamp", "actor", "action", "resource_type", "resource_id", "details", "ip_address"])
                        writer.writeheader()
                        for row in data:
                            row_flat = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()}
                            writer.writerow(row_flat)
                    from fastapi.responses import PlainTextResponse
                    return PlainTextResponse(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit_logs.csv"})
                from fastapi.responses import Response
                return Response(content=json.dumps(data), media_type="application/json", headers={"Content-Disposition": "attachment; filename=audit_logs.json"})
            except Exception as e:
                logger.error("Export audit logs failed: %s", e)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.post("/dashboard/api/audit-logs/retention", tags=["Dashboard - Audit"])
        async def run_audit_retention(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Run retention policy: delete entries older than AUDIT_RETENTION_DAYS."""
            try:
                from shared.utils.audit_service import run_retention
                result = run_retention()
                return result
            except Exception as e:
                logger.error("Audit retention run failed: %s", e)
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/dashboard/api/audit-logs/retention-config", tags=["Dashboard - Audit"])
        async def get_audit_retention_config(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Get configured retention days (0 = keep forever)."""
            try:
                from shared.utils.audit_service import get_retention_days
                return {"retention_days": get_retention_days()}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # ── Eval Framework ────────────────────────────────────────────────────

        @self.app.get("/dashboard/evals", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_evals(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            return self.templates.TemplateResponse(request, "dashboard/evals.html", {
                "request": request,
                "page_title": "Evals",
                "username": username,
                "is_admin": True,
            })

        @self.app.get("/dashboard/api/evals/agent/{agent_name}/versions-list", tags=["Dashboard - Evals"])
        async def list_agent_versions_for_eval(agent_name: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Return available config versions for an agent — used to populate the run modal dropdown."""
            if not self.db_client:
                return {"versions": []}
            session = self.db_client.get_session()
            if not session:
                return {"versions": []}
            try:
                ac = session.query(self.AgentConfig).filter(self.AgentConfig.name == agent_name).first()
                if not ac:
                    return {"versions": []}
                versions = (
                    session.query(self.AgentConfigVersion)
                    .filter(self.AgentConfigVersion.agent_config_id == ac.id)
                    .order_by(self.AgentConfigVersion.version_number.desc())
                    .all()
                )
                return {
                    "versions": [
                        {
                            "id": v.id,
                            "version_number": v.version_number,
                            "tag": v.tag,
                            "change_type": v.change_type,
                            "created_at": v.created_at.isoformat() if v.created_at else None,
                        }
                        for v in versions
                    ]
                }
            except Exception as e:
                logger.error("Error listing versions for %s: %s", agent_name, e)
                return {"versions": [], "error": str(e)}
            finally:
                session.close()

        @self.app.get("/dashboard/api/evals", tags=["Dashboard - Evals"])
        async def list_eval_suites(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """List test suites grouped by agent with latest avg score and last run time."""
            if not self.db_client:
                return {"suites": []}
            session = self.db_client.get_session()
            if not session:
                return {"suites": []}
            try:
                from sqlalchemy import func
                rows = (
                    session.query(
                        self.TestCase.agent_name,
                        func.count(self.TestCase.id).label("test_case_count"),
                    )
                    .filter(self.TestCase.is_active.is_(True))
                    .group_by(self.TestCase.agent_name)
                    .all()
                )
                suites = []
                for row in rows:
                    latest = (
                        session.query(
                            func.max(self.EvalResult.run_at).label("last_run"),
                            func.avg(self.EvalResult.score).label("avg_score"),
                        )
                        .join(self.TestCase, self.EvalResult.test_case_id == self.TestCase.id)
                        .filter(self.TestCase.agent_name == row.agent_name)
                        .first()
                    )
                    suites.append({
                        "agent_name": row.agent_name,
                        "test_case_count": row.test_case_count,
                        "last_run": latest.last_run.isoformat() if latest and latest.last_run else None,
                        "avg_score": round(float(latest.avg_score), 3) if latest and latest.avg_score is not None else None,
                    })
                return {"suites": suites}
            except Exception as e:
                logger.error("Error listing eval suites: %s", e)
                return {"suites": [], "error": str(e)}
            finally:
                session.close()

        @self.app.get("/dashboard/api/evals/agent/{agent_name}/history", tags=["Dashboard - Evals"])
        async def get_eval_history(agent_name: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Score history per version for Chart.js: [{version_number, version_id, avg_score, pass_rate, run_at}]"""
            if not self.db_client:
                return {"history": []}
            session = self.db_client.get_session()
            if not session:
                return {"history": []}
            try:
                from sqlalchemy import func, case
                rows = (
                    session.query(
                        self.AgentConfigVersion.id.label("version_id"),
                        self.AgentConfigVersion.version_number,
                        func.avg(self.EvalResult.score).label("avg_score"),
                        func.avg(
                            case((self.EvalResult.passed == True, 1), else_=0)
                        ).label("pass_rate"),
                        func.max(self.EvalResult.run_at).label("run_at"),
                    )
                    .join(self.EvalResult, self.AgentConfigVersion.id == self.EvalResult.version_id)
                    .join(self.TestCase, self.EvalResult.test_case_id == self.TestCase.id)
                    .filter(self.TestCase.agent_name == agent_name)
                    .group_by(self.AgentConfigVersion.id, self.AgentConfigVersion.version_number)
                    .order_by(self.AgentConfigVersion.version_number.asc())
                    .all()
                )
                history = [
                    {
                        "version_id": r.version_id,
                        "version_number": r.version_number,
                        "avg_score": round(float(r.avg_score), 3) if r.avg_score is not None else None,
                        "pass_rate": round(float(r.pass_rate), 3) if r.pass_rate is not None else None,
                        "run_at": r.run_at.isoformat() if r.run_at else None,
                    }
                    for r in rows
                ]
                return {"history": history}
            except Exception as e:
                logger.error("Error fetching eval history for %s: %s", agent_name, e)
                return {"history": [], "error": str(e)}
            finally:
                session.close()

        @self.app.get("/dashboard/api/evals/agent/{agent_name}", tags=["Dashboard - Evals"])
        async def get_agent_test_cases(agent_name: str, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Return all active test cases for an agent with each case's latest eval result."""
            if not self.db_client:
                return {"test_cases": []}
            session = self.db_client.get_session()
            if not session:
                return {"test_cases": []}
            try:
                tcs = (
                    session.query(self.TestCase)
                    .filter(
                        self.TestCase.agent_name == agent_name,
                        self.TestCase.is_active.is_(True),
                    )
                    .order_by(self.TestCase.id.asc())
                    .all()
                )
                result = []
                for tc in tcs:
                    latest_result = (
                        session.query(self.EvalResult)
                        .filter(self.EvalResult.test_case_id == tc.id)
                        .order_by(self.EvalResult.run_at.desc())
                        .first()
                    )
                    d = tc.to_dict()
                    d["latest_result"] = latest_result.to_dict() if latest_result else None
                    result.append(d)
                return {"test_cases": result}
            except Exception as e:
                logger.error("Error fetching test cases for %s: %s", agent_name, e)
                return {"test_cases": [], "error": str(e)}
            finally:
                session.close()

        @self.app.post("/dashboard/api/evals", tags=["Dashboard - Evals"])
        async def create_test_case(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Create a new test case."""
            body = await request.json()
            agent_name = body.get("agent_name", "").strip()
            input_text = body.get("input", "").strip()
            expected = body.get("expected_output", "").strip()
            eval_method = body.get("eval_method", "exact_match").strip()

            if not agent_name or not input_text or not expected:
                raise HTTPException(status_code=400, detail="agent_name, input, and expected_output are required")
            if eval_method not in ("exact_match", "semantic", "llm_judge"):
                raise HTTPException(status_code=400, detail="eval_method must be exact_match, semantic, or llm_judge")

            if not self.db_client:
                raise HTTPException(status_code=503, detail="Database not available")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=503, detail="Database session not available")
            try:
                tc = self.TestCase(
                    agent_name=agent_name,
                    version_id=body.get("version_id"),
                    input=input_text,
                    expected_output=expected,
                    eval_method=eval_method,
                    judge_model=body.get("judge_model"),
                    threshold=float(body.get("threshold", 0.7)),
                    created_by=username,
                )
                session.add(tc)
                session.commit()
                session.refresh(tc)
                return {"test_case": tc.to_dict()}
            except Exception as e:
                session.rollback()
                logger.error("Error creating test case: %s", e)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.put("/dashboard/api/evals/{test_case_id}", tags=["Dashboard - Evals"])
        async def update_test_case(test_case_id: int, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Update a test case's fields."""
            body = await request.json()
            if not self.db_client:
                raise HTTPException(status_code=503, detail="Database not available")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=503, detail="Database session not available")
            try:
                tc = session.query(self.TestCase).filter(self.TestCase.id == test_case_id).first()
                if not tc:
                    raise HTTPException(status_code=404, detail="Test case not found")
                updatable = ("input", "expected_output", "eval_method", "judge_model", "threshold", "version_id")
                for field in updatable:
                    if field in body:
                        setattr(tc, field, body[field])
                if "eval_method" in body and body["eval_method"] not in ("exact_match", "semantic", "llm_judge"):
                    raise HTTPException(status_code=400, detail="Invalid eval_method")
                session.commit()
                session.refresh(tc)
                return {"test_case": tc.to_dict()}
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                logger.error("Error updating test case %s: %s", test_case_id, e)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.delete("/dashboard/api/evals/{test_case_id}", tags=["Dashboard - Evals"])
        async def delete_test_case(test_case_id: int, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Soft-delete a test case (sets is_active=False)."""
            if not self.db_client:
                raise HTTPException(status_code=503, detail="Database not available")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=503, detail="Database session not available")
            try:
                tc = session.query(self.TestCase).filter(self.TestCase.id == test_case_id).first()
                if not tc:
                    raise HTTPException(status_code=404, detail="Test case not found")
                tc.is_active = False
                session.commit()
                return {"success": True}
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                logger.error("Error deleting test case %s: %s", test_case_id, e)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.post("/dashboard/api/evals/version/{version_id}/run", tags=["Dashboard - Evals"])
        async def run_version_eval_suite(version_id: int, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """
            Run the eval suite for all active test cases linked to the agent of this version.

            Body: {"results": [{"test_case_id": int, "actual_output": str}]}

            If results is empty, returns a dry-run response with the list of inputs
            that need to be evaluated (for the version history modal "Run Evals" flow).
            """
            body = await request.json()
            # submitted is optional — if absent or empty, agent is invoked automatically
            submitted = body.get("results", [])

            if not self.db_client:
                raise HTTPException(status_code=503, detail="Database not available")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=503, detail="Database session not available")
            try:
                version = session.query(self.AgentConfigVersion).filter(self.AgentConfigVersion.id == version_id).first()
                if not version:
                    raise HTTPException(status_code=404, detail="Version not found")

                # Derive agent name from the version's config snapshot
                snapshot = version.get_snapshot() if hasattr(version, "get_snapshot") else {}
                agent_name = snapshot.get("name") if snapshot else None
                if not agent_name:
                    ac = session.query(self.AgentConfig).filter(self.AgentConfig.id == version.agent_config_id).first()
                    agent_name = ac.name if ac else None
                if not agent_name:
                    raise HTTPException(status_code=400, detail="Could not determine agent name for this version")

                # Fetch active test cases for this agent
                test_cases = (
                    session.query(self.TestCase)
                    .filter(self.TestCase.agent_name == agent_name, self.TestCase.is_active.is_(True))
                    .all()
                )

                if not test_cases:
                    return {"passed": 0, "failed": 0, "avg_score": None, "pass_rate": None, "results": []}

                # Build lookup from any manually supplied actual_outputs
                output_map = {r["test_case_id"]: r["actual_output"] for r in submitted if r.get("actual_output")}

                from shared.utils.eval_runner import EvalRunner
                runner = EvalRunner()

                persisted = []
                for tc in test_cases:
                    actual_output = output_map.get(tc.id)
                    if not actual_output:
                        # Auto-invoke the agent for this test case
                        actual_output = await self._invoke_agent_for_eval(agent_name, tc.input)
                    run_result = runner.score_output(tc, actual_output, version_id)
                    er = self.EvalResult(
                        test_case_id=run_result.test_case_id,
                        version_id=run_result.version_id,
                        actual_output=run_result.actual_output,
                        score=run_result.score,
                        passed=run_result.passed,
                        eval_method=run_result.eval_method,
                        details=run_result.details,
                        error=run_result.error,
                    )
                    session.add(er)
                    persisted.append(run_result)

                session.commit()

                if not persisted:
                    return {"passed": 0, "failed": 0, "avg_score": None, "pass_rate": None, "results": []}

                scored = [r for r in persisted if r.score is not None]
                passed_count = sum(1 for r in persisted if r.passed)
                failed_count = sum(1 for r in persisted if r.passed is False)
                avg_score = round(sum(r.score for r in scored) / len(scored), 3) if scored else None
                pass_rate = round(passed_count / len(persisted), 3) if persisted else None

                # Regression alert: compare against previous version's avg score
                regression_alert = False
                try:
                    prev_version = (
                        session.query(self.AgentConfigVersion)
                        .filter(
                            self.AgentConfigVersion.agent_config_id == version.agent_config_id,
                            self.AgentConfigVersion.version_number < version.version_number,
                        )
                        .order_by(self.AgentConfigVersion.version_number.desc())
                        .first()
                    )
                    if prev_version and avg_score is not None:
                        from sqlalchemy import func
                        prev_avg_row = (
                            session.query(func.avg(self.EvalResult.score))
                            .join(self.TestCase, self.EvalResult.test_case_id == self.TestCase.id)
                            .filter(
                                self.EvalResult.version_id == prev_version.id,
                                self.TestCase.agent_name == agent_name,
                            )
                            .scalar()
                        )
                        if prev_avg_row is not None:
                            prev_avg = float(prev_avg_row)
                            regression = prev_avg - avg_score
                            if regression > 0.05:
                                regression_alert = True
                                webhook_url = os.getenv("EVAL_REGRESSION_WEBHOOK_URL")
                                if webhook_url:
                                    try:
                                        import httpx
                                        from datetime import datetime, timezone
                                        payload = {
                                            "type": "eval_regression_alert",
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                            "agent_name": agent_name,
                                            "version_id": version_id,
                                            "new_avg_score": avg_score,
                                            "prev_avg_score": round(prev_avg, 3),
                                            "regression": round(regression, 4),
                                        }
                                        import asyncio
                                        async with httpx.AsyncClient() as client:
                                            await client.post(webhook_url, json=payload, timeout=10.0)
                                    except Exception as wh_err:
                                        logger.warning("Regression webhook failed: %s", wh_err)
                except Exception as reg_err:
                    logger.warning("Regression check failed: %s", reg_err)

                return {
                    "passed": passed_count,
                    "failed": failed_count,
                    "avg_score": avg_score,
                    "pass_rate": pass_rate,
                    "regression_alert": regression_alert,
                    "results": [
                        {
                            "test_case_id": r.test_case_id,
                            "score": r.score,
                            "passed": r.passed,
                            "eval_method": r.eval_method,
                            "details": r.details,
                            "error": r.error,
                        }
                        for r in persisted
                    ],
                }
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                logger.error("Error running eval suite for version %s: %s", version_id, e)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.post("/dashboard/api/evals/{test_case_id}/run", tags=["Dashboard - Evals"])
        async def run_single_eval(test_case_id: int, request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """
            Score a single test case.
            Body: {"version_id": int, "actual_output"?: str}
            If actual_output is omitted the agent is invoked automatically.
            """
            body = await request.json()
            actual_output = (body.get("actual_output") or "").strip()
            version_id = body.get("version_id")

            if not version_id:
                raise HTTPException(status_code=400, detail="version_id is required")

            if not self.db_client:
                raise HTTPException(status_code=503, detail="Database not available")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=503, detail="Database session not available")
            try:
                tc = session.query(self.TestCase).filter(self.TestCase.id == test_case_id).first()
                if not tc:
                    raise HTTPException(status_code=404, detail="Test case not found")

                if not actual_output:
                    actual_output = await self._invoke_agent_for_eval(tc.agent_name, tc.input)

                from shared.utils.eval_runner import EvalRunner
                runner = EvalRunner()
                run_result = runner.score_output(tc, actual_output, version_id)

                er = self.EvalResult(
                    test_case_id=run_result.test_case_id,
                    version_id=run_result.version_id,
                    actual_output=run_result.actual_output,
                    score=run_result.score,
                    passed=run_result.passed,
                    eval_method=run_result.eval_method,
                    details=run_result.details,
                    error=run_result.error,
                )
                session.add(er)
                session.commit()
                session.refresh(er)
                return {"result": er.to_dict()}
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                logger.error("Error running eval for test case %s: %s", test_case_id, e)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        # ── Trigger Engine ────────────────────────────────────────────────────

        @self.app.get("/dashboard/triggers", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_triggers(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            if not self._get_is_admin(request):
                return RedirectResponse(url="/dashboard/workroom", status_code=302)
            projects = self._get_all_projects()
            agents = self._get_all_agent_configs()
            return self.templates.TemplateResponse(request, "dashboard/triggers.html", {
                "request": request,
                "page_title": "Triggers",
                "username": username,
                "projects": projects,
                "agents": [{"name": a.get("name", ""), "type": a.get("type", ""), "project_id": a.get("project_id"), "parent_agents": a.get("parent_agents") or []} for a in agents],
                "is_admin": True,
            })

        @self.app.get("/dashboard/workroom", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_workroom(request: Request, agent: Optional[str] = None, session: Optional[str] = None, username: str = Depends(self._get_auth_user_dependency)):
            is_admin = self._get_is_admin(request)
            all_agents = self._get_all_agent_configs()
            # Only expose root agents (no parent_agents) in the Work Room
            agent_list = []
            for a in all_agents:
                if not a.get("name"):
                    continue
                parents = a.get("parent_agents") or []
                if parents:
                    continue
                desc = a.get("description") or ""
                if not desc:
                    instr = a.get("instruction") or ""
                    desc = instr[:120] + ("…" if len(instr) > 120 else "")
                agent_list.append({
                    "name": a.get("name", ""),
                    "display_name": a.get("display_name") or a.get("name", ""),
                    "description": desc,
                    "type": a.get("type", "llm"),
                })
            return self.templates.TemplateResponse(request, "dashboard/workroom.html", {
                "request": request,
                "page_title": "Work Room",
                "username": username,
                "agents": agent_list,
                "selected_agent": agent or "",
                "selected_session": session or "",
                "is_admin": is_admin,
            })

        @self.app.post("/dashboard/api/workroom/title", tags=["Dashboard - WorkRoom"])
        async def generate_session_title(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Generate and persist a descriptive title for a Work Room session."""
            import httpx
            body = await request.json()
            agent = body.get("agent", "").strip()
            user_id = body.get("user_id", "").strip()
            session_id = body.get("session_id", "").strip()
            if not agent or not user_id or not session_id:
                raise HTTPException(status_code=400, detail="agent, user_id, session_id required")

            adk_session_url = f"http://{self.adk_host}:{self.adk_port}/apps/{agent}/users/{user_id}/sessions/{session_id}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(adk_session_url)
                if not resp.is_success:
                    raise HTTPException(status_code=502, detail="Failed to fetch session from ADK")
                data = resp.json()
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=f"ADK unreachable: {exc}")

            # Return existing title without regenerating
            existing_title = (data.get("state") or {}).get("_title", "")
            if existing_title:
                return {"title": existing_title}

            # Extract first user message
            first_user_text = ""
            for ev in (data.get("events") or []):
                if ev.get("author") == "user":
                    parts = (ev.get("content") or {}).get("parts") or []
                    for p in parts:
                        if p.get("text"):
                            first_user_text = p["text"]
                            break
                if first_user_text:
                    break

            if not first_user_text:
                return {"title": ""}

            # Generate a short title via LiteLLM
            title = ""
            model = os.environ.get("TITLE_GEN_MODEL")
            if not model:
                title = session_id
            else:
                try:
                    import litellm  # type: ignore
                    gen_resp = litellm.completion(
                        model=model,
                        messages=[{"role": "user", "content": (
                            "Generate a concise 4-6 word title for this conversation. "
                            "Reply with ONLY the title, no quotes or trailing punctuation.\n\n"
                            f"First message: {first_user_text[:400]}"
                        )}],
                        temperature=0.3,
                        max_tokens=30,
                    )
                    title = gen_resp.choices[0].message.content.strip().strip('"').strip("'").rstrip(".")
                except Exception as exc:
                    logger.warning("Session title generation failed: %s", exc)
                    title = first_user_text[:50] + ("…" if len(first_user_text) > 50 else "")

            # Persist to ADK session state
            if title:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.patch(adk_session_url, json={"stateDelta": {"_title": title}})
                except Exception as exc:
                    logger.warning("Session title PATCH failed: %s", exc)

            return {"title": title}

        @self.app.get("/dashboard/api/triggers", tags=["Dashboard - Triggers"])
        async def list_triggers(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
            project_id: Optional[int] = None,
            agent_name: Optional[str] = None,
        ):
            """List all triggers with optional filters."""
            if not self.db_client:
                return {"triggers": []}
            session = self.db_client.get_session()
            if not session:
                return {"triggers": []}
            try:
                q = session.query(self.AgentTrigger)
                if project_id is not None:
                    q = q.filter(self.AgentTrigger.project_id == project_id)
                if agent_name:
                    q = q.filter(self.AgentTrigger.agent_name == agent_name)
                rows = q.order_by(self.AgentTrigger.created_at.desc()).all()
                return {"triggers": [r.to_dict() for r in rows]}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.get("/dashboard/api/system-jobs", tags=["Dashboard - Triggers"])
        async def list_system_jobs(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """List all active programmatically-registered system cron jobs."""
            if not self._get_is_admin(request):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Forbidden")
            
            try:
                from shared.utils.trigger_runner import get_trigger_runner
                runner = get_trigger_runner()
                scheduler = runner._scheduler
                
                result = []
                if scheduler and scheduler.running:
                    jobs = scheduler.get_jobs()
                    for job in jobs:
                        result.append({
                            "id": job.id,
                            "name": job.name,
                            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                            "trigger": str(job.trigger)
                        })
                return {"jobs": result}
            except Exception as e:
                from fastapi import HTTPException
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/dashboard/api/system-jobs/{job_id}/toggle", tags=["Dashboard - Triggers"])
        async def toggle_system_job(
            job_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Pause or resume a system background job."""
            if not self._get_is_admin(request):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Forbidden")
            
            try:
                from shared.utils.trigger_runner import get_trigger_runner
                runner = get_trigger_runner()
                scheduler = runner._scheduler
                if not scheduler or not scheduler.running:
                    return {"success": False, "error": "Scheduler is not running"}
                
                job = scheduler.get_job(job_id)
                if not job:
                    return {"success": False, "error": "Job not found"}
                
                if job.next_run_time is None:
                    scheduler.resume_job(job_id)
                    status = "enabled"
                else:
                    scheduler.pause_job(job_id)
                    status = "disabled"
                
                return {"success": True, "status": status}
            except Exception as e:
                return {"success": False, "error": str(e)}

        @self.app.post("/dashboard/api/system-jobs/{job_id}/reschedule", tags=["Dashboard - Triggers"])
        async def reschedule_system_job(
            job_id: str,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency)
        ):
            """Reschedule a system background job using a standard cron expression."""
            if not self._get_is_admin(request):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Forbidden")
            
            try:
                body = await request.json()
                cron_expr = body.get("cron_expression")
                if not cron_expr:
                    return {"success": False, "error": "Missing cron_expression"}
                
                from shared.utils.trigger_runner import get_trigger_runner
                from apscheduler.triggers.cron import CronTrigger
                runner = get_trigger_runner()
                scheduler = runner._scheduler
                if not scheduler or not scheduler.running:
                    return {"success": False, "error": "Scheduler is not running"}
                
                job = scheduler.get_job(job_id)
                if not job:
                    return {"success": False, "error": "Job not found"}
                
                try:
                    trigger = CronTrigger.from_crontab(cron_expr, timezone="UTC")
                except Exception as pe:
                    return {"success": False, "error": f"Invalid cron expression: {pe}"}
                
                scheduler.reschedule_job(job_id, trigger=trigger)
                updated_job = scheduler.get_job(job_id)
                
                return {
                    "success": True,
                    "next_run_time": updated_job.next_run_time.isoformat() if updated_job.next_run_time else None,
                    "trigger": str(updated_job.trigger)
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        @self.app.post("/dashboard/api/triggers", tags=["Dashboard - Triggers"])
        async def create_trigger(
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Create a new trigger. Returns trigger + fire_key (webhook triggers only, shown once)."""
            import re as _re
            import secrets as _secrets
            from shared.utils.trigger_runner import get_trigger_runner, generate_webhook_path

            if not self.db_client:
                raise HTTPException(status_code=500, detail="Database unavailable")
            body = await request.json()
            trigger_type = body.get("trigger_type", "cron")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database unavailable")
            try:
                raw_fire_key = None
                fire_key_hash = None
                webhook_path = None

                if trigger_type == "webhook":
                    name = body.get("name", "trigger")
                    webhook_path = generate_webhook_path(name)
                    # Ensure uniqueness (collision is astronomically unlikely but safe to check)
                    for _ in range(5):
                        exists = session.query(self.AgentTrigger).filter(
                            self.AgentTrigger.webhook_path == webhook_path
                        ).first()
                        if not exists:
                            break
                        webhook_path = generate_webhook_path(name)
                    raw_fire_key, fire_key_hash = get_trigger_runner().generate_fire_key()

                trigger = self.AgentTrigger(
                    name=body.get("name", "").strip(),
                    description=body.get("description", ""),
                    trigger_type=trigger_type,
                    agent_name=body.get("agent_name", ""),
                    project_id=int(body.get("project_id", 0)),
                    prompt=body.get("prompt", ""),
                    cron_expression=body.get("cron_expression"),
                    webhook_path=webhook_path,
                    fire_key_hash=fire_key_hash,
                    output_type=body.get("output_type", "memory_block"),
                    is_enabled=body.get("is_enabled", True),
                    created_by=username,
                )
                trigger.set_output_config(body.get("output_config") or {})
                session.add(trigger)
                session.commit()
                session.refresh(trigger)
                get_trigger_runner().sync_cron_jobs()
                result = {"trigger": trigger.to_dict()}
                if raw_fire_key:
                    result["fire_key"] = raw_fire_key
                return result
            except Exception as e:
                session.rollback()
                logger.error("create_trigger error: %s", e)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.put("/dashboard/api/triggers/{trigger_id}", tags=["Dashboard - Triggers"])
        async def update_trigger(
            trigger_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Update a trigger. Pass regenerate_fire_key=true to rotate the webhook key."""
            from shared.utils.trigger_runner import get_trigger_runner

            if not self.db_client:
                raise HTTPException(status_code=500, detail="Database unavailable")
            body = await request.json()
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database unavailable")
            try:
                trigger = session.query(self.AgentTrigger).filter(
                    self.AgentTrigger.id == trigger_id
                ).first()
                if not trigger:
                    raise HTTPException(status_code=404, detail="Trigger not found")

                for field in ("name", "description", "trigger_type", "agent_name", "prompt",
                              "cron_expression", "output_type"):
                    if field in body:
                        setattr(trigger, field, body[field])
                if "project_id" in body:
                    trigger.project_id = int(body["project_id"])
                if "is_enabled" in body:
                    trigger.is_enabled = bool(body["is_enabled"])
                if "output_config" in body:
                    trigger.set_output_config(body["output_config"] or {})

                raw_fire_key = None
                if body.get("regenerate_fire_key") and trigger.trigger_type == "webhook":
                    raw_fire_key, trigger.fire_key_hash = get_trigger_runner().generate_fire_key()

                session.commit()
                session.refresh(trigger)
                get_trigger_runner().sync_cron_jobs()
                result = {"trigger": trigger.to_dict()}
                if raw_fire_key:
                    result["fire_key"] = raw_fire_key
                return result
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                logger.error("update_trigger %s error: %s", trigger_id, e)
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.delete("/dashboard/api/triggers/{trigger_id}", tags=["Dashboard - Triggers"])
        async def delete_trigger(
            trigger_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Delete a trigger."""
            from shared.utils.trigger_runner import get_trigger_runner

            if not self.db_client:
                raise HTTPException(status_code=500, detail="Database unavailable")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database unavailable")
            try:
                trigger = session.query(self.AgentTrigger).filter(
                    self.AgentTrigger.id == trigger_id
                ).first()
                if not trigger:
                    raise HTTPException(status_code=404, detail="Trigger not found")
                session.delete(trigger)
                session.commit()
                get_trigger_runner().sync_cron_jobs()
                return {"success": True}
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.post("/dashboard/api/triggers/{trigger_id}/toggle", tags=["Dashboard - Triggers"])
        async def toggle_trigger(
            trigger_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Toggle a trigger's is_enabled state."""
            from shared.utils.trigger_runner import get_trigger_runner

            if not self.db_client:
                raise HTTPException(status_code=500, detail="Database unavailable")
            session = self.db_client.get_session()
            if not session:
                raise HTTPException(status_code=500, detail="Database unavailable")
            try:
                trigger = session.query(self.AgentTrigger).filter(
                    self.AgentTrigger.id == trigger_id
                ).first()
                if not trigger:
                    raise HTTPException(status_code=404, detail="Trigger not found")
                trigger.is_enabled = not trigger.is_enabled
                session.commit()
                session.refresh(trigger)
                get_trigger_runner().sync_cron_jobs()
                return {"trigger": trigger.to_dict()}
            except HTTPException:
                raise
            except Exception as e:
                session.rollback()
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                session.close()

        @self.app.post("/dashboard/api/triggers/{trigger_id}/test-fire", tags=["Dashboard - Triggers"])
        async def test_fire_trigger(
            trigger_id: int,
            request: Request,
            username: str = Depends(self._get_auth_user_dependency),
        ):
            """Immediately execute a trigger and return the result."""
            from shared.utils.trigger_runner import get_trigger_runner
            result = get_trigger_runner().execute_trigger(trigger_id)
            if result.get("status") == "error":
                raise HTTPException(status_code=500, detail=result.get("message", "Trigger execution failed"))
            return {"result": result}

        @self.app.post("/triggers/{trigger_id}/fire", tags=["Dashboard - Triggers"])
        async def fire_trigger_webhook(trigger_id: int, request: Request):
            """
            Webhook fire endpoint — authenticate with X-MATE-Trigger-Key header,
            ?key= query param, or standard dashboard bearer/basic auth.
            """
            from shared.utils.trigger_runner import get_trigger_runner, TriggerRunner

            if not self.db_client:
                raise HTTPException(status_code=500, detail="Database unavailable")

            # 1. Try fire key auth first (for external callers)
            raw_key = (
                request.headers.get("X-MATE-Trigger-Key")
                or request.query_params.get("key")
            )
            authed = False
            if raw_key:
                session = self.db_client.get_session()
                if not session:
                    raise HTTPException(status_code=500, detail="Database unavailable")
                try:
                    trigger_row = session.query(self.AgentTrigger).filter(
                        self.AgentTrigger.id == trigger_id
                    ).first()
                    if trigger_row and trigger_row.fire_key_hash:
                        authed = TriggerRunner.verify_fire_key(raw_key, trigger_row.fire_key_hash)
                finally:
                    session.close()
                if not authed:
                    raise HTTPException(status_code=403, detail="Invalid trigger key")
            else:
                # 2. Fall back to standard dashboard auth (bearer/basic/cookie)
                try:
                    from server.auth import require_dashboard_auth
                    require_dashboard_auth(request)
                    authed = True
                except Exception:
                    raise HTTPException(
                        status_code=403,
                        detail="Authentication required: provide X-MATE-Trigger-Key header or dashboard credentials",
                    )

            result = get_trigger_runner().execute_trigger(trigger_id)
            if result.get("status") == "error":
                raise HTTPException(status_code=500, detail=result.get("message", "Trigger execution failed"))
            return result

