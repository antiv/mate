---
title: Getting started
summary: Sign in, find your way around the three spaces, create a first agent and talk to it.
audience: user
order: 10
covers:
  - templates/base.html
  - templates/login.html
  - templates/dashboard/workroom.html
  - templates/dashboard/modals/project_modal.html
---

# Getting started

MATE runs AI agents that you configure in a browser instead of in code. This page
takes you from the login screen to a first conversation with an agent you made.

## Sign in

Open the dashboard address your administrator gave you. On a fresh local
installation that is `http://localhost:8000`, and the built-in account is `admin`
with the password `mate` until someone changes it.

If single sign-on is enabled, the login page also offers Google or GitHub. A person
who signs in that way for the first time has no access to any agent until an
administrator gives them a role on the **Users** page, or until they accept an
invitation to a workspace. Signing in from an invitation's link comes back to it.

## The three spaces

The switch at the top of every page moves between three spaces:

| Space | What it is for |
|---|---|
| **Work Room** | Talking to agents. Everyone who can sign in has this. |
| **Studio** | Building: agents, the visual builder, triggers, integrations, templates, evals, traces. |
| **Control Room** | Running the installation, in three groups: **Observability** (usage, audit and guardrail logs, sessions); **Workspace** ([approvals](approvals.md), members, [models and keys](workspaces.md#models-and-keys), rate limits, alerts); and, for platform administrators, **Platform** ([workspaces](workspaces.md), users, [model groups](model-groups.md), server-wide [settings](settings.md), migrations, and the [wizard and shop](wizard.md) pages when in use). This documentation is at the bottom. |

**Search** in the header, or Ctrl+K (⌘K on a Mac), opens a box that jumps to an agent,
one of your recent conversations or a page you may open. Type part of the name, choose
with the arrow keys and press Enter; Ctrl+Enter (⌘Enter) opens it in a new tab, and
Escape closes the box. Accents don't matter: `kljucevi` finds "Ključevi". An
administrator goes to an agent's page, anyone else to a chat with it. Inside a code
editor Ctrl+K stays the editor's.

Studio and Control Room are for administrators. Someone signed in without the admin
role sees only the Work Room and their own usage.

## Create a project

Agents live in projects. A project is a group of agents that belong together, with
their own memory blocks and triggers.

1. Go to **Studio → Agents**.
2. Pick a project from the project list (**Select project...**). If the list is empty, click
   **Manage Projects**, enter a name and click **Create Project**.

## Create your first agent

1. On the Agents page, with a project selected, click **Add Agent**. The new agent's
   page opens.
2. Fill in the required fields:
   - **Name**: a short identifier, for example `helper`.
   - **Type**: **LLM**.
   - **Model**: leave empty to use the installation's default model, or enter
     one such as `gemini-2.5-flash` or `openai/gpt-4o`.
   - **Description**: one sentence about what the agent is for.
   - **Instruction**, in the centre: what the agent should do and how it should behave.
3. Under **Allowed roles**, type `user` and press Enter. An agent answers only people
   who hold one of the roles listed here, and a new member starts with the `user`
   role. With none, the agent answers only the workspace's owners and admins.
4. Click **Create agent**.

The [Agents guide](agents.md) explains every field.

## Talk to it

On the Agents page, choose the root agent in the agent filter (**All Agents**) and
click the speech-bubbles icon (**Open agent chat**) next to it, or switch to the **Work Room** and
select the agent. Type a message and press Enter.

In the Work Room you can also:

- attach an image, a PDF or a text file to a message with **+**
- start over with **New Conversation**, or return to an earlier one under
  **Recent Sessions** in the sidebar
- open the **Coding panel**, where code the agent writes can be copied, downloaded
  or run
- open the **Browser panel** to watch and steer a browser the agent is driving, for
  agents that have the browser tool

## Where to go next

- [Agents](agents.md): give the agent tools, memory, guardrails and sub-agents.
- [Triggers](triggers.md): run an agent on a schedule or from another system.
- Use the search box above to find anything else; press `/` to jump to it.
- Or click **?** in the bottom-right corner of any page and ask; see
  [Help in the dashboard](help.md).
