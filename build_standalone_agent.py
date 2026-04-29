#!/usr/bin/env python3
"""
MATE Standalone Agent Builder

Takes a MATE agent JSON export and packages everything needed to run
the agent as a standalone application.

Usage:
    python build_standalone_agent.py agent_export.json [options]

Options:
    --agent-name NAME    Override the root agent name (auto-detected by default)
    --output-dir DIR     Output directory (default: build/standalone)
    --build              Run PyInstaller after preparing the build directory
    --app-name NAME      Name for the output executable (default: MATEAgent)

Steps:
    1. Parse the JSON export file
    2. Detect the root agent (agent with no parent_agents)
    3. Create a fresh SQLite database with the agent configs
    4. Copy required source files and assets
    5. Generate a .env file for the standalone server
    6. Optionally invoke PyInstaller to create the binary
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Set


# Project root is where this script lives
PROJECT_ROOT = Path(__file__).parent.resolve()


def parse_agent_json(json_path: str) -> Dict[str, Any]:
    """Parse and validate a MATE agent JSON export file."""
    path = Path(json_path)
    if not path.exists():
        print(f"❌ File not found: {json_path}")
        sys.exit(1)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON: {e}")
        sys.exit(1)

    if "agents" not in data or not isinstance(data["agents"], list):
        print("❌ Invalid export format: missing 'agents' array")
        sys.exit(1)

    if not data["agents"]:
        print("❌ No agents found in export file")
        sys.exit(1)

    return data


def detect_root_agent(agents: List[Dict], override_name: Optional[str] = None) -> str:
    """Find the root agent (no parent_agents) or use the override."""
    if override_name:
        # Verify the agent exists in the export
        names = [a["name"] for a in agents]
        if override_name not in names:
            print(f"❌ Agent '{override_name}' not found in export. Available: {', '.join(names)}")
            sys.exit(1)
        return override_name

    # Auto-detect: find agent(s) with no parent_agents
    root_candidates = []
    for agent in agents:
        parents = agent.get("parent_agents", [])
        if not parents or parents == []:
            root_candidates.append(agent["name"])

    if not root_candidates:
        print("❌ No root agent found (all agents have parent_agents)")
        print("   Use --agent-name to specify the root agent manually")
        sys.exit(1)

    if len(root_candidates) > 1:
        print(f"⚠️  Multiple root agents found: {', '.join(root_candidates)}")
        print(f"   Using first: {root_candidates[0]}")
        print(f"   Use --agent-name to specify a different one")

    return root_candidates[0]


def scan_mcp_dependencies(agents: List[Dict]) -> List[Dict[str, str]]:
    """
    Scan all agent MCP server configs and collect the external commands they need.

    Returns a list of dicts with keys: 'command', 'server', 'agent', 'found', 'path'.
    """
    results = []
    seen: Set[str] = set()

    for agent in agents:
        mcp_config_raw = agent.get("mcp_servers_config", "")
        if not mcp_config_raw:
            continue
        try:
            if isinstance(mcp_config_raw, str):
                mcp_config = json.loads(mcp_config_raw)
            else:
                mcp_config = mcp_config_raw

            servers = mcp_config.get("mcpServers", {})
            for server_name, server_cfg in servers.items():
                command = server_cfg.get("command", "").strip()
                if not command:
                    continue
                key = f"{agent['name']}::{server_name}::{command}"
                if key in seen:
                    continue
                seen.add(key)
                resolved = shutil.which(command)
                results.append({
                    "command": command,
                    "server": server_name,
                    "agent": agent["name"],
                    "found": resolved is not None,
                    "path": resolved or "",
                })
        except (json.JSONDecodeError, AttributeError):
            continue

    return results


def print_mcp_dependency_report(deps: List[Dict[str, str]]):
    """Print a human-readable MCP dependency report."""
    if not deps:
        print("   ℹ️  No MCP stdio server commands found in any agent.")
        return

    found = [d for d in deps if d["found"]]
    missing = [d for d in deps if not d["found"]]

    print(f"   {'CMD':<12} {'SERVER':<20} {'AGENT':<25} STATUS")
    print(f"   {'-'*12} {'-'*20} {'-'*25} {'------'}")
    for dep in deps:
        status = f"✅ {dep['path']}" if dep["found"] else "❌ NOT FOUND"
        print(f"   {dep['command']:<12} {dep['server']:<20} {dep['agent']:<25} {status}")

    if missing:
        print()
        print("   ⚠️  WARNING: The following MCP commands were NOT found on this build machine.")
        print("   They must be installed on every machine that runs this binary:")
        hints = {
            "npx": "Install Node.js from https://nodejs.org/",
            "node": "Install Node.js from https://nodejs.org/",
            "uvx": "Install uv from https://github.com/astral-sh/uv",
            "uv": "Install uv from https://github.com/astral-sh/uv",
            "bun": "Install Bun from https://bun.sh/",
            "deno": "Install Deno from https://deno.land/",
        }
        unique_missing = {d["command"] for d in missing}
        for cmd in sorted(unique_missing):
            hint = hints.get(cmd, f"Ensure '{cmd}' is installed and in PATH")
            print(f"   • '{cmd}': {hint}")


def create_database(output_dir: Path, agents: List[Dict], memory_blocks: List[Dict] = None, triggers: List[Dict] = None) -> Path:
    """Create a fresh SQLite database and import agents.
    
    Uses direct SQLAlchemy table creation (not DatabaseClient) to avoid
    running migrations that contain demo seed data.
    """
    db_path = output_dir / "standalone_agent.db"

    # Remove existing database if present
    if db_path.exists():
        db_path.unlink()

    # Use SQLAlchemy directly to create a clean database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import models (Base contains all table definitions)
    sys.path.insert(0, str(PROJECT_ROOT))
    from shared.utils.models import Base, AgentConfig, Project

    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    # Create all tables from ORM models (no migrations, no seed data)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Create default project
        default_project = Project(
            name="Default Project",
            description="Standalone agent project"
        )
        session.add(default_project)
        session.flush()
        project_id = default_project.id

        # Import agents
        imported = 0
        for agent_data in agents:
            parent_agents = agent_data.get("parent_agents", [])
            parent_agents_json = json.dumps(parent_agents) if parent_agents else None

            agent = AgentConfig(
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
                project_id=project_id,
                disabled=agent_data.get("disabled", False),
                hardcoded=agent_data.get("hardcoded", False),
            )
            session.add(agent)
            imported += 1

        # Import memory blocks if present
        memory_imported = 0
        if memory_blocks:
            from shared.utils.models import MemoryBlock
            for block_data in memory_blocks:
                block = MemoryBlock(
                    project_id=project_id,
                    label=block_data.get("label", ""),
                    value=block_data.get("value", ""),
                    description=block_data.get("description"),
                )
                md = block_data.get("metadata")
                if isinstance(md, dict):
                    block.set_metadata(md)
                session.add(block)
                memory_imported += 1

        # Import triggers if present
        triggers_imported = 0
        if triggers:
            from shared.utils.models import AgentTrigger
            for tdata in triggers:
                trigger = AgentTrigger(
                    name=tdata.get("name", ""),
                    description=tdata.get("description"),
                    trigger_type=tdata.get("trigger_type", "cron"),
                    agent_name=tdata.get("agent_name", ""),
                    project_id=project_id,
                    prompt=tdata.get("prompt", ""),
                    cron_expression=tdata.get("cron_expression"),
                    webhook_path=None,   # webhook paths are regenerated per deployment
                    fire_key_hash=None,
                    output_type=tdata.get("output_type", "memory_block"),
                    is_enabled=tdata.get("is_enabled", True),
                )
                trigger.set_output_config(tdata.get("output_config") or {})
                session.add(trigger)
                triggers_imported += 1

        session.commit()
        print(f"   ✅ Imported {imported} agent(s) into database")
        if memory_imported:
            print(f"   ✅ Imported {memory_imported} memory block(s)")
        if triggers_imported:
            print(f"   ✅ Imported {triggers_imported} trigger(s)")

    except Exception as e:
        session.rollback()
        print(f"   ❌ Error importing agents: {e}")
        raise
    finally:
        session.close()
        engine.dispose()

    return db_path


def copy_build_assets(output_dir: Path):
    """Copy necessary source files and assets to the build directory."""
    copies = [
        # Core Python modules
        ("shared", "shared"),
        ("agents", "agents"),

        # Server entry point
        ("standalone_server.py", "standalone_server.py"),

        # Templates
        ("templates/standalone", "templates/standalone"),

        # Static assets (only what the chat UI needs)
        ("static/css/widget", "static/css/widget"),
        ("static/js/standalone", "static/js/standalone"),
    ]

    for src_rel, dst_rel in copies:
        src = PROJECT_ROOT / src_rel
        dst = output_dir / dst_rel

        if not src.exists():
            print(f"   ⚠️  Skipping (not found): {src_rel}")
            continue

        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(
                src, dst,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".DS_Store",
                    "*.db", "*.db-wal", "*.db-shm",
                    ".env", ".git", ".venv",
                    "test_*", "*.test.*",
                ),
            )
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        print(f"   ✅ Copied: {src_rel}")

    # Also copy the favicon if it exists
    favicon_src = PROJECT_ROOT / "static" / "favicon.svg"
    if favicon_src.exists():
        favicon_dst = output_dir / "static" / "favicon.svg"
        favicon_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(favicon_src, favicon_dst)

    # Create tiktoken runtime hook and pre-download BPE data
    create_tiktoken_hook(output_dir)


def generate_env_file(output_dir: Path, root_agent_name: str):
    """Generate a .env file for the standalone server."""
    env_content = f"""\
