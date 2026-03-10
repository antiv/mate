import json
from shared.utils.database_client import DatabaseClient
from shared.utils.models import AgentConfig, Project
from shared.utils.template_service import TemplateService

db_client = DatabaseClient()
session = db_client.get_session()
template_service = TemplateService(project_root="/Users/ivanantonijevic/development/bt/adk/mate")

project_id = 8
project = session.query(Project).filter(Project.id == project_id).first()

print(f"Project: {project.name}")
print(f"Template ID: {project.template_id}")

template = template_service.get_template(project.template_id)
template_agents = template.get("agents", [])

slug = template_service.slugify_project_name(project.name)
tpl_prefix = project.template_prefix or template.get("template_meta", {}).get("agent_prefix") or "tpl"
replace_with = f"{slug}_" if tpl_prefix.endswith("_") else slug

print(f"Prefix mapping: replace {tpl_prefix} with {replace_with}")

name_map = {}
for a in template_agents:
    old_name = a.get("name", "")
    if old_name:
        new_name = old_name.replace(tpl_prefix, replace_with, 1) if tpl_prefix in old_name else f"{slug}_{old_name}"
        name_map[old_name] = new_name

print("Name Map:", json.dumps(name_map, indent=2))

def sub_names(text):
    if isinstance(text, str):
        for old, new in name_map.items():
            text = text.replace(old, new)
    return text

db_agents = session.query(AgentConfig).filter(AgentConfig.project_id == project_id).all()
print("DB Agents:", [a.name for a in db_agents])

for tpl_agent in template_agents:
    tpl_name = tpl_agent.get("name", "")
    proj_name = name_map.get(tpl_name, tpl_name)
    db_agent = next((a for a in db_agents if a.name == proj_name), None)
    
    if db_agent:
        print(f"\nChecking changes for {proj_name}:")
        tpl_instr = tpl_agent.get("instruction") or ""
        db_instr = db_agent.instruction or ""
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
            print(f"Instruction changed! Tpl len: {len(tpl_instr)}, DB len: {len(db_instr)}")

        tpl_mcp_raw = tpl_agent.get("mcp_servers_config")
        if isinstance(tpl_mcp_raw, str):
            try:
                tpl_mcp_raw = json.loads(sub_names(tpl_mcp_raw))
            except json.JSONDecodeError:
                tpl_mcp_raw = sub_names(tpl_mcp_raw)
        
        tpl_mcp = to_json_str(tpl_mcp_raw)

        db_mcp_raw = db_agent.mcp_servers_config
        if isinstance(db_mcp_raw, str) and db_mcp_raw:
            try:
                db_mcp_raw = json.loads(db_mcp_raw)
            except json.JSONDecodeError:
                pass
                
        db_mcp = to_json_str(db_mcp_raw)
        
        if tpl_mcp != db_mcp:
            print(f"MCP changed!")
            print(f"Tpl MCP:\n{tpl_mcp}")
            print(f"DB MCP:\n{db_mcp}")
        else:
            print("MCP match.")
            
session.close()
