import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
from google.adk.cli.adk_web_server import AdkWebServer
from google.adk.cli.utils.agent_loader import AgentLoader
from google.adk.cli.service_registry import get_service_registry
from google.adk.evaluation.local_eval_set_results_manager import LocalEvalSetResultsManager
from google.adk.evaluation.local_eval_sets_manager import LocalEvalSetsManager
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from shared.utils.db_memory_service import DBMemoryService
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from shared.utils.utils import fix_session_events_compaction
import uvicorn
import argparse
from shared.utils.db_credential_service import DBCredentialService
from shared.utils.artifacts import SupabaseArtifactService, S3ArtifactService, LocalFolderArtifactService

# Load environment variables from .env file
load_dotenv()

from shared.utils.logging_config import configure_logging
configure_logging()

parser = argparse.ArgumentParser()
parser.add_argument("--session-db-url", default="sqlite:///session_db.db")
parser.add_argument("--host", default="0.0.0.0")
parser.add_argument("--a2a", action="store_true", help="Enable A2A (Agent-to-Agent) client support")
args = parser.parse_args()
SESSION_DB_URL = args.session_db_url
HOST = args.host
ENABLE_A2A = args.a2a

class CompactionFixDatabaseSessionService(DatabaseSessionService):
    """Wrapper that fixes ADK bug #3633: EventCompaction dict deserialization."""

    async def get_session(self, **kwargs):
        session = await super().get_session(**kwargs)
        if session:
            fix_session_events_compaction(session)
        return session


# Service Registry Setup
def register_custom_services():
    """Register custom services with the ADK service registry."""
    registry = get_service_registry()
    
    # Register custom artifact services
    def local_folder_artifact_service_factory(uri: str, **kwargs):
        """Factory for creating LocalFolderArtifactService."""
        kwargs_copy = kwargs.copy()
        kwargs_copy.pop("agents_dir", None)
        base_path = kwargs_copy.pop("base_path", Path(__file__).parent / "artifacts")
        return LocalFolderArtifactService(base_path=str(base_path))
    
    def s3_artifact_service_factory(uri: str, **kwargs):
        """Factory for creating S3ArtifactService."""
        kwargs_copy = kwargs.copy()
        kwargs_copy.pop("agents_dir", None)
        return S3ArtifactService(
            bucket_name=kwargs_copy.pop("bucket_name", os.getenv("DISTRIBUTION_S3_BUCKET_NAME", "test-bucket")),
            endpoint_url=kwargs_copy.pop("endpoint_url", os.getenv("DISTRIBUTION_S3_ENDPOINT")),
            **kwargs_copy
        )
    
    def supabase_artifact_service_factory(uri: str, **kwargs):
        """Factory for creating SupabaseArtifactService."""
        kwargs_copy = kwargs.copy()
        kwargs_copy.pop("agents_dir", None)
        return SupabaseArtifactService(
            url=kwargs_copy.pop("url", os.getenv("SUPABASE_URL")),
            key=kwargs_copy.pop("key", os.getenv("SUPABASE_KEY")),
            bucket_name=kwargs_copy.pop("bucket_name", os.getenv("SUPABASE_BUCKET", "artifacts")),
            **kwargs_copy
        )
    
    # Register artifact services with file:// scheme for local_folder
    registry.register_artifact_service("file", local_folder_artifact_service_factory)
    registry.register_artifact_service("s3", s3_artifact_service_factory)
    registry.register_artifact_service("supabase", supabase_artifact_service_factory)
    
    # Register custom memory service
    def db_memory_service_factory(uri: str, **kwargs):
        """Factory for creating DBMemoryService."""
        kwargs_copy = kwargs.copy()
        kwargs_copy.pop("agents_dir", None)
        return DBMemoryService()
    
    registry.register_memory_service("sqlite", db_memory_service_factory)
    registry.register_memory_service("postgresql", db_memory_service_factory)
    registry.register_memory_service("mysql", db_memory_service_factory)
    
    # Register session services
    def database_session_service_factory(uri: str, **kwargs):
        """Factory for creating DatabaseSessionService."""
        kwargs_copy = kwargs.copy()
        kwargs_copy.pop("agents_dir", None)
        # Extract db_url from URI or use default
        db_url = kwargs_copy.pop("db_url", uri.replace("sqlite://", "sqlite+aiosqlite:///").replace("postgresql://", "postgresql+asyncpg://").replace("mysql://", "mysql+aiomysql://"))
        return CompactionFixDatabaseSessionService(db_url=db_url)
    
    def in_memory_session_service_factory(uri: str, **kwargs):
        """Factory for creating InMemorySessionService."""
        kwargs_copy = kwargs.copy()
        kwargs_copy.pop("agents_dir", None)
        return InMemorySessionService()
    
    registry.register_session_service("sqlite", database_session_service_factory)
    registry.register_session_service("postgresql", database_session_service_factory)
    registry.register_session_service("mysql", database_session_service_factory)
    registry.register_session_service("in_memory", in_memory_session_service_factory)
    
    # Note: Credential services are not supported by service registry yet
    # We'll initialize them directly
    
    print("✅ Custom services registered with ADK service registry")