# MATE Standalone Agent Configuration
# Generated by build_standalone_agent.py

# Agent to serve (do not change)
ROOT_AGENT_NAME={root_agent_name}

# Database (do not change)
DB_TYPE=sqlite
DB_PATH=standalone_agent.db

# --- API Keys (REQUIRED — fill in before running) ---
# Uncomment and set the keys needed by your agent's model:

# For Gemini models (gemini-2.5-flash, etc.)
# GOOGLE_API_KEY=your-gemini-api-key-here

# For OpenRouter models (openrouter/deepseek/..., etc.)
# OPENROUTER_API_KEY=your-openrouter-api-key-here

# For OpenAI models (openai/gpt-4o, etc.)
# OPENAI_API_KEY=your-openai-api-key-here

# --- Server (optional) ---
# STANDALONE_PORT=8080
# STANDALONE_HOST=127.0.0.1
"""
    env_path = output_dir / ".env"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env_content)

    print(f"   ✅ Generated .env file")


def create_tiktoken_hook(output_dir: Path):
    """Create a PyInstaller runtime hook for tiktoken and pre-download BPE data.
    
    tiktoken uses entry_points to discover encodings (cl100k_base, etc.).
    PyInstaller does NOT preserve entry_points, so we must manually register
    the encodings at startup via a runtime hook.
    """
    # Pre-download tiktoken BPE data files into a cache directory
    tiktoken_cache = output_dir / "tiktoken_cache"
    tiktoken_cache.mkdir(exist_ok=True)

    try:
        import tiktoken
        import tempfile

        # Point tiktoken at our build cache dir so downloads go there directly
        original_cache = os.environ.get("TIKTOKEN_CACHE_DIR")
        os.environ["TIKTOKEN_CACHE_DIR"] = str(tiktoken_cache)

        # Force-download common encodings used by litellm / OpenAI
        for encoding_name in ["cl100k_base", "o200k_base", "p50k_base", "r50k_base"]:
            try:
                enc = tiktoken.get_encoding(encoding_name)
                print(f"   ✅ Pre-cached tiktoken encoding: {encoding_name}")
            except Exception as e:
                print(f"   ⚠️  Could not pre-cache tiktoken encoding: {encoding_name} ({e})")

        # Restore original env
        if original_cache is not None:
            os.environ["TIKTOKEN_CACHE_DIR"] = original_cache
        else:
            os.environ.pop("TIKTOKEN_CACHE_DIR", None)

        # Also copy any existing cache files from default location
        default_cache = Path(tempfile.gettempdir()) / "data-gym-cache"
        if default_cache.exists():
            for f in default_cache.iterdir():
                if f.is_file() and not (tiktoken_cache / f.name).exists():
                    shutil.copy2(f, tiktoken_cache / f.name)

        cached_count = len([f for f in tiktoken_cache.iterdir() if f.is_file()])
        print(f"   ✅ Tiktoken cache ready ({cached_count} files)")

    except ImportError:
        print("   ⚠️  tiktoken not installed, skipping BPE cache")

    # Create runtime hook that registers tiktoken encodings manually
    hook_content = '''\
import os
import sys

# Set tiktoken cache directory to bundled data
if getattr(sys, 'frozen', False):
    # Running as PyInstaller bundle
    bundle_dir = sys._MEIPASS
    cache_dir = os.path.join(bundle_dir, 'tiktoken_cache')
    if os.path.isdir(cache_dir):
        os.environ['TIKTOKEN_CACHE_DIR'] = cache_dir

# Manually register tiktoken encodings (entry_points don't work in PyInstaller)
try:
    import tiktoken_ext.openai_public  # noqa: F401
except ImportError:
    pass
try:
    import tiktoken_ext  # noqa: F401
except ImportError:
    pass
'''
    hook_path = output_dir / "runtime_hook_tiktoken.py"
    with open(hook_path, "w", encoding="utf-8") as f:
        f.write(hook_content)
    print("   ✅ Created tiktoken runtime hook")


def generate_spec_file(output_dir: Path, app_name: str):
    """Generate a PyInstaller .spec file for the build."""
    spec_content = f"""\
# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MATE Standalone Agent
# Generated by build_standalone_agent.py

import sys
from pathlib import Path

block_cipher = None

# Base directory is where this spec file lives
BASE_DIR = Path(SPECPATH)

# Dynamically collect all submodules for packages with lazy/dynamic imports
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

litellm_imports = collect_submodules('litellm')
litellm_datas = collect_data_files('litellm')

tiktoken_imports = collect_submodules('tiktoken')
tiktoken_datas = collect_data_files('tiktoken')
tiktoken_datas += collect_data_files('tiktoken_ext')

google_adk_imports = collect_submodules('google.adk')
google_adk_datas = collect_data_files('google.adk')

google_genai_imports = collect_submodules('google.genai')
google_genai_datas = collect_data_files('google.genai')

a = Analysis(
    [str(BASE_DIR / 'standalone_server.py')],
    pathex=[str(BASE_DIR)],
    binaries=[],
    datas=[
        (str(BASE_DIR / 'shared'), 'shared'),
        (str(BASE_DIR / 'agents'), 'agents'),
        (str(BASE_DIR / 'templates'), 'templates'),
        (str(BASE_DIR / 'static'), 'static'),
        (str(BASE_DIR / 'standalone_agent.db'), '.'),
        (str(BASE_DIR / '.env'), '.'),
        (str(BASE_DIR / 'tiktoken_cache'), 'tiktoken_cache'),
    ] + litellm_datas + tiktoken_datas + google_adk_datas + google_genai_datas,
    hiddenimports=[
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'starlette',
        'pydantic',
        'dotenv',
        'sqlalchemy',
        'sqlalchemy.dialects.sqlite',
        'aiosqlite',
        'greenlet',
        'jinja2',
        'httpx',
        'anyio',
        'shared',
        'shared.utils',
        'shared.utils.database_client',
        'shared.utils.models',
        'shared.utils.agent_manager',
        'shared.utils.logging_config',
        'shared.utils.utils',
    ] + litellm_imports + tiktoken_imports + google_adk_imports + google_genai_imports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[str(BASE_DIR / 'runtime_hook_tiktoken.py')],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
        'torch',
        'tensorflow',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if sys.platform == 'darwin':
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='{app_name}',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='{app_name}',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )

    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='{app_name}'
    )


