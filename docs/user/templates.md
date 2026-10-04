---
title: Templates
summary: Start from a ready-made set of agents, keep it in sync, and turn your own agents into a template.
audience: user
order: 70
covers:
  - templates/dashboard/templates.html
  - static/js/template-gallery.js
  - templates/dashboard/modals/save_template_modal.html
  - templates/dashboard/modals/template_sync_modal.html
  - templates/dashboard/modals/import_agents_modal.html
  - static/js/modals/template-sync.js
  - shared/utils/template_service.py
  - shared/utils/dashboard/dashboard_server.py::*template*
---

# Templates

A template is a complete, working set of agents: a root agent, its sub-agents and
the memory blocks they rely on. Importing one gives you something to run and adapt
instead of an empty page.

## Import a template

1. Go to **Studio → Template Library**.
2. Find a template with the search box or the category filter (Support, Research,
   Code, Content, Demo).
3. Import it.

The import creates a new project with the template's agents and memory blocks.
Agent names are derived from the project name, so the same template can be imported
more than once without the copies colliding.

After the import, open the project on the **Agents** page. Two things usually need
your attention:

- **Models.** A template names the models its authors used. If your server has no
  key for that provider, change **Model Name** on each agent.
- **Roles.** Check **Allowed Roles** on the root agent so that the right people can
  use it.

## Keep an imported project up to date

When a template is improved later, a project that came from it can take the changes.
On the **Agents** page, select the project and click **Check for Updates**. The
dialog shows what would change:

- agents that are new in the template
- agents whose configuration changed
- memory blocks

Click **Apply Updates** to take them. Your own model choices and role settings on
existing agents are kept.

A memory block marked **Read-only** in your project keeps its value; so does one whose
**Character Limit** the template's new value exceeds. After the update a warning names
the blocks that were not updated and why. To take the template's value, unlock the
block (see [Memory blocks](memory-blocks.md)) and apply the updates again.

## Make your own template

1. On the **Agents** page, select the project and the root agent of the tree you
   want to reuse.
2. Click **Save as Template**.
3. Enter a **Template ID** (letters, numbers, hyphens and underscores; it becomes the
   file name), a display name, a description and a category.

The template then appears in the Template Library on this installation. It is saved
as a file in the server's `templates/agent_templates/` folder, so it can be copied
to another installation or contributed to the project.

## Templates or export?

Both move agents around; they answer different needs.

| | Template | Export / Import |
|---|---|---|
| Purpose | A reusable starting point | A copy of what you have |
| Result of importing | A **new** project, with renamed agents | Agents added to an existing installation, optionally overwriting agents with the same name |
| Carries | Agents and memory blocks | Agents, memory blocks and triggers |
| Updates later | Yes, with **Check for Updates** | No |

Use a template to give other people a starting point. Use **Export** and **Import**
on the Agents page to move your own project between a test and a production
installation.
