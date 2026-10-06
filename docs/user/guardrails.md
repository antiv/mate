---
title: Guardrails
summary: Automatic checks on what people send an agent and on what it answers, and the log of every time one fired.
audience: user
order: 120
covers:
  - shared/utils/guardrails/**
  - shared/callbacks/guardrail_callback.py
  - shared/utils/guardrail_log_service.py
  - templates/dashboard/guardrail_logs.html
---

# Guardrails

A guardrail inspects a message and reacts when it finds something it was told to
look for: personal data, an attempt to override the agent's instructions, a
forbidden word, an answer that is too long. Guardrails are set per agent.

## Switch guardrails on

Open the agent on **Studio → Agents** and click **Configure** under **Guardrails**.
Tick the checks you want and choose an action for each.

| Guardrail | Looks for | Checked on |
|---|---|---|
| **PII Detection** | Email addresses, phone numbers, social security numbers, credit card numbers, IP addresses. | what the person sends and what the agent answers |
| **Prompt Injection** | Phrasings that try to make the agent ignore its instructions. **Sensitivity** (Low, Medium, High) sets how many patterns are used. | what the person sends |
| **Content Policy** | Words on your blocklist and text matching your regular expressions. | both |
| **Output Length** | Answers longer than **Max chars** or **Max words**. | what the agent answers |
| **Hallucination Check** | Answers a second model judges to be fabricated or inconsistent. | what the agent answers |

### Actions

| Action | On a message from a person | On the agent's answer |
|---|---|---|
| **Block** | The agent is not called. The person sees "I cannot process this request" and the reason. | The answer is replaced by "Response blocked by safety guardrail" and the reason. |
| **Redact** | The matched text is replaced by a placeholder such as `[EMAIL_REDACTED]` before the agent sees it. | The matched text is replaced the same way before the person sees it. |
| **Warn** | Nothing changes for the person. The hit is recorded and written to the server log. | the same |
| **Log** | Nothing changes. The hit is recorded. | the same |

A sensible start: **Redact** for PII, **Block** for prompt injection, and **Log** for
anything you are still tuning, so you can see how often it would fire before it
affects anyone.

## What guardrails cannot do

- **Prompt injection detection matches known phrasings.** It stops the obvious
  attempts and misses inventive ones. Do not rely on it alone for an agent that has
  powerful tools; limit the tools instead.
- **PII detection matches formats**, such as something shaped like an email address
  or a card number. It does not recognise names or addresses.
- **The hallucination check is a second opinion from another model**, with that
  model's own error rate. It is configured in the guardrails dialog or directly
  in the JSON, with the judge `model` and a `threshold` (0.7 if not set). If the
  judge model cannot be reached, the answer is let through.
- **A guardrail that fails does not block.** If a check raises an error, the message
  passes and the error is logged.
- Every check adds a little time to each message; the hallucination check adds a
  full model call.

## See what fired

**Control Room → Guardrail Logs** lists every hit: when, which agent and user, which
guardrail, whether it was on the way in or out, the action taken and what matched.
Filter by agent, guardrail type, phase or action.

Use it to tune. Many **Log** hits on harmless messages mean a rule is too broad; no
hits at all on a public agent may mean a rule is not doing anything.

To be told when hits pile up, create a *Guardrail hits* rule on the
[Alerts](alerts.md) page.
