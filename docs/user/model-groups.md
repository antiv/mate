---
title: "Model groups"
summary: Lists of models under a name, kept by a platform administrator for the whole installation.
audience: user
order: 166
covers:
  - templates/dashboard/model_groups.html
  - static/js/model-groups-page.js
  - server/model_group_routes.py
  - shared/utils/model_groups.py
---

# Model groups

**Control Room → Platform → Model groups** holds lists of models under a name, such as
`group/coding`. Only platform administrators can open it.

Agents can't choose a group yet: the page, the rules below and the audit log are the
first part of model groups. The agent's side is planned in
[#424](https://github.com/antiv/mate/issues/424).

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