"""
    spec_path = output_dir / f"{app_name}.spec"
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)

    print(f"   ✅ Generated PyInstaller spec: {app_name}.spec")
    return spec_path


def run_pyinstaller(spec_path: Path, output_dir: Path):
    """Run PyInstaller to create the binary."""
    print(f"\n📦 Running PyInstaller...")
    print(f"   Spec: {spec_path}")
    print(f"   This may take several minutes...\n")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(spec_path),
        "--distpath", str(output_dir / "dist"),
        "--workpath", str(output_dir / "pyinstaller_build"),
        "--noconfirm",
    ]

    try:
        result = subprocess.run(cmd, cwd=str(output_dir), check=True)
        print(f"\n✅ Build complete! Binary at: {output_dir / 'dist'}")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ PyInstaller failed (exit code {e.returncode})")
        print(f"   Check the output above for errors.")
        print(f"   Common fix: pip install pyinstaller")
        sys.exit(1)
    except FileNotFoundError:
        print(f"\n❌ PyInstaller not found. Install it:")
        print(f"   pip install pyinstaller")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Build a standalone MATE agent application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Prepare build directory from JSON export
  python build_standalone_agent.py my_agent.json

  # Specify root agent and custom output directory
  python build_standalone_agent.py my_agent.json --agent-name my_root --output-dir ./my_build

  # Full build with PyInstaller
  python build_standalone_agent.py my_agent.json --build --app-name MyAgent
        """,
    )

    parser.add_argument("json_file", help="Path to MATE agent JSON export file")
    parser.add_argument("--agent-name", help="Root agent name (auto-detected if not specified)")
    parser.add_argument("--output-dir", default="build/standalone", help="Output directory (default: build/standalone)")
    parser.add_argument("--build", action="store_true", help="Run PyInstaller after preparing files")
    parser.add_argument("--app-name", default="MATEAgent", help="Name for the output executable (default: MATEAgent)")

    args = parser.parse_args()

    print("=" * 60)
    print("  MATE Standalone Agent Builder")
    print("=" * 60)
    print()

    # Step 1: Parse JSON
    print("📄 Step 1: Parsing agent export...")
    export_data = parse_agent_json(args.json_file)
    agents = export_data["agents"]
    memory_blocks = export_data.get("memory_blocks", [])
    triggers = export_data.get("triggers", [])
    print(f"   Found {len(agents)} agent(s)")
    if memory_blocks:
        print(f"   Found {len(memory_blocks)} memory block(s)")
    if triggers:
        print(f"   Found {len(triggers)} trigger(s)")

    # Step 2: Detect root agent
    print("\n🔍 Step 2: Detecting root agent...")
    root_agent = detect_root_agent(agents, args.agent_name)
    print(f"   Root agent: {root_agent}")

    # Step 3: Prepare output directory
    output_dir = Path(args.output_dir).resolve()
    print(f"\n📁 Step 3: Preparing build directory...")
    print(f"   Output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 4: Copy assets
    print(f"\n📋 Step 4: Copying source files and assets...")
    copy_build_assets(output_dir)

    # Step 5: Create database
    print(f"\n🗄️  Step 5: Creating SQLite database...")
    db_path = create_database(output_dir, agents, memory_blocks, triggers)
    print(f"   Database: {db_path}")

    # Step 5b: Scan MCP dependencies
    print(f"\n🔌 Step 5b: Scanning MCP server dependencies...")
    mcp_deps = scan_mcp_dependencies(agents)
    print_mcp_dependency_report(mcp_deps)

    # Step 6: Generate .env
    print(f"\n⚙️  Step 6: Generating configuration...")
    generate_env_file(output_dir, root_agent)

    # Step 7: Generate spec file
    print(f"\n📐 Step 7: Generating PyInstaller spec...")
    spec_path = generate_spec_file(output_dir, args.app_name)

    # Step 8: Optionally build
    if args.build:
        run_pyinstaller(spec_path, output_dir)
    else:
        print(f"\n{'=' * 60}")
        print(f"  ✅ Build directory prepared successfully!")
        print(f"{'=' * 60}")
        print()
        print(f"To test the standalone server:")
        print(f"  cd {output_dir}")
        print(f"  # Edit .env to add your API keys")
        print(f"  python standalone_server.py")
        print()
        print(f"To create a standalone binary:")
        print(f"  # Install PyInstaller if needed: pip install pyinstaller")
        print(f"  python build_standalone_agent.py {args.json_file} --build")
        print()
        print(f"Or run PyInstaller manually:")
        print(f"  cd {output_dir}")
        print(f"  pyinstaller {args.app_name}.spec")


if __name__ == "__main__":
    main()
