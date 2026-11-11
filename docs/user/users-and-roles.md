---
title: Users and roles
summary: Who counts as an administrator, how roles open agents to people, and how user records are created and cleaned up.
audience: user
order: 110
covers:
  - templates/dashboard/users.html
  - shared/utils/user_service.py
  - shared/utils/user_cleanup.py
  - shared/utils/rbac_middleware.py
  - server/auth.py::get_user_role
  - server/auth.py::_is_session_oauth_user
  - shared/utils/dashboard/dashboard_server.py::*user*
  - server/workspace_routes.py
  - server/member_routes.py
  - templates/dashboard/members.html
  - templates/base.html
---

# Users and roles

Two separate checks decide what a person can do in MATE, and they are easy to
confuse:

- **Dashboard access** depends on how the person signed in, and on their role in the
  workspace.
- **Agent access** depends on their agent roles in the workspace.

People are managed on two pages. **Control Room → Users** holds every user record of
the installation and their platform roles. **Control Room → Members** holds the
people of the current workspace, with their role and agent roles there.

## Dashboard access

A person gets the whole dashboard (Studio and Control Room) when they sign in with
the built-in account, or sign in through Google or GitHub and have the `admin` role.
Everyone else sees the Work Room and their own usage. People with a role in
`ALLOWED_API_ROLES` (by default `admin` and `developer`), in the workspace they work
in, also get **Personal Tokens**
in the account menu, for the OpenAI-compatible API and the agents' MCP servers; see
[Personal access tokens](personal-access-tokens.md).

### Platform administrators and workspace administrators

Agents, projects and logs belong to a **workspace**. An installation starts with one,
Default, and with one workspace nothing below changes what anyone sees.

- A **platform administrator** (the built-in account, or anyone with the `admin`
  role) has the whole dashboard of every workspace, and the pages that cover the
  whole installation: **Users**, **Workspaces** (see [Workspaces](workspaces.md)),
  **Settings**, **Migrations**, the agent server's start and stop controls, system
  jobs, audit-log retention, the wizard and shop orders, saving and deleting
  templates, **Reload All** and binary builds.
- An **owner** or **admin** of a workspace has the dashboard of that workspace, as a
  platform administrator does, but not those installation pages: their links are
  hidden, and the pages send them to the Work Room.
- A **member** has the Work Room and their own usage.

An owner sets these roles on the **Members** page (below).

### Switching workspace

A person who may open more than one workspace sees a workspace menu in the top bar,
next to the theme switch. Choosing one reloads the page in that workspace, and the
dashboard and Work Room stay in it until they choose another. With one workspace the
menu is not shown. Scripts that use the API name a workspace with the
`X-MATE-Workspace` header instead.

## Agent access

Whether an agent answers a person depends on their agent roles in the agent's
workspace and the agent's **Allowed Roles**. A platform administrator, and an owner or
admin of the workspace, pass every agent of it. Anyone else is matched on their
agent roles.

## Roles

There are two kinds of role.

**Platform roles** are on the user record, and set on the **Users** page:

| Role | Meaning |
|---|---|
| `admin` | A platform administrator: the whole dashboard of every workspace, every agent whatever its **Allowed Roles**, and agents may change memory blocks on their behalf. |
| `pending` | Signed in, but not let in yet: no workspace, no agent. New single sign-on users start here. |
| `widget` | Assigned automatically to visitors who chat through an embedded widget. |

**Agent roles** are per workspace, and set on the **Members** page. A role is a
label: a person can have several, and you can invent your own, such as `support` or
`sales`. Two have a built-in meaning:

| Role | Meaning |
|---|---|
| `user` | An ordinary person. Agents that list `user` in **Allowed Roles** are open to them. A new member gets it. |
| `developer` | May create personal access tokens and call the API with them in that workspace, while the dashboard stays the Work Room. The roles with this right are set by `ALLOWED_API_ROLES`, `admin` and `developer` by default. |

A person can have different agent roles in different workspaces. Before 2.0, agent
roles were on the user record with the platform roles. The first start of 2.0 moves
each user's agent roles to their membership of the Default workspace, and the user
record keeps the platform roles.

## Members

**Control Room → Members** lists the people of the workspace you are working in: their
role (owner, admin or member), their agent roles, and when they joined. Owners and
admins of the workspace see the page, and platform administrators see it in every
workspace.

- An owner, or a platform administrator, changes a member's role and agent roles
  (separated by commas) and clicks **Save**, or removes them with **Remove**. Removing
  someone keeps their user record; their personal access tokens for the workspace
  stop working.
- An admin of the workspace sees the list but can't change it.
- Anyone leaves the workspace with **Leave workspace** on the page, or **Leave this
  workspace** in the account menu, shown to people with more than one workspace.
- The last owner can't be demoted, removed or leave: make someone else an owner
  first.

Every change is written to the workspace's audit log, as `member.update`,
`member.remove` or `member.leave`.

## Open an agent to people

Each agent has an **Allowed Roles** list. A member can use the agent when at least
one of their agent roles in its workspace is on that list. Platform administrators,
and owners and admins of the workspace, can use every agent of it.

- An agent with `["user"]` is open to every member with the `user` role.
- An agent with `["support", "sales"]` is open to members with either role.
- **An agent with an empty list is open only to administrators.**

The built-in account (`AUTH_USERNAME`) always has the `admin` role. It is added back
whenever it is missing, so the account is not refused by an agent whose list is
empty. Removing `admin` from it on the Users page lasts only until its next chat.

Someone who is refused is told which roles the agent requires. Each refusal is
written to the audit log.

## Let a new person in

When someone signs in through Google or GitHub for the first time, MATE creates
their user record with the `pending` role. They can sign in but cannot use any
agent.

1. Open **Control Room → Users**. Filter by **Provider** or search for their email.
2. Click edit on their row.
3. Remove `pending` from **Platform roles** and save. They join the Default workspace
   as a member with the `user` agent role.
4. To give them other agent roles, open **Control Room → Members** in the Default
   workspace and change theirs there.

Your administrator can change the starting role for new sign-ins, and can restrict
sign-in to certain email domains or addresses; see `OAUTH_DEFAULT_ROLE`,
`OAUTH_ALLOWED_DOMAINS` and `OAUTH_ALLOWED_EMAILS` in the
[configuration reference](../reference/configuration.md).

**Add User** creates a record by hand: enter a user id and roles. `admin` and
`pending` stay on the record; any other role is an agent role the user gets as a
member of the Default workspace. This is useful for giving roles in advance to an id
that an application will use when it calls an agent through the API.

## Profiles

Each user has **Profile Data**: free text about the person. Agents receive it with
every conversation and use it to personalise their answers, and an agent can add to
it when it learns something about the person. Edit or clear it from the user's row.

## Users you did not create

A user record is also created automatically the first time an unknown id reaches an
agent: as a member of the agent's workspace with the `user` agent role for the API
and the dashboard, and with the `widget` role for visitors of an embedded widget. On a busy public widget this adds up.

MATE removes such temporary users once they have been inactive for five days. The
cleanup runs every night, and **Clean Up Inactive Users** runs it immediately. It
never removes:

- the built-in administrator
- people who signed in through Google or GitHub
- anyone with the `admin` role
- anyone who has a personal access token

Removing a temporary user also removes their conversations.
