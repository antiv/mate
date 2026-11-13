---
title: Workspaces
summary: Give another team or customer its own agents, people, budget and logs on the same installation, from creating the workspace to suspending it.
audience: user
order: 112
covers:
  - templates/dashboard/workspaces.html
  - server/platform_workspace_routes.py
  - server/invitation_routes.py::create_invitation
  - server/workspace_routes.py
  - shared/utils/dashboard/dashboard_server.py::dashboard_workspaces
---

# Workspaces

A workspace holds its own projects, agents, members and logs. Every installation
has one, **Default**, which holds everything a 1.x installation had. Add more when
another team or customer should build and run agents on the same installation
without seeing yours.

## Who does what

| Who | What they do |
|---|---|
| **Platform administrator** (the built-in account, or a user with the `admin` role) | Creates, suspends and closes workspaces on **Workspaces**, decides what each may use, and opens every workspace. |
| **Owner** of a workspace | Its whole dashboard: projects, agents, budgets, alerts, keys and integrations. Invites people and sets their roles on **Members**. |
| **Admin** of a workspace | The same dashboard, except that they can't change members or invite. |
| **Member** | The Work Room, with the agents their agent roles open, and their own usage. |

A person can belong to several workspaces, with a different role and different agent
roles in each. See [Users and roles](users-and-roles.md) for roles and agent roles.

## Set up a workspace, start to finish

1. **Create it.** A platform administrator opens **Workspaces**, clicks **New
   Workspace**, and enters the name and the owner's email address (below). The owner
   gets an invitation.
2. **The owner joins.** They open the invitation link, sign in with Google or GitHub as
   that address, and click **Join**. Their dashboard opens in the new workspace.
3. **The owner builds.** They create a project and its agents, as in Default. An
   agent's **Allowed Roles** decide which members may use it.
4. **The owner invites people** under **Members → Invitations**, and gives each the
   agent roles that open the agents they need (see
   [Invitations](users-and-roles.md#invitations)).
5. **The owner sets a budget** for the whole workspace on **Rate Limits**, with the
   scope **Whole workspace** (see [Rate limits](rate-limits.md#the-whole-workspace)),
   and an alert on **Alerts** with the scope **Whole workspace** to hear before it
   runs out.
6. **Members use the agents** in the Work Room, through a widget, Slack, triggers or
   the API with their own personal access token.

Nothing in it is visible from another workspace: agents, sessions, logs, budgets,
keys and members stay in their workspace.

## Switch between workspaces

Someone in more than one workspace has a workspace menu in the top bar. The dashboard
works in the workspace chosen there until they choose another; the Work Room follows
it too. **Leave this workspace** in the account menu takes them out of the current
one, unless they are its last owner. A platform administrator's menu lists every open
workspace.

## The Workspaces page

The **Workspaces** page is for platform administrators. Open it from the Control
Room, under **Governance & Access**. Owners of a workspace don't see it.

### The list

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

### Create a workspace

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

### What a workspace may use

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

### Edit, suspend, resume, close

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

## When a workspace is suspended

Everything that runs its agents stops at once, for everyone, and starts again when it
is resumed:

- its agents leave its members' Work Room, and the workspace leaves their workspace
  menu; someone who belongs to no other workspace sees no agent at all;
- its dashboard answers as not found, even for a platform administrator who opens it
  by name;
- its personal access tokens are refused, and so are its widgets, Slack
  integrations, A2A and MCP;
- its webhook triggers answer without running, and its scheduled triggers and alert
  rules are skipped;
- its open invitations can't be accepted.

Its projects, agents, members, budgets and logs stay as they were.

## Usage and logs across workspaces

Each workspace's owners and admins see its own usage, audit, guardrail, trace and
session logs. A platform administrator also has an **All workspaces** box on those
pages, which shows every workspace's entries, each named with its workspace (see
[Usage and audit logs](usage-and-audit.md)), and the 30-day usage of each workspace
in the list above.