# Register custom services
register_custom_services()

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))+ "/agents"
ALLOWED_ORIGINS = ["http://localhost", "http://localhost:8000", "*"]
SERVE_WEB_INTERFACE = True

# initialize Agent Loader
agent_loader = AgentLoader(AGENT_DIR)

# Global variable for ADK web server (set after it's created)
adk_web_server = None

# Initialize services using service registry
ARTIFACT_SERVICE_TYPE = os.getenv("ARTIFACT_SERVICE", "none").lower()
MEMORY_SERVICE_TYPE = os.getenv("MEMORY_SERVICE", "database").lower()
CREDENTIAL_SERVICE_TYPE = os.getenv("CREDENTIAL_SERVICE", "database").lower()

print(f"ARTIFACT_SERVICE environment variable: {os.getenv('ARTIFACT_SERVICE', 'not set')}")
print(f"MEMORY_SERVICE environment variable: {os.getenv('MEMORY_SERVICE', 'not set')}")
print(f"CREDENTIAL_SERVICE environment variable: {os.getenv('CREDENTIAL_SERVICE', 'not set')}")
print(f"Using artifact service type: {ARTIFACT_SERVICE_TYPE}")
print(f"Using memory service type: {MEMORY_SERVICE_TYPE}")
print(f"Using credential service type: {CREDENTIAL_SERVICE_TYPE}")

# Get service registry
registry = get_service_registry()

# Initialize session service using registry
if SESSION_DB_URL.startswith("sqlite://"):
    session_service = registry.create_session_service(SESSION_DB_URL)
elif SESSION_DB_URL.startswith("postgresql://"):
    session_service = registry.create_session_service(SESSION_DB_URL)
elif SESSION_DB_URL.startswith("mysql://"):
    session_service = registry.create_session_service(SESSION_DB_URL)
else:
    # Default to in-memory for unsupported URLs
    session_service = registry.create_session_service("in_memory://")

# Initialize artifact service using registry
if ARTIFACT_SERVICE_TYPE == "local_folder":
    ARTIFACT_DIR = Path(__file__).parent / "artifacts"
    ARTIFACT_DIR.mkdir(exist_ok=True, parents=True)
    print(f"Artifacts directory: {ARTIFACT_DIR.absolute()}")
    artifact_service = registry.create_artifact_service(
        f"file://{ARTIFACT_DIR.absolute()}", 
        base_path=str(ARTIFACT_DIR)
    )
elif ARTIFACT_SERVICE_TYPE == "s3":
    artifact_service = registry.create_artifact_service(
        "s3://test-bucket",
        bucket_name=os.getenv("DISTRIBUTION_S3_BUCKET_NAME", "test-bucket"),
        endpoint_url=os.getenv("DISTRIBUTION_S3_ENDPOINT")
    )
elif ARTIFACT_SERVICE_TYPE == "supabase":
    artifact_service = registry.create_artifact_service(
        "supabase://test-project",
        url=os.getenv("SUPABASE_URL"),
        key=os.getenv("SUPABASE_KEY"),
        bucket_name=os.getenv("SUPABASE_BUCKET", "artifacts")
    )
else:
    # Default to InMemoryArtifactService if "none" or not set
    artifact_service = InMemoryArtifactService()

