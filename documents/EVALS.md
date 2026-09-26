# Eval Framework

The Eval Framework gives you a structured way to measure whether an agent config change improved or degraded quality. You define test cases (input + expected output), run them against any stored version of an agent's config, and track score history over time.

## Overview

```
┌─────────────────────────────────────────────────────────┐
│  /dashboard/evals                                       │
├──────────────────────┬──────────────────────────────────┤
│  Agent Suites        │  Test Cases for <agent>          │
│  agent  #  avg score │  input | expected | method|score │
├──────────────────────┴──────────────────────────────────┤
│  Score History (avg_score + pass_rate per version)      │
└─────────────────────────────────────────────────────────┘
```

- **Agent Suites** — left panel lists every agent that has at least one active test case
- **Test Cases** — right panel shows cases for the selected agent, with the most recent score per case
- **Score History** — Chart.js line chart showing `avg_score` and `pass_rate` across config versions

## Database Schema

Two tables are created by migration `V011__eval_framework.sql`:

### `test_cases`

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer PK | auto-increment |
| `agent_name` | varchar(255) | matches `agents_config.name` |
| `version_id` | integer FK → `agent_config_versions.id` | nullable (ON DELETE SET NULL) |
| `input` | text | prompt sent to the agent |
| `expected_output` | text | ground-truth reply |
| `eval_method` | varchar(50) | `exact_match`, `semantic_similarity`, or `llm_judge` |
| `judge_model` | varchar(255) | LiteLLM model string used when `eval_method=llm_judge` |
| `threshold` | float | pass threshold, default 0.7 |
| `created_at` | datetime | UTC |
| `created_by` | varchar(255) | optional username |
| `is_active` | boolean | soft-delete flag, default true |
| `source_feedback_id` | integer | nullable; the `response_feedback` row the case was made from (V032) |

### `eval_results`

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer PK | auto-increment |
| `test_case_id` | integer FK → `test_cases.id` | CASCADE delete |
| `version_id` | integer FK → `agent_config_versions.id` | CASCADE delete |
| `actual_output` | text | agent reply recorded during the run |
| `score` | float | 0.0 – 1.0 |
| `passed` | boolean | `score >= threshold` |
| `eval_method` | varchar(50) | method used for this result |
| `details` | text | JSON with reasoning or similarity info |
| `error` | text | error message if the run failed |
| `run_at` | datetime | UTC |

## Eval Methods

### `exact_match`

Returns `1.0` if the stripped, lowercased actual output equals the stripped, lowercased expected output, otherwise `0.0`. Fastest and deterministic — good for structured outputs or simple factual answers.

### `semantic_similarity`

Computes cosine similarity between sentence embeddings using `sentence-transformers` (model `all-MiniLM-L6-v2`). Falls back to `difflib.SequenceMatcher.ratio()` when the library is not installed. Good for paraphrase-tolerance tests where exact wording doesn't matter.

### `llm_judge`

Sends a structured prompt to any LiteLLM-compatible model asking it to score the response from `0.0` to `1.0`:

```
You are an impartial evaluator. Given:
- Input: {input}
- Expected: {expected}
- Actual: {actual}

Score how well the actual output satisfies the expected output.
Respond ONLY with valid JSON: {"score": <float 0.0-1.0>, "reasoning": "<brief explanation>"}
```

The judge model default is `gemini/gemini-2.0-flash`. Any LiteLLM model string works (e.g. `openai/gpt-4o`, `anthropic/claude-haiku-4-5-20251001`). Markdown code fences are stripped before JSON parsing. The score is clamped to `[0.0, 1.0]`.

## Agent Auto-Invocation

When you run an eval, MATE runs the agent for you — no need to copy-paste agent responses — and it runs **the version you selected**, not whichever one is deployed.

The agent is built from the selected version's config snapshot and run in the dashboard process through an ADK `Runner` with an in-memory session (`shared/utils/eval_agent_runner.py`). Nothing is written to `agents_config`, and the deployed agent is not touched, so a version can be scored before, after or instead of being rolled back to.

- **The agent itself comes from the version.** Instruction, model, endpoint, tools, guardrails — everything in the snapshot.
- **Its sub-agents come from their current config.** They are separate rows with versions of their own; a version records one agent.
- **One build per suite run.** Each test case gets a fresh session; MCP toolsets start once and are closed when the run ends.
- **RBAC is skipped for eval runs.** Evals are started by an admin from the dashboard, and an agent with no roles configured is admin-only, which would refuse the eval user. The skip is keyed on a context variable set only inside the in-process eval run, which no request from outside can set.
- **LangGraph is not supported yet.** With `AGENT_FRAMEWORK=langgraph`, running against a version returns `501` rather than silently running the deployed agent. Supplying `actual_output` yourself still works.
- A single-case run refuses a `version_id` that belongs to a different agent (`400`).

Only the **final user-facing reply** is scored: text is tracked per author and reset when the author changes or a tool call or response appears, and routing events are ignored.

## Running Evals

### From the Evals Dashboard

1. Go to `/dashboard/evals`
2. Click an agent name in the left panel to load its test cases
3. Click **Run Suite** → select a version from the dropdown → **Run All**

The backend runs all active test cases for that agent against the selected version, stores results, and returns aggregate stats.

### From the Version History Modal

1. Open an agent → click **History** icon
2. Select a version from the left list
3. Click **Run Evals** (green button)

Results appear inline below the diff viewer: `N passed / N failed / avg score / pass rate`. A regression warning is shown if the new version scores more than 5 points below the previous.

