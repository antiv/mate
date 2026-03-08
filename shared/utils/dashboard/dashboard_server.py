"""
Dashboard Server Implementation
Basic dashboard endpoints without complex service dependencies
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.exc import IntegrityError
from fastapi import FastAPI, Request, HTTPException, Depends, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from shared.utils import audit_service

logger = logging.getLogger(__name__)


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
            from shared.utils.models import AgentConfig, AgentConfigVersion, Project, User, TokenUsageLog, GuardrailLog, AuditLog
            
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
            
            print("✅ Dashboard database services initialized successfully")
        except Exception as e:
            print(f"⚠️  Dashboard database services initialization error: {e}")
            # Set defaults if services fail to initialize
            self.db_client = None
            self.user_service = None
            self.token_service = None
    
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
        """Delete a project and associated agents."""
        if not self.db_client:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        session = self.db_client.get_session()
        if not session:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        try:
            project = session.query(self.Project).filter(self.Project.id == project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            
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
                            description=agent_data.get("description"),
                            instruction=agent_data.get("instruction"),
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
            
            result = {
                "success": True,
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "errors": errors
            }
            if memory_blocks_imported or memory_blocks_skipped:
                result["memory_blocks_imported"] = memory_blocks_imported
                result["memory_blocks_skipped"] = memory_blocks_skipped
            return result
            
        except Exception as e:
            session.rollback()
            print(f"Error importing agent configs: {e}")
            return {"error": str(e)}
        finally:
            session.close()
    
    def _import_template(self, template_id: str, project_name: Optional[str] = None, changed_by: str = None) -> Dict[str, Any]:
        """Import a template: create project, agents, memory blocks. Apply name prefix to avoid collisions."""
        template = self.template_service.get_template(template_id)
        if not template:
            return {"error": f"Template not found: {template_id}"}
        
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
        agent_prefix = (template.get("template_meta") or {}).get("agent_prefix") or "tpl"
        replace_with = f"{slug}_" if agent_prefix.endswith("_") else slug
        
        # Build replacement map: old_agent_name -> new_agent_name
        agents_data = template.get("agents") or []
        name_map = {}
        for a in agents_data:
            old_name = a.get("name", "")
            if old_name:
                new_name = old_name.replace(agent_prefix, replace_with, 1) if agent_prefix in old_name else f"{slug}_{old_name}"
                name_map[old_name] = new_name
        
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
        @self.app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_index(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard main page"""
            # Get real stats from database
            stats = self._get_usage_stats(7)
            
            return self.templates.TemplateResponse("dashboard/index.html", {
                "request": request,
                "page_title": "Dashboard",
                "username": username,
                "stats": stats
            })

        @self.app.get("/dashboard/users", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_users(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard users page"""
            users = self._get_all_users()
            return self.templates.TemplateResponse("dashboard/users.html", {
                "request": request,
                "page_title": "User Management",
                "username": username,
                "users": users
            })

        @self.app.get("/dashboard/agents", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_agents(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard agents page"""
            project_param = request.query_params.get("project_id")
            try:
                selected_project_id = int(project_param) if project_param else None
            except (TypeError, ValueError):
                selected_project_id = None
            
            projects = self._get_all_projects()
            configs = self._get_all_agent_configs(selected_project_id) if selected_project_id else []
            return self.templates.TemplateResponse("dashboard/agents.html", {
                "request": request,
                "page_title": "Agent Management",
                "username": username,
                "configs": configs,
                "projects": projects,
                "selected_project_id": selected_project_id
            })

        @self.app.get("/dashboard/agents/visual", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_agents_visual(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard visual agent builder page"""
            project_param = request.query_params.get("project_id")
            try:
                selected_project_id = int(project_param) if project_param else None
            except (TypeError, ValueError):
                selected_project_id = None

            projects = self._get_all_projects()
            configs = self._get_all_agent_configs(selected_project_id) if selected_project_id else []
            return self.templates.TemplateResponse("dashboard/agents_visual.html", {
                "request": request,
                "page_title": "Agent Visual Builder",
                "username": username,
                "configs": configs,
                "projects": projects,
                "selected_project_id": selected_project_id,
            })

        @self.app.get("/dashboard/templates", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_templates(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard templates gallery page"""
            return self.templates.TemplateResponse("dashboard/templates.html", {
                "request": request,
                "page_title": "Template Library",
                "username": username,
            })

        @self.app.get("/dashboard/migrations", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_migrations(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard migrations page"""
            migrations = self._get_schema_migrations()
            return self.templates.TemplateResponse("dashboard/migrations.html", {
                "request": request,
                "page_title": "Database Migrations",
                "username": username,
                "migrations": migrations
            })

        @self.app.get("/dashboard/usage", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_usage(request: Request, days: int = 30, view: str = "analytics", username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard usage page"""
            stats = self._get_usage_stats(days)
            logs = self._get_token_usage_logs(24, limit=1000) if view == "logs" else {"logs": []}
            return self.templates.TemplateResponse("dashboard/usage.html", {
                "request": request,
                "page_title": "Usage Analytics",
                "username": username,
                "stats": stats,
                "logs": logs.get("logs", []) if isinstance(logs, dict) else [],
                "days": days,
                "view": view
            })

        @self.app.get("/dashboard/rate-limits", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_rate_limits(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard rate limits and budgets page"""
            return self.templates.TemplateResponse("dashboard/rate_limits.html", {
                "request": request,
                "page_title": "Rate Limits & Budgets",
                "username": username,
            })

        @self.app.get("/dashboard/traces", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_traces(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard traces page - OpenTelemetry distributed tracing viewer"""
            return self.templates.TemplateResponse("dashboard/traces.html", {
                "request": request,
                "page_title": "Traces",
                "username": username,
            })

        @self.app.get("/dashboard/docs", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_docs(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard docs page"""
            # Extract hostname from request
            host = request.headers.get("host", "localhost").split(":")[0]
            return self.templates.TemplateResponse("dashboard/docs.html", {
                "request": request,
                "page_title": "Documentation",
                "username": username,
                "adk_host": host,
                "adk_port": 8000
            })

        @self.app.get("/dashboard/audit-logs", response_class=HTMLResponse, tags=["Dashboard - Pages"])
        async def dashboard_audit_logs(request: Request, username: str = Depends(self._get_auth_user_dependency)):
            """Dashboard audit log viewer (EU AI Act compliance)."""
            return self.templates.TemplateResponse("dashboard/audit_logs.html", {
                "request": request,
                "page_title": "Audit Logs",
                "username": username,
            })

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
            hardcoded: bool = Form(False)
        ):
            """Create a new agent configuration."""
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
                "hardcoded": hardcoded
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
            hardcoded: bool = Form(False)
        ):
            """Update an agent configuration."""
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
                "hardcoded": hardcoded
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

