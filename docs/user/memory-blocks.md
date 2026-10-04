---
title: Memory blocks
summary: Shared, named notes that agents read and update, with history and restore.
audience: user
order: 40
covers:
  - templates/dashboard/modals/memory_blocks_modal.html
  - static/js/modals/memory-blocks.js
  - shared/utils/memory_blocks_service.py
  - shared/utils/tools/memory_blocks_tools.py
  - shared/utils/dashboard/dashboard_server.py::*memory_block*
---

# Memory blocks

A memory block is a named piece of text that belongs to a project. Every agent in
the project that has the Memory Blocks tool can read it, which makes blocks the
place for anything several agents should share or that should outlive one
conversation: house rules, a product list, facts about a customer, the result of
last night's scheduled run.

## Give an agent access

Open the agent on **Studio → Agents**, click **Configure** under
**Tool Configuration**, tick **Memory Blocks** and save. Until an agent has the tool,
its **Manage Blocks** dialog cannot create blocks.

## Create and edit blocks

In the agent form, click **Manage Blocks**. The dialog lists the project's blocks.

| Field | Meaning |
|---|---|
| **Label** | The block's name. Agents ask for a block by its label, so choose one that says what is inside, such as `returns_policy`. |
| **Value** | The text itself. |
| **Description** | A note about what the block is for. |
| **Character Limit**, **Read-only**, **Preserve on migration** | Recorded with the block. See the note below. |

Use the search row at the top to filter the list; **Add Condition** combines several
filters.

> **Read-only and Character Limit are labels, not locks.** They are stored with the
> block, but the server does not check them when a block is written. An agent that
> is allowed to modify blocks can still overwrite a block marked read-only.

## What an agent can do with blocks

| The agent can | When |
|---|---|
| List blocks, optionally filtered by label or content | always |
| Search blocks by meaning, without knowing a label | always |
| Read one block by its label | always |
| Create, change or delete a block | **only while the person it is talking to has the `admin` role** |

So in a conversation with an ordinary user the agent can consult its memory but
cannot rewrite it. The role is the one on the person's user record; see
[Users and roles](users-and-roles.md) for giving it to the built-in account. This keeps a visitor from talking an agent into changing its own
rules.

Search by meaning uses an embedding model. If none is available on the server, the
agent falls back to matching words in labels and values.

### One block per person

In a label, `{user_id}` is replaced by the id of the person the agent is talking to
when the agent lists or creates blocks. An instruction such as "keep what you learn
about the user in the block `human_{user_id}`" gives every person their own block.

## History and restore

Every change to a block is recorded with who made it: a person in the dashboard, an
agent (and whose conversation it was), or a trigger. The last 20 versions of each
block are kept.

- **History** on a block lists its versions and restores any of them.
- **Deleted blocks** lists blocks that were deleted, and brings one back.

## Patterns that work

**Keep long instructions in blocks.** Give the agent a short instruction that tells
it to load its detailed rules from blocks at the start of a conversation, for
example every block whose label starts with `system_instruction_`. You can then
change the rules without touching the agent, and several agents can share them.

**Hand work from one agent to another.** A [trigger](triggers.md) can write an
agent's answer into a block every night; another agent reads that block the next
day.

**Remember people.** Combine a per-person block with an instruction to update it
whenever the agent learns something worth keeping. Remember that the agent can only
write the block while someone with the `admin` role is the one chatting; for ordinary users, the
profile on the **Users** page is the place agents can update on their own.