### Run a Single Test Case

Click the **Run** button on any individual test case row in the Evals dashboard. You will be prompted to select a version; the agent is invoked automatically and the result is shown immediately.

### From a Thumbs-Down

The **Rated Down** section at the bottom of the Evals page lists responses users gave a 👎 in the widget or the Work Room, newest first and filterable by agent. After a 👎 the chat offers an optional *What was wrong?* note; the rating is recorded on the click, and the note, if one is sent, is added to the same rating and shown here as the comment. A rating stores only the session and the invocation id, so the question and the answer are read back from the session history (ADK or LangGraph). If the session has since been deleted, the rating is still listed, with the question and answer shown as unavailable.

**Add to evals** opens the test case form with the agent and the question filled in and `llm_judge` selected, since a hand-written expected answer rarely matches word for word. Expected output is left for you to write; the answer that was rated down is shown beside it for reference and is not saved. Once saved, the row links to the test case, and the same response cannot be added a second time (`409`) while that test case is active.

The list is admin-only, because it shows what visitors said to the agent.

## Regression Alerts

After each full suite run (`POST /dashboard/api/evals/version/{version_id}/run`), the server:

1. Computes `new_avg` — average score across all results for this version
2. Queries the immediately preceding version's results for the same agent → `prev_avg`
3. If `new_avg < prev_avg - 0.05` and `EVAL_REGRESSION_WEBHOOK_URL` is set, fires a POST:

```json
{
  "timestamp": "2026-04-27T12:00:00Z",
  "type": "eval_regression_alert",
  "agent_name": "my_agent",
  "version_id": 42,
  "new_avg_score": 0.61,
  "prev_avg_score": 0.78,
  "regression": true
}
```

Set the env var to any webhook URL (Slack incoming webhook, n8n, Make, custom endpoint):

```bash
EVAL_REGRESSION_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
```

## API Reference

All endpoints require HTTP Basic Auth (same credentials as the dashboard).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/dashboard/evals` | Serve the Evals dashboard page |
| `GET` | `/dashboard/api/evals` | List agent suites (name, count, last_run, avg_score) |
| `GET` | `/dashboard/api/evals/agent/{name}` | Test cases for agent + each case's latest result |
| `GET` | `/dashboard/api/evals/agent/{name}/history` | Score history for chart (avg_score + pass_rate per version) |
| `GET` | `/dashboard/api/evals/agent/{name}/versions-list` | Real version records for the version dropdown |
| `GET` | `/dashboard/api/evals/feedback` | Rated-down responses with question and answer (admin only); `?agent_name=` filters |
| `POST` | `/dashboard/api/evals` | Create a test case; optional `source_feedback_id` links it to a rating |
| `PUT` | `/dashboard/api/evals/{id}` | Update a test case |
| `DELETE` | `/dashboard/api/evals/{id}` | Soft-delete (sets `is_active=False`) |
| `POST` | `/dashboard/api/evals/{id}/run` | Run one test case; body: `{version_id}` |
| `POST` | `/dashboard/api/evals/version/{version_id}/run` | Run all active cases for the agent; body: `{results: []}` |

### Create Test Case

```bash
curl -u admin:mate -X POST http://localhost:8000/dashboard/api/evals \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_name": "my_agent",
    "input": "What is the capital of France?",
    "expected_output": "Paris",
    "eval_method": "exact_match",
    "threshold": 0.9
  }'
```

### Run Suite for a Version

```bash
curl -u admin:mate -X POST http://localhost:8000/dashboard/api/evals/version/32/run \
  -H 'Content-Type: application/json' \
  -d '{"results": []}'
```

Response:

```json
{
  "success": true,
  "passed": 3,
  "failed": 1,
  "total": 4,
  "avg_score": 0.81,
  "pass_rate": 0.75,
  "regression_alert": false,
  "results": [...]
}
```

### Score History

```bash
curl -u admin:mate http://localhost:8000/dashboard/api/evals/agent/my_agent/history
```

Response:

```json
{
  "history": [
    {"version_id": 30, "version_number": 1, "avg_score": 0.72, "pass_rate": 0.5, "run_at": "2026-04-20T10:00:00Z"},
    {"version_id": 32, "version_number": 2, "avg_score": 0.81, "pass_rate": 0.75, "run_at": "2026-04-27T12:00:00Z"}
  ]
}
```

## Hallucination Guardrail

The `hallucination_check` guardrail uses the same LLM-as-judge infrastructure as `llm_judge` evals. Configure it in an agent's guardrail settings:

```json
{
  "hallucination_check": {
    "enabled": true,
    "model": "gemini/gemini-2.0-flash",
    "threshold": 0.7
  }
}
```

The guardrail scores the agent's response for factual consistency on a `0.0–1.0` scale. If `score < threshold`, the response is flagged (`triggered=True`) and can be blocked or logged depending on your guardrail action setting.

**Fail-open**: any LLM call error results in `triggered=False` with a warning log — the guardrail never blocks on infrastructure failure.

## Score Colour Legend

| Colour | Range | Meaning |
|--------|-------|---------|
| Green | ≥ 0.8 | Passing |
| Yellow | 0.5 – 0.8 | Borderline |
| Red | < 0.5 | Failing |

These thresholds apply to the badge colours in both the Evals dashboard and the Version History modal result display.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `EVAL_REGRESSION_WEBHOOK_URL` | Webhook URL for regression alerts (optional) |

The `judge_model` and `threshold` are configured per test case in the database, not via env vars.
