---
title: Evals
summary: Test cases that score an agent's answers, so you can tell whether a change made it better or worse.
audience: user
order: 80
covers:
  - templates/dashboard/evals.html
  - shared/utils/eval_runner.py
  - shared/utils/eval_agent_runner.py
  - shared/utils/agent_improver.py
  - shared/utils/feedback_service.py
  - shared/utils/dashboard/dashboard_server.py::*eval*
---

# Evals

An eval is a question with the answer you expect. A set of them for one agent is a
suite. Running the suite before and after you change an agent tells you whether the
change helped, and whether it broke something that used to work.

Evals are in **Studio → Evals**.

## Add a test case

Click **Add Test Case** and fill in:

| Field | What to enter |
|---|---|
| **Agent Name** | The agent to test. |
| **Input** | The message to send it. |
| **Expected Output** | The answer you want. |
| **Eval Method** | How the real answer is compared with the expected one; see below. |
| **Threshold** | The score, from 0 to 1, at which the case passes. 0.7 if you leave it. |
| **Judge Model** | For the LLM Judge method only: the model that does the scoring. |

### Choosing a method

| Method | Scores | Use it for |
|---|---|---|
| **Exact Match** | 1 if the answer equals the expected text, ignoring case and surrounding spaces; otherwise 0. | Fixed outputs: a category, a yes or no, a code. |
| **Semantic Similarity** | How close the answer is to the expected text, from 0 to 1. | Answers that should say the same thing in other words. |
| **LLM Judge** | A model reads the question, the expected answer and the real answer, and gives a score with a one-sentence reason. | Open answers, where wording and length vary. |

An LLM Judge case needs a judge model, either on the case or set for the whole
server. With neither, the case is skipped rather than scored.

How good Semantic Similarity is depends on the server: with a sentence-embedding
library installed it compares meaning; without one it only compares the text.

## Run a suite

1. Click the agent in **Agent Test Suites**.
2. Click **Run Suite**, choose the version of the agent to score, and click
   **Run All**.

MATE sends every case to the agent itself and records each answer and score.
**Run** on a single row runs just that case.

Things to know:

- The agent is built from the **version you chose**, not necessarily the one that is
  live. Its sub-agents run with their current configuration.
- Each case runs in a fresh conversation.
- Role checks are skipped for eval runs, so an agent that would refuse you in chat
  can still be evaluated.
- Runs call the real models and cost tokens. They are listed under the *eval* origin
  on the **Usage** page.

**Score History** shows the average score and pass rate per version, which is where
you see whether your last change moved things up or down. When a run scores more
than 0.05 below the previous version's average it is flagged as a regression, and
your administrator can have such regressions posted to a webhook.

You can also run the suite from an agent's **History** dialog, for the version you
are looking at.

## Turn a bad answer into a test

**Rated Down** lists the answers people gave a thumbs-down in the Work Room or the
widget, with their comment. A [standalone build](../dev/standalone-build.md#response-ratings)
can send its ratings here too; those rows are marked *sent by a standalone build*,
because the question and answer come from the build rather than from a conversation
stored on this server. **Add to evals** opens a new test case with the agent
and question filled in. Write the answer the agent should have given, and that
mistake is checked on every future run.

## Suggest a fix

On a failing case, or on a rated-down answer, **Suggest a fix** asks a model to
rewrite the agent's instruction so the case would pass.

1. It shows the current and the suggested instruction side by side, with the reason.
   You can edit the suggestion, and tick memory blocks the agent read if their
   content should be revised too.
2. **Check against suite** runs the whole suite twice, with the current and with the
   suggested instruction, and shows the results side by side. Cases that passed
   before and fail now are highlighted. Nothing is changed yet.
3. **Apply** saves the suggestion you just checked as a new version of the agent,
   which you can roll back from **History**.

Only the instruction and the memory blocks you ticked can change. Tools, model and
roles are never touched. If someone edited the instruction in the meantime, Apply is
refused and you start again from the current text.