# Initialize memory service using registry
if MEMORY_SERVICE_TYPE == "database":
    # Use the same database type as session service for consistency
    if SESSION_DB_URL.startswith("sqlite://"):
        memory_service = registry.create_memory_service("sqlite://memory")
    elif SESSION_DB_URL.startswith("postgresql://"):
        memory_service = registry.create_memory_service("postgresql://memory")
    elif SESSION_DB_URL.startswith("mysql://") or SESSION_DB_URL.startswith("mysql+pymysql://"):
        memory_service = registry.create_memory_service("mysql://memory")
    else:
        # Default to sqlite if database type is unclear
        memory_service = registry.create_memory_service("sqlite://memory")
else:
    # Default to InMemoryMemoryService for backwards compatibility
    memory_service = InMemoryMemoryService()

# Initialize credential service directly (not supported by registry yet)
if CREDENTIAL_SERVICE_TYPE == "database":
    # Use database-backed credential service
    credential_service = DBCredentialService()
elif CREDENTIAL_SERVICE_TYPE == "in_memory":
    credential_service = InMemoryCredentialService()
else:
    # Default to database for better persistence
    credential_service = DBCredentialService()

print(f"✅ Services initialized using service registry")
print(f"✅ Session service: {type(session_service).__name__}")
print(f"✅ Artifact service: {type(artifact_service).__name__}")
print(f"✅ Memory service: {type(memory_service).__name__}")
print(f"✅ Credential service: {type(credential_service).__name__}")
eval_sets_manager = LocalEvalSetsManager(agents_dir=AGENT_DIR)
eval_set_results_manager = LocalEvalSetResultsManager(agents_dir=AGENT_DIR)

