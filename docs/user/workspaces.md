---
title: Workspaces
summary: Create workspaces for other teams or customers, give each a first owner, choose what it may use, and suspend or close it.
audience: user
order: 112
covers:
  - templates/dashboard/workspaces.html
  - server/platform_workspace_routes.py
  - server/invitation_routes.py::create_invitation
  - shared/utils/dashboard/dashboard_server.py::dashboard_workspaces
---

# Workspaces

A workspace holds its own projects, agents, members and logs. Every installation
has one, **Default**, which holds everything a 1.x installation had. Add more when
another team or customer should build and run agents on the same installation
without seeing yours.

The **Workspaces** page is for platform administrators: the built-in account, or
anyone with the `admin` role. Open it from the Control Room, under **Governance &
Access**. Owners of a workspace don't see it.

## The list

Every workspace is listed, closed ones too, with:

| Column | Meaning |
|---|---|
| **Name** | The name, and under it the slug. Default is marked. |
| **Status** | **open**, **suspended** or **closed**. |
| **Owners** | The members with the owner role, and addresses invited as owner who haven't accepted yet, marked *(invited)*. |
| **Members**, **Projects** | How many of each it has. |
| **Usage (30 days)** | Tokens and cost of its agents' calls over the last 30 days. Calls of agents with no configuration count as Default's. |
| **Platform keys** | Whether it may call models on the installation's provider keys. |
| **Features** | The platform features it may use (below). |

## Create a workspace

1. Click **New Workspace**.
2. Enter a **Name**. It must differ from every other workspace's, closed ones
   included, ignoring case.
3. Check the **Slug**, which is filled in from the name: lowercase letters, digits
   and hyphens, at most 40. It can't be changed later.
4. Choose the **First owner**: the id of an existing user, as on the **Users** page,
   or the email address of someone who has no user yet. A pending sign-up or a widget
   visitor can't be chosen by id.
   - An address that is no user's id is invited as the owner. The invitation is
     emailed when the server has SMTP set, and its link is shown above the list once,
     to copy. The workspace has no member until they open the link and sign in as
     that address (see [Invitations](users-and-roles.md#invitations)).
5. Set **May use platform keys** and the **Platform features**, and click **Save**.

Once they are a member, the owner can open the workspace from the workspace menu in the top bar, and
has its whole dashboard: they create its projects and agents, and add people on its
**Members** page (see [Users and roles](users-and-roles.md#members)). Their agent
roles there are those they have in the Default workspace, or `user`; an invited
owner's are `user`. You see the workspace menu too, since you may open
every workspace.

## What a workspace may use

**Platform features** are the agent features that use the server itself, so only a
platform administrator enables them, per workspace. A new workspace has none.

| Feature | Lets the workspace's agents use |
|---|---|
| **Code** | The code executor, and MCP servers started as a command. |
| **Server credentials** | Google Drive, Google Calendar, Supabase Storage and CV Tools on the server's credentials, and the shop's `vendor_email` and `partner_key`. |
| **Email** | Email from triggers and alert rules, through the server's SMTP. |

Default keeps all three, as in 1.x; its checkboxes can't be changed. Even with a
feature enabled, only a platform administrator adds it to an agent. An agent picks up
a change to **Code** or **Server credentials** when its tools are next built, for
example after it is saved or **Reload All**.

**May use platform keys** is stored, and has no effect yet: every workspace calls
models on the installation's provider keys.

## Edit, suspend, resume, close

- **Edit** changes the name, platform keys and features. The slug stays.
- **Suspend** stops the workspace: its dashboard, personal access tokens, agents
  (chat, API, A2A, MCP and the Work Room), widgets, Slack integrations, triggers and
  alert rules. Its data stays. Platform administrators are stopped too.
- **Resume** undoes a suspension.
- **Close** stops the workspace for good. Only a workspace with no projects can be
  closed: its owner deletes them first. A closed workspace stays in the list and
  keeps its name and slug.

Default can't be suspended or closed.

Every change is recorded in the audit log of the workspace it changed, as
`workspace.create`, `workspace.update`, `workspace.suspend`, `workspace.resume` or
`workspace.close`.
