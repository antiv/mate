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
---

# Users and roles

Two separate checks decide what a person can do in MATE, and they are easy to
confuse:

- **Dashboard access** depends on how the person signed in.
- **Agent access** depends on the roles stored on their user record.

Users are managed in **Control Room → Users**.

## Dashboard access

A person gets the whole dashboard (Studio and Control Room) when they sign in with
the built-in account, or sign in through Google or GitHub and have the `admin` role.
Everyone else sees the Work Room and their own usage.

## Agent access

Whether an agent answers a person depends only on that person's roles and the
agent's **Allowed Roles**. Being a dashboard administrator does not bypass it.

## Roles

A role is a label. A person can have several, and you can invent your own, such as
`support` or `sales`. Four have a built-in meaning:

| Role | Meaning |
|---|---|
| `admin` | The whole dashboard for people who sign in through Google or GitHub. Admits the person to agents with an empty **Allowed Roles** list, and lets agents change memory blocks on their behalf. |
| `user` | An ordinary person. Agents that list `user` in **Allowed Roles** are open to them. |
| `pending` | Signed in, but not yet given access to anything. New single sign-on users start here. |
| `widget` | Assigned automatically to visitors who chat through an embedded widget. |

## Open an agent to people

Each agent has an **Allowed Roles** list. A person can use the agent when at least
one of their roles is on that list.

- An agent with `["user"]` is open to everyone with the `user` role, and closed to
  someone who has only `admin`.
- An agent with `["support", "admin"]` is open to people with either role.
- **An agent with an empty list is open only to people with the `admin` role.**

> **Give the built-in account the `admin` role.** Its user record does not exist
> until it first talks to an agent, and is then created with the role `user`. On a
> new installation, the built-in account is therefore refused by any agent whose
> list is empty. Open the Users page, edit that user and set its roles to
> `user, admin`.

Someone who is refused is told which roles the agent requires and which roles they
have. Each refusal is written to the audit log.

## Let a new person in

When someone signs in through Google or GitHub for the first time, MATE creates
their user record with the `pending` role. They can sign in but cannot use any
agent.

1. Open **Control Room → Users**. Filter by **Provider** or search for their email.
2. Click edit on their row.
3. Replace `pending` in **Roles** with the roles they should have, separated by
   commas, for example `user` or `user, support`.

Your administrator can change the starting role for new sign-ins, and can restrict
sign-in to certain email domains or addresses; see `OAUTH_DEFAULT_ROLE`,
`OAUTH_ALLOWED_DOMAINS` and `OAUTH_ALLOWED_EMAILS` in the
[configuration reference](../reference/configuration.md).

**Add User** creates a record by hand: enter a user id and roles. This is useful for
giving roles in advance to an id that an application will use when it calls an agent
through the API.

## Profiles

Each user has **Profile Data**: free text about the person. Agents receive it with
every conversation and use it to personalise their answers, and an agent can add to
it when it learns something about the person. Edit or clear it from the user's row.

## Users you did not create

A user record is also created automatically the first time an unknown id reaches an
agent: with the `user` role for the API and the dashboard, and with the `widget`
role for visitors of an embedded widget. On a busy public widget this adds up.

MATE removes such temporary users once they have been inactive for five days. The
cleanup runs every night, and **Clean Up Inactive Users** runs it immediately. It
never removes:

- the built-in administrator
- people who signed in through Google or GitHub
- anyone with the `admin` role
- anyone who has a personal access token

Removing a temporary user also removes their conversations.
