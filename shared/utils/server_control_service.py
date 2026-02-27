"""
Server Control Service
Manages ADK server lifecycle (start, stop, restart, status)
"""

import os
import subprocess
import time
import httpx
from typing import Dict, Any, Optional


class ServerControlService:
    """Service for controlling ADK server."""
    
    def __init__(self, adk_host: str = None, adk_port: int = None, session_service_uri: str = None):
        """
        Initialize server control service.
        
        Args:
            adk_host: ADK server host (defaults to environment config)
            adk_port: ADK server port (defaults to environment config)
            session_service_uri: Session service URI (defaults to environment config)
        """
        # Import here to avoid circular imports
        from shared.utils.utils import get_adk_config
        
        adk_config = get_adk_config()
        self.adk_host = adk_host or adk_config["adk_host"]
        self.adk_port = adk_port or adk_config["adk_port"]
        self.session_service_uri = session_service_uri or adk_config["session_service_uri"]
        
        # Get project root (2 levels up from this file: shared/utils/server_control_service.py -> project_root)
        from pathlib import Path
        self.project_root = Path(__file__).parent.parent.parent
    
    def get_adk_status(self) -> Dict[str, Any]:
        """Check if ADK server is running."""
        try:
            with httpx.Client(timeout=5.0) as client:
                # Use the /list-apps endpoint to check if ADK server is running
                response = client.get(f"http://{self.adk_host}:{self.adk_port}/list-apps")
                
                if response.status_code == 200:
                    # Try to get some info about the response
                    try:
                        data = response.json()
                        app_count = len(data) if isinstance(data, list) else "unknown"
                        return {"status": "running", "message": f"ADK server responding ({app_count} apps)"}
                    except:
                        return {"status": "running", "message": "ADK server is responding"}
                else:
                    return {"status": "stopped", "message": f"ADK server responded with status {response.status_code}"}
                    
        except Exception as e:
            return {"status": "stopped", "message": f"ADK server not responding: {str(e)}"}
    
    def _initialize_agent_folders(self):
        """Create agent folders for all top-level agents (without parent agents) from template."""
        import shutil
        
        try:
            from shared.utils.database_client import get_database_client
            from shared.utils.models import AgentConfig
            
            db_client = get_database_client()
            if not db_client:
                print("⚠️  Cannot initialize agent folders: Database not available")
                return
            
            session = db_client.get_session()
            if not session:
                print("⚠️  Cannot initialize agent folders: Database session failed")
                return
            
            try:
                # Define paths
                template_path = self.project_root / "shared" / "template_agent"
                agents_dir = self.project_root / "agents"
                
                # Check if template exists
                if not template_path.exists():
                    print(f"⚠️  Template agent not found at {template_path}, skipping folder initialization")
                    return
                
                # Query all agents without parent agents (top-level agents) that are NOT hardcoded
                top_level_agents = session.query(AgentConfig).filter(
                    (AgentConfig.parent_agents == None) | (AgentConfig.parent_agents == '[]'),
                    AgentConfig.hardcoded == False
                ).all()
                
                if not top_level_agents:
                    print("ℹ️  No non-hardcoded top-level agents found in database")
                    return
                
                print(f"🔍 Found {len(top_level_agents)} non-hardcoded top-level agent(s) in database")
                
                # Create agents directory if it doesn't exist
                agents_dir.mkdir(parents=True, exist_ok=True)
                
                created_count = 0
                skipped_count = 0
                
                for agent in top_level_agents:
                    agent_name = agent.name
                    dest_path = agents_dir / agent_name
                    
                    # Check if folder already exists
                    if dest_path.exists():
                        print(f"   ✓ Agent folder '{agent_name}' already exists, skipping")
                        skipped_count += 1
                        continue
                    
                    # Copy template folder
                    try:
                        shutil.copytree(template_path, dest_path)
                        print(f"   ✅ Created agent folder '{agent_name}' from template")
                        created_count += 1
                    except Exception as e:
                        print(f"   ⚠️  Failed to create folder for '{agent_name}': {e}")
                
                if created_count > 0 or skipped_count > 0:
                    print(f"✅ Agent folders initialized: {created_count} created, {skipped_count} already existed")
                
            except Exception as e:
                print(f"⚠️  Error querying agents: {e}")
            finally:
                session.close()
                
        except Exception as e:
            print(f"⚠️  Agent folder initialization error: {e}")
    
    def start_adk_server(self) -> Dict[str, Any]:
        """Start ADK server."""
        try:
            if self.get_adk_status()["status"] == "running":
                return {"success": False, "message": "ADK server is already running"}
            
            # Initialize agent folders before starting server
            self._initialize_agent_folders()
            
            # Set PYTHONPATH to include the parent directory so agents can find shared modules
            env = os.environ.copy()
            env['PYTHONPATH'] = os.pathsep.join([
                os.path.join(self.project_root, "agents"),  # agents directory
                str(self.project_root)  # Project root (where shared/ is located)
            ])
            
            # Set PORT for adk_main.py to read from environment
            env['PORT'] = str(self.adk_port)
            
            # Run adk_main.py script instead of adk CLI
            adk_script_path = os.path.join(self.project_root, "adk_main.py")
            print(f"🚀 Starting ADK server from project root: {self.project_root}")
            print(f"🚀 ADK script path: {adk_script_path}")
            print(f"🚀 ADK host: {self.adk_host}, port: {self.adk_port}")
            print(f"🚀 Session DB URL: {self.session_service_uri}")
            
            # Start from project root so load_dotenv() in adk_main.py finds .env
            cmd = ["python", adk_script_path, "--host", self.adk_host, "--session-db-url", self.session_service_uri, "--a2a"]
            # Don't capture output so it appears in Docker logs
            # Set cwd to project_root so adk_main.py can find .env file
            adk_process = subprocess.Popen(cmd, env=env, cwd=str(self.project_root))
            
            # Check if process started successfully (not terminated immediately)
            time.sleep(1)
            if adk_process.poll() is not None:
                print(f"⚠️  ADK server process terminated immediately (exit code: {adk_process.returncode})")
                print(f"⚠️  Check logs above for error details")
                return {"success": False, "message": f"ADK server process terminated immediately (exit code: {adk_process.returncode})"}
            
            print(f"✅ ADK server process started (PID: {adk_process.pid})")
            
            # Give it more time to start up and check multiple times
            for attempt in range(10):  # Try for up to 10 seconds
                time.sleep(1)
                status = self.get_adk_status()
                if status["status"] == "running":
                    print(f"✅ ADK server is responding")
                    return {"success": True, "message": "ADK server started successfully"}
            
            # If we get here, server started but isn't responding yet
            print(f"⚠️  ADK server started but not responding yet")
            return {"success": True, "message": "ADK server started (may still be initializing)"}
                
        except Exception as e:
            print(f"⚠️  Error starting ADK server: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"success": False, "message": f"Error starting ADK server: {str(e)}"}
    
    def stop_adk_server(self) -> Dict[str, Any]:
        """Stop ADK server."""
        try:
            import psutil
            import signal
            
            # Find and terminate ADK server processes
            try:
                killed_count = 0
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        cmdline = proc.info.get('cmdline')
                        if cmdline:
                            cmdline_str = ' '.join(cmdline)
                            # Look for processes matching "adk_main.py" or legacy "adk web" in the command line
                            if 'adk_main.py' in cmdline_str or ('adk' in cmdline_str.lower() and 'web' in cmdline_str.lower()):
                                proc.send_signal(signal.SIGTERM)
                                killed_count += 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                
                if killed_count > 0:
                    # Give processes time to terminate
                    time.sleep(2)
                    
                    # Check if it's actually stopped
                    status = self.get_adk_status()
                    if status["status"] == "stopped":
                        return {"success": True, "message": f"ADK server stopped successfully ({killed_count} process(es) terminated)"}
                    else:
                        return {"success": False, "message": "ADK server may still be running"}
                else:
                    return {"success": False, "message": "No ADK server processes found to stop"}
                    
            except Exception as e:
                return {"success": False, "message": f"Error stopping ADK server: {str(e)}"}
                
        except Exception as e:
            return {"success": False, "message": f"Error stopping ADK server: {str(e)}"}
    
    def restart_adk_server(self) -> Dict[str, Any]:
        """Restart ADK server."""
        stop_result = self.stop_adk_server()
        if stop_result["success"] or "not running" in stop_result["message"]:
            time.sleep(1)  # Brief pause between stop and start
            return self.start_adk_server()
        else:
            return stop_result