try:
    print(f"🚀 Initializing ADK web server...")
    adk_web_server = AdkWebServer(
          agent_loader=agent_loader,
          session_service=session_service,
          artifact_service=artifact_service,
          memory_service=memory_service,
          credential_service=credential_service,
          eval_sets_manager=eval_sets_manager,
          eval_set_results_manager=eval_set_results_manager,
          agents_dir=AGENT_DIR,
      )
    print(f"✅ ADK web server instance created")

    extra_fast_api_args = {}
    if SERVE_WEB_INTERFACE:
        try:
            import google.adk.cli
            ANGULAR_DIST_PATH = Path(google.adk.cli.__file__).parent / "browser"
            
            # Only set web_assets_dir if the directory actually exists
            if ANGULAR_DIST_PATH.exists() and ANGULAR_DIST_PATH.is_dir():
                print(f"✅ Browser assets found at: {ANGULAR_DIST_PATH}")
                extra_fast_api_args.update(
                    web_assets_dir=ANGULAR_DIST_PATH,
                )
            else:
                print(f"⚠️  Browser assets NOT found at: {ANGULAR_DIST_PATH}")
                print(f"⚠️  API will work, but web UI will not be available")
        except Exception as e:
            print(f"⚠️  Error checking for browser assets: {e}")
            print(f"⚠️  API will work, but web UI will not be available")

    print(f"🚀 Creating FastAPI app...")
    print(f"Extra FastAPI args: {list(extra_fast_api_args.keys())}")
    app = adk_web_server.get_fast_api_app(
        allow_origins=ALLOWED_ORIGINS,
        **extra_fast_api_args,
      )
    print(f"✅ ADK web server initialized successfully")
    print(f"✅ FastAPI app created with {len(app.routes)} routes")
    
    # Add agent reload endpoints directly to ADK app
    @app.post("/api/reload-agent/{agent_name}")
    async def reload_agent_cache(agent_name: str):
        """Reload a specific agent by clearing all caches, including all parent agents."""
        from shared.utils.utils import reload_agent_cache as reload_agent_cache_util
        
        print(f"🔄 [ADK] Reload request for agent: {agent_name}")
        
        # Use the utility function with agent_loader and adk_web_server from this module
        result = reload_agent_cache_util(agent_name, agent_loader, adk_web_server)
        
        if result["success"]:
            print(f"✅ [ADK] {result['message']}")
        else:
            print(f"❌ [ADK] {result['message']}")
        
        return result
    
    @app.post("/api/reload-all-agents")
    async def reload_all_agents_cache():
        """Reload all agents by clearing all caches."""
        from shared.utils.agent_manager import get_agent_manager
        
        print(f"🔄 [ADK] Reload all agents request")
        
        try:
            # Clear AgentManager cache
            agent_manager = get_agent_manager()
            agent_manager.clear_initialized_agents()
            print(f"✅ [ADK] Cleared AgentManager cache")
            
            # Clear all from AgentLoader and runners
            import sys
            agent_modules = [name for name in sys.modules.keys() if name.startswith('agents.') and name.endswith('.agent')]
            
            for module_name in agent_modules:
                agent_name = module_name.replace('agents.', '').replace('.agent', '')
                agent_loader.remove_agent_from_cache(agent_name)
            
            print(f"✅ [ADK] Cleared AgentLoader cache for {len(agent_modules)} agents")
            
            # Mark all runners for cleanup
            all_runner_names = list(adk_web_server.runner_dict.keys())
            for runner_name in all_runner_names:
                adk_web_server.runners_to_clean.add(runner_name)
            
            print(f"✅ [ADK] Marked {len(all_runner_names)} runners for cleanup")
            print(f"✅ [ADK] All agents will reload with fresh config on next request")
            
            return {
                "success": True,
                "message": f"All agent caches cleared. Agents will reload on next request."
            }
        except Exception as e:
            print(f"❌ [ADK] Error reloading all agents: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "message": f"Error: {str(e)}"
            }
    
    print(f"✅ Added agent reload endpoints to ADK app")
    
    # Add builder endpoints (for ADK UI agent builder)
    from fastapi import UploadFile
    from fastapi.responses import FileResponse, PlainTextResponse
    import shutil
    
    @app.post("/builder/save", response_model_exclude_none=True)
    async def builder_build(
        files: list[UploadFile], tmp: Optional[bool] = False
    ) -> bool:
        """Save agent files from the builder UI"""
        base_path = Path.cwd() / AGENT_DIR.split('/')[-1]  # Get agents dir relative to cwd
        for file in files:
            if not file.filename:
                print("❌ Agent name is missing in the input files")
                return False
            agent_name, filename = file.filename.split("/")
            agent_dir = os.path.join(base_path, agent_name)
            try:
                # File name format: {app_name}/{agent_name}.yaml
                if tmp:
                    agent_dir = os.path.join(agent_dir, "tmp/" + agent_name)
                    os.makedirs(agent_dir, exist_ok=True)
                    file_path = os.path.join(agent_dir, filename)
                    with open(file_path, "wb") as buffer:
                        shutil.copyfileobj(file.file, buffer)
                else:
                    source_dir = os.path.join(agent_dir, "tmp/" + agent_name)
                    destination_dir = agent_dir
                    for item in os.listdir(source_dir):
                        source_item = os.path.join(source_dir, item)
                        destination_item = os.path.join(destination_dir, item)
                        if os.path.isdir(source_item):
                            shutil.copytree(source_item, destination_item, dirs_exist_ok=True)
                        elif os.path.isfile(source_item):
                            shutil.copy2(source_item, destination_item)
            except Exception as e:
                print(f"❌ Error in builder_build: {e}")
                return False
        return True

    @app.post("/builder/app/{app_name}/cancel", response_model_exclude_none=True)
    async def builder_cancel(app_name: str) -> bool:
        """Cancel builder changes for an agent"""
        base_path = Path.cwd() / AGENT_DIR.split('/')[-1]
        agent_dir = os.path.join(base_path, app_name)
        destination_dir = os.path.join(agent_dir, "tmp/" + app_name)
        source_dir = agent_dir
        source_items = set(os.listdir(source_dir))
        try:
            for item in os.listdir(destination_dir):
                if item in source_items:
                    continue
                # If it doesn't exist in the source, delete it from the destination
                item_path = os.path.join(destination_dir, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                elif os.path.isfile(item_path):
                    os.remove(item_path)

            for item in os.listdir(source_dir):
                source_item = os.path.join(source_dir, item)
                destination_item = os.path.join(destination_dir, item)
                if item == "tmp" and os.path.isdir(source_item):
                    continue
                if os.path.isdir(source_item):
                    shutil.copytree(source_item, destination_item, dirs_exist_ok=True)
                elif os.path.isfile(source_item):
                    shutil.copy2(source_item, destination_item)
        except Exception as e:
            print(f"❌ Error in builder_cancel: {e}")
            return False
        return True

    @app.get(
        "/builder/app/{app_name}",
        response_model_exclude_none=True,
        response_class=PlainTextResponse,
    )
    async def get_agent_builder(
        app_name: str,
        file_path: Optional[str] = None,
        tmp: Optional[bool] = False,
    ):
        """Get agent files for the builder UI"""
        base_path = Path.cwd() / AGENT_DIR.split('/')[-1]
        agent_dir = base_path / app_name
        if tmp:
            agent_dir = agent_dir / "tmp" / app_name
        if not file_path:
            file_name = "root_agent.yaml"
            root_file_path = agent_dir / file_name
            if not root_file_path.is_file():
                return ""
            else:
                return FileResponse(
                    path=root_file_path,
                    media_type="application/x-yaml",
                    filename=f"{app_name}.yaml",
                    headers={"Cache-Control": "no-store"},
                )
        else:
            agent_file_path = agent_dir / file_path
            if not agent_file_path.is_file():
                return ""
            else:
                return FileResponse(
                    path=agent_file_path,
                    media_type="application/x-yaml",
                    filename=file_path,
                    headers={"Cache-Control": "no-store"},
                )
    
    print(f"✅ Added builder endpoints to ADK app")
    
    # Add A2A (Agent-to-Agent) support if enabled
    if ENABLE_A2A:
        print(f"🚀 Setting up A2A (Agent-to-Agent) support...")
        try:
            from a2a.server.apps import A2AStarletteApplication
            from a2a.server.request_handlers import DefaultRequestHandler
            from a2a.server.tasks import InMemoryTaskStore
            from a2a.types import AgentCard
            from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
            from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
            import json
            
            # Enable A2A for all existing agents in the system
            a2a_task_store = InMemoryTaskStore()
            a2a_agents_count = 0
            
            def create_a2a_runner_loader(captured_app_name: str):
                """Factory function to create A2A runner with proper closure."""
                async def _get_a2a_runner_async():
                    return await adk_web_server.get_runner_async(captured_app_name)
                return _get_a2a_runner_async
            
            # Get all agents that are available in the ADK system
            try:
                # Get all agent names from both file-based and database-configured agents
                available_agents = []
                
                # 1. Get file-based agents from the agents directory
                base_path = Path(AGENT_DIR)
                if base_path.exists() and base_path.is_dir():
                    for p in base_path.iterdir():
                        if (
                            p.is_file()
                            or p.name.startswith((".", "__pycache__"))
                            or not p.is_dir()
                        ):
                            continue
                        available_agents.append(p.name)
                
                # 2. Get database-configured agents from AgentManager
                try:
                    from shared.utils.agent_manager import get_agent_manager
                    agent_manager = get_agent_manager()
                    
                    # Get all agents from database configuration
                    session = agent_manager.get_session()
                    if session:
                        from shared.utils.models import AgentConfig
                        db_agents = session.query(AgentConfig).filter(
                            AgentConfig.disabled.is_(False)
                        ).all()
                        
                        for agent_config in db_agents:
                            if agent_config.name not in available_agents:
                                available_agents.append(agent_config.name)
                        
                        session.close()
                        print(f"🔍 Found {len(db_agents)} database-configured agents")
                except Exception as e:
                    print(f"⚠️  Could not load database-configured agents: {e}")
                
                # 3. Get agents from ADK web server runners (comprehensive list)
                try:
                    runner_agents = list(adk_web_server.runner_dict.keys())
                    for runner_name in runner_agents:
                        if runner_name not in available_agents:
                            available_agents.append(runner_name)
                    print(f"🔍 Found {len(runner_agents)} agents from ADK runners")
                except Exception as e:
                    print(f"⚠️  Could not load agents from ADK runners: {e}")
                
                print(f"🔍 Total found {len(available_agents)} agents: {available_agents}")
                
                for app_name in available_agents:
                    print(f"🔧 Setting up A2A for agent: {app_name}")
                    
                    try:
                        # Create a default agent card for agents without agent.json
                        agent_card_data = {
                            "name": app_name,
                            "description": f"A2A-enabled {app_name} agent",
                            "version": "1.0.0",
                            "url": "http://localhost:8000",
                            "capabilities": {
                                "streaming": True,
                                "pushNotifications": False,
                                "stateTransitionHistory": True
                            },
                            "defaultInputModes": ["application/json"],
                            "defaultOutputModes": ["application/json"],
                            "skills": [
                                {
                                    "id": "general",
                                    "name": "general",
                                    "description": f"General capabilities of {app_name}",
                                    "inputMimeTypes": ["application/json"],
                                    "outputMimeTypes": ["application/json"],
                                    "tags": ["general", "a2a"]
                                }
                            ],
                            "metadata": {
                                "author": "MATE (Multi-Agent Tree Engine)",
                                "created": "2025-01-27",
                                "tags": ["a2a", "auto-generated"]
                            }
                        }
                        
                        # Check if agent has custom agent.json
                        agent_json_path = base_path / app_name / "agent.json"
                        if agent_json_path.exists():
                            try:
                                with agent_json_path.open("r", encoding="utf-8") as f:
                                    custom_data = json.load(f)
                                    agent_card_data.update(custom_data)
                                    print(f"📄 Using custom agent.json for {app_name}")
                            except Exception as e:
                                print(f"⚠️  Failed to load custom agent.json for {app_name}: {e}")
                        
                        agent_card = AgentCard(**agent_card_data)
                        
                        agent_executor = A2aAgentExecutor(
                            runner=create_a2a_runner_loader(app_name),
                        )
                        
                        request_handler = DefaultRequestHandler(
                            agent_executor=agent_executor, 
                            task_store=a2a_task_store
                        )
                        
                        a2a_app = A2AStarletteApplication(
                            agent_card=agent_card,
                            http_handler=request_handler,
                        )
                        
                        routes = a2a_app.routes(
                            rpc_url=f"/a2a/{app_name}",
                            agent_card_url=f"/a2a/{app_name}{AGENT_CARD_WELL_KNOWN_PATH}",
                        )
                        
                        for new_route in routes:
                            app.router.routes.append(new_route)
                        
                        a2a_agents_count += 1
                        print(f"✅ Successfully configured A2A for agent: {app_name}")
                        
                    except Exception as e:
                        print(f"❌ Failed to setup A2A for agent {app_name}: {e}")
                        # Continue with other agents even if one fails
                
                print(f"✅ A2A support enabled for {a2a_agents_count} agents")
                
            except Exception as e:
                print(f"❌ Error discovering agents: {e}")
                import traceback
                traceback.print_exc()
                
        except ImportError as e:
            import sys
            if sys.version_info < (3, 10):
                print(f"❌ A2A requires Python 3.10 or above. Current version: {sys.version_info.major}.{sys.version_info.minor}")
            else:
                print(f"❌ A2A dependencies not available: {e}")
            print(f"⚠️  A2A support disabled")
        except Exception as e:
            print(f"❌ Error setting up A2A support: {e}")
            import traceback
            traceback.print_exc()
    else:
        print(f"ℹ️  A2A support disabled (use --a2a flag to enable)")
        
except Exception as e:
    print(f"⚠️  Error initializing ADK web server: {e}")
    import traceback
    traceback.print_exc()
    raise

if __name__ == "__main__":
    try:
        port = int(os.getenv("PORT", 8000))
        print(f"🚀 Starting ADK server on {HOST}:{port}")
        print(f"🚀 Agents directory: {AGENT_DIR}")
        print(f"🚀 Session DB URL: {SESSION_DB_URL}")
        print(f"🚀 Artifact service: {ARTIFACT_SERVICE_TYPE}")
        print(f"🚀 Memory service: {MEMORY_SERVICE_TYPE}")
        print(f"🚀 Credential service: {CREDENTIAL_SERVICE_TYPE}")
        print(f"🚀 A2A support: {'Enabled' if ENABLE_A2A else 'Disabled'}")
        
        # Use uvicorn to run the FastAPI app with service registry
        uvicorn.run(app, host=HOST, port=port)
        
    except Exception as e:
        print(f"⚠️  Fatal error starting ADK server: {e}")
        import traceback
        traceback.print_exc()
        raise