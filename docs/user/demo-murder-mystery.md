---
title: "Demo: Murder at the Villa"
summary: A murder-mystery game built from MATE agents, as a worked example.
audience: user
order: 200
covers:
  - shared/utils/tools/mystery_game.py
  - static/mystery-demo.html
  - static/villa-demo.html
  - templates/agent_templates/murder-mystery.json
  - templates/agent_templates/murder-mystery-en.json
  - templates/agent_templates/mystery-generator.json
---

# Demo: Murder Mystery — "Ubistvo u vili" / "Murder at the Villa"

An interactive detective game that showcases MATE's distinctive features in a single demo.
Available in two editions: Serbian (`murder-mystery`) and English (`murder-mystery-en`) —
same story, characters and mechanics; import either or both. A third template,
`mystery-generator`, writes a new case for each player: see
[The generator edition](#the-generator-edition).
The player is a detective interrogating four suspects — each suspect is a **separate agent**
with its own persona, its own LLM model, and its own guardrails that prevent it from
confessing. Trying to jailbreak a suspect into revealing the culprit is part of the gameplay.

## What it demonstrates

| Feature | How the game uses it |
|---|---|
| Multi-agent tree | Root game master (inspector) + 4 suspect sub-agents; ADK transfer routes interrogations |
| Memory blocks | Case dossier, the confidential solution, and three evidence blocks — the inspector reads them via `memory_blocks` tools; suspects have no tools and cannot see them |
| Per-agent guardrails | Every suspect: `prompt_injection` (input, block) + `content_policy` (output, block) on first-person confessions — patterns cover **both Serbian and English** (suspects answer in the player's language). The culprit additionally gets a `redact` policy — a half-successful jailbreak visibly prints `[REDACTED]`. All triggers land in `guardrail_logs` |
| Multi-LLM | The characters run on different models through OpenRouter: the inspector on Gemini 3 Flash, the butler on DeepSeek V3.1, the other three suspects on Gemini 3.1 Flash Lite |
| Rich cards | Suspect dossiers, evidence and the final verdict render as `[[CARD]]` cards with action buttons in both the dashboard and the widget |
| Widget embedding | The whole game is playable on any external page via a widget API key (`static/villa-demo.html`) |
| Token tracking | **Usage Analytics** shows the "cost of the investigation" per suspect/model |
| Config versioning | Editing the case (see below) snapshots agent versions; cases can be tagged and rolled back |

## Setup

1. **Import the template**: **Template Library** → *Ubistvo u vili* (`murder-mystery`)
   or *Murder at the Villa* (`murder-mystery-en`).
   This creates the project, the 5 agents (with guardrails) and the 5 memory blocks.
   The agents' `villa_` prefix becomes the project's slug, so each edition is a
   separate project with its own root agent.
2. **Play in the dashboard**: open the Work Room and select the imported root agent
   (`<project-slug>_gm_root`). Say hello — the inspector introduces the case with suspect cards.
3. **Play via the widget** (optional):
   - On the Agents page, choose the imported root agent in the agent filter, click the
     code icon (**Widget Keys**) and create a key (one key per edition — SR and EN are
     separate root agents).
   - Open `http://localhost:8000/static/villa-demo.html?key=wk_...` (Serbian page) or
     `...villa-demo.html?key=wk_...&lang=en` (English page, use the EN edition's key).
   - The same page works hosted on any external site — only the widget `<script>` tag talks
     to the MATE server (set an origin allowlist on the key for production).

Requires `OPENROUTER_API_KEY` in `.env`, or an OpenRouter key on the workspace the
game is imported into ([Models and keys](workspaces.md#models-and-keys)): all five
models go through OpenRouter. To use other providers, change each agent's **Model
Name**.

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

## The generator edition

*Detektivske misterije (generator)* (`mystery-generator`) plays the same way, but each
player can ask for a brand-new case. It imports the project *Detektivske misterije*
with the inspector (`<project-slug>_gm_root`) and four generic suspect agents
(`<project-slug>_suspect_1` to `_4`), and no memory blocks.

- **The case lives in the player's session**, not in the agents or memory blocks. A
  session starts with a built-in case (the villa "Sova", written in Serbian).
  Generating a new one replaces it for that session only; other players, and the
  agents themselves, are unaffected. A new conversation starts again from the
  built-in case.
- **A new case**: tell the inspector "Generate a new mystery", optionally with a theme.
  A model writes the plot, victim, suspects and evidence, which takes 20–40 seconds.
  The suspects are shuffled after writing, so the killer is not always the last card.
- **What each agent can see**: the inspector's tools give the case brief, the evidence
  and an accusation check (`get_case_brief`, `get_case_evidence`, `check_accusation`,
  `generate_new_case`). The solution reaches the inspector only once the player
  accuses someone, and a wrong accusation returns only why that suspect is innocent.
  Each suspect agent's one tool (`get_my_character`) returns only its own character
  sheet, picked by the number at the end of its name.
- **The model that writes cases** is set in the inspector's tool configuration,
  `{"mystery_gm": {"model": "..."}}`; the template sets
  `openrouter/deepseek/deepseek-v4-flash`. Without it, `MYSTERY_GEN_MODEL`, then
  `WIZARD_ANALYSIS_MODEL`, then `openrouter/deepseek/deepseek-v4-flash` is used. The
  call uses the workspace's key for that provider, like the agents' own calls.
- **Guardrails**: each suspect has the same input and output guardrails as in the villa
  editions, but no case-specific `redact` policy.
- **The demo page**: `http://localhost:8000/static/mystery-demo.html?key=wk_...`, with
  `&lang=en` for the English page copy.

## Files

- `templates/agent_templates/murder-mystery.json` — the whole game, Serbian edition (agents, guardrails, blocks)
- `templates/agent_templates/murder-mystery-en.json` — English edition (same structure)
- `static/villa-demo.html` — villa-themed external page embedding the widget; `?lang=en` switches the page copy
- `templates/agent_templates/mystery-generator.json` — the generator edition
- `shared/utils/tools/mystery_game.py` — the generator edition's tools and its built-in case
- `static/mystery-demo.html` — the generator edition's external page
