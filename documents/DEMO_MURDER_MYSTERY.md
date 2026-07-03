# Demo: Murder Mystery — "Ubistvo u vili" / "Murder at the Villa"

An interactive detective game that showcases MATE's distinctive features in a single demo.
Available in two editions: Serbian (`murder-mystery`) and English (`murder-mystery-en`) —
same story, characters and mechanics; import either or both.
The player is a detective interrogating four suspects — each suspect is a **separate agent**
with its own persona, its own LLM model, and its own guardrails that prevent it from
confessing. Trying to jailbreak a suspect into revealing the culprit is part of the gameplay.

## What it demonstrates

| Feature | How the game uses it |
|---|---|
| Multi-agent tree | Root game master (inspector) + 4 suspect sub-agents; ADK transfer routes interrogations |
| Memory blocks | Case dossier, the confidential solution, and three evidence blocks — the inspector reads them via `memory_blocks` tools; suspects have no tools and cannot see them |
| Per-agent guardrails | Every suspect: `prompt_injection` (input, block) + `content_policy` (output, block) on first-person confessions — patterns cover **both Serbian and English** (suspects answer in the player's language). The culprit additionally gets a `redact` policy — a half-successful jailbreak visibly prints `[REDACTED]`. All triggers land in `guardrail_logs` |
| Multi-LLM | Each character runs on a different provider via OpenRouter (Gemini Flash, DeepSeek, Claude Haiku, GPT-4o-mini, Gemini Pro) — personality differences are part of the experience |
| Rich cards | Suspect dossiers, evidence and the final verdict render as `[[CARD]]` cards with action buttons in both the dashboard and the widget |
| Widget embedding | The whole game is playable on any external page via a widget API key (`static/villa-demo.html`) |
| Token tracking | The usage dashboard shows the "cost of the investigation" per suspect/model |
| Config versioning | Editing the case (see below) snapshots agent versions; cases can be tagged and rolled back |

## Setup

1. **Import the template**: Dashboard → Template Gallery → *Ubistvo u vili* (`murder-mystery`)
   or *Murder at the Villa* (`murder-mystery-en`).
   This creates the project, the 5 agents (with guardrails) and the 5 memory blocks, and
   hot-reloads the ADK server. Each edition is a separate project with its own root agent.
2. **Play in the dashboard**: open the Workroom and select the imported root agent
   (`<project-slug>_gm_root`). Say hello — the inspector introduces the case with suspect cards.
3. **Play via the widget** (optional):
   - Dashboard → Widget Keys → create a key pointing to the imported root agent
     (one key per edition — SR and EN are separate root agents).
   - Open `http://localhost:8000/static/villa-demo.html?key=wk_...` (Serbian page) or
     `...villa-demo.html?key=wk_...&lang=en` (English page, use the EN edition's key).
   - The same page works hosted on any external site — only the widget `<script>` tag talks
     to the MATE server (set an origin allowlist on the key for production).

Requires `OPENROUTER_API_KEY` in `.env` (all five models go through OpenRouter). To use other
providers, just change each agent's `model_name` in the dashboard.

## How to play

- Interrogate suspects (cards have an "Ispitaj" button, or just ask the inspector).
- Ask the inspector for evidence: crime-scene search, toxicology report, desk search.
- Confront suspects with evidence and each other's statements — they crack and reveal
  their small secrets (false leads), but never the murder.
- When confident, tell the inspector: **"Optužujem <ime>"** / **"I accuse <name>"**.
  A correct accusation (ideally backed by motive/evidence) solves the case.

Worth showing during a demo: the guardrail logs page after a jailbreak attempt, the token
usage page after a round ("which suspect model is the most expensive"), and the agent tree.

## Changing / adding cases

The case is **data, not code**:

- Edit the `case_dossier`, `case_solution` and `evidence_*` memory blocks (Memory Blocks
  modal) and the suspects' personas/secrets in their agent instructions.
- Every instruction change is snapshotted in `agent_config_versions` — tag a version per case
  (e.g. `slucaj-1-vila-perunika`) and roll back to replay an old one.
- Future idea (v2): a `villa_scenarista` agent with `create_agent` + `memory_blocks` tools
  that generates a whole new case on request and rewrites the suspects itself.

## Files

- `templates/agent_templates/murder-mystery.json` — the whole game, Serbian edition (agents, guardrails, blocks)
- `templates/agent_templates/murder-mystery-en.json` — English edition (same structure)
- `static/villa-demo.html` — villa-themed external page embedding the widget; `?lang=en` switches the page copy
