---
title: "Model groups"
summary: An agent names a group, such as group/coding, and a platform administrator says which models are in it.
audience: user
order: 166
covers:
  - templates/dashboard/model_groups.html
  - static/js/model-groups-page.js
  - server/model_group_routes.py
  - static/js/model-choice.js
  - shared/utils/model_groups.py
  - shared/utils/group_model.py
  - shared/utils/langgraph/group_model.py
---

# Model groups

An agent may name a group instead of a model: `group/coding`. Which
models are in the group is decided on **Control Room → Platform → Model groups**, so
changing a model means changing one list instead of every agent. Only platform
administrators can open that page.

## Put an agent on a group

Choose **Group** under **Model**, on the agent's page or in the Visual Builder's side
panel, and pick one. The line under it says which model **answers now** in this
workspace, or why none does. A group without models can't be picked. **Fallback model**
is hidden, as the group's next models take its place, and comes back with **Model**.

The agent's configuration holds `group/<name>` in `model_name`, as its API, versions,
exports and templates do. In the widget's admin page, type it into **Model**.

An agent with the [Create Agent](tools-and-mcp.md) tool sees the groups with
`list_model_groups`, and may put the agents it makes or changes on one.

## Which model answers

- **The first model of the list that the agent's workspace has a key for**: its own, under
  [Models and keys](workspaces.md#models-and-keys), or the server's where the workspace
  may use it. A model on a local server needs no key.
- **The next one only when the provider is unavailable**, by the rules of the
  [fallback model](fallback-model.md#when-it-fires). If that one is unavailable too, the
  next, to the end of the list. The list takes the fallback model's place: an agent on
  a group doesn't use its **Fallback Model**.
- **It is looked up on every call.** A changed group or key applies to the next message,
  without reloading anything.

[Usage](usage-and-audit.md) and [traces](sessions-and-traces.md) name the model that
answered, with the group beside it, and cost is that model's. A call fails, and says why, when:

- the workspace has no key for any model of the group;
- there is no such group, or it has no models. It never goes to another model.

Saving an agent is refused when its group doesn't exist or has no models, and when it
also has a **Model Base URL** or **Model API Key**, which belong to one model. So are a
rollback, an import and a template that bring such an agent. A
[subtask](subagent-delegation.md) may be given a group as its model too. Image
generation, memory search and the eval judge keep their own settings.

## The groups

A new installation has six groups without models: `standard`, `fast`, `reasoning`,
`coding`, `vision` and `local`. Rename their labels, change them or delete them as you
like; a deleted one doesn't come back. A group without models can't be used.

## A group

| Field | What it is |
|---|---|
| **Name** | What agents name it by. Lowercase letters, digits and hyphens. It can't be changed later |
| **Label**, **Description** | How people see it |
| **Its models must** | **Call tools** and **Read images**. Every model in the group has to do what is ticked |
| **Own servers only** | Every model runs on an endpoint in this server's own network |
| **Models, in order** | At most ten, written as in an agent's Model Name. The arrows change the order |

**Save** checks the whole group and says what is wrong:

- **A name LiteLLM can't place** is refused, as is a name without a provider that isn't
  a Gemini model (`gpt-4o`; write `openai/gpt-4o`).
- **A model LiteLLM knows can't do it** is refused: one without images in a group
  that reads images.
- **A model LiteLLM says nothing about** (many on OpenRouter, anything new on Ollama)
  gets a checkbox on its row. Tick it if you know that the model can.

## Own servers only

A model is accepted when both hold:

- **Its provider's endpoint is yours to name:** `ollama`, `ollama_chat`
  (`OLLAMA_API_BASE`), `lm_studio`, `llamacpp`, `localai`, `llamafile` (their
  `..._BASE_URL`), `hosted_vllm` (`HOSTED_VLLM_API_BASE`), or `openai`, when
  `OPENAI_BASE_URL` or `OPENAI_API_BASE` is set. Without a variable, the local servers
  are at `localhost`.
- **Every address that endpoint resolves to** is private or the machine itself. A host
  that doesn't resolve is refused.

An Ollama model whose tag is `cloud` or ends in `-cloud` runs on Ollama's servers, and
is refused. Ollama can also give such a model another name, which MATE can't see: keep
those off the Ollama server of such a group.

Both are checked again on every call, on the address the call would go to. A workspace
whose own entry for the provider has an address elsewhere doesn't use that model. A
subtask of such an agent stays in its group, or in another one with Own servers only.

It is about the agent's own model. What else MATE does with a conversation goes to that
feature's model: its title in the Work Room, the summary of a long conversation where
`CONTEXT_COMPACTION_SUMMARIZER_MODEL` names a model, memory
search, a guardrail that asks a model, image tools, and any tool the agent has.

## Groups that agents use

**Agents** counts the agents of every workspace that name the group. Click the number to
see them. While there are any:

- **the last model** can't be removed;
- **Own servers only** can't be turned off;
- **Delete** lists them, and moves them to another group in the same step. Agents of a
  group with Own servers only can move to another such group and no other. Each agent
  gets a new version and an audit entry in its workspace.

## The audit log

`model_group.create`, `model_group.update` and `model_group.delete`, with the group and
its list before and after the change. A delete names the agents it moved and where.
