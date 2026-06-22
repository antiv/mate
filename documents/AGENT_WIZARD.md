# MATE Agent Builder Wizard

An embeddable, step-by-step wizard that lets prospects build, **test**, and request an AI agent
straight from your marketing site. The wizard provisions a live **trial agent** the visitor can
chat with, then captures their contact details as a lead. There is **no billing** — the wizard
ends with a price estimate and a "contact us" instruction; you follow up to activate.

---

## Quick Start

Add the wizard to any page as an inline iframe with a single `<script>` tag:

```html
<div id="mate-wizard"></div>
<script
  src="https://your-mate-instance.com/wizard/mate-wizard.js"
  data-server="https://your-mate-instance.com"
  data-target="mate-wizard"
></script>
```

The wizard renders inside `#mate-wizard` and auto-resizes. You can also embed it directly:

```html
<iframe src="https://your-mate-instance.com/wizard/embed" style="width:100%;height:720px;border:0"></iframe>
```

---

## Loader Options

Configuration is done via `data-*` attributes on the script tag:

| Attribute | Required | Default | Description |
|---|---|---|---|
| `data-server` | Yes | — | URL of your MATE instance (e.g. `https://mate.example.com`) |
| `data-target` | No | (script's parent) | `id` of the element to render the wizard into |
| `data-tier` | No | — | Pre-select a tier (`tier1`…`tier4`), skipping the chooser |
| `data-height` | No | `720` | Initial iframe height in pixels (auto-grows via `postMessage`) |

---

## Tiers

Tiers and their display prices are defined in
[`shared/utils/wizard/pricing.py`](../shared/utils/wizard/pricing.py). A tier offers a **live trial**
only if its agent template exists; otherwise it collects a structured request as a lead.

| Tier | What it does | Trial provisioned | Template |
|---|---|---|---|
| **tier1** — Website Support | Reads the prospect's website (via the `browser` tool) to answer support questions | ✅ | `wizard-tier1-website-support.json` |
| **tier2** — Support + Scheduling | tier1 + Google Calendar (availability / booking) | When the Calendar tool + template are configured | `wizard-tier2-support-scheduling.json` |
| **tier3** — Sales | Fills cart / places orders via the store's e-commerce MCP | When the Shopify MCP + template are configured | `wizard-tier3-sales.json` |
| **tier4** — Custom | Free-form requirements forwarded to you as a lead | ✗ (lead only) | — |

Trial tier templates live in [`templates/agent_templates/`](../templates/agent_templates/) and reuse the
[Template Library](TEMPLATE_LIBRARY.md). Per-prospect values are injected into `{{PLACEHOLDER}}`
tokens at provision time (e.g. `{{SITE_URL}}`, `{{EXTRA_INSTRUCTIONS}}`,
`{{STORE_DOMAIN}}`, `{{STORE_TOKEN}}`).

---

## How a Trial Works

1. The visitor picks a tier and fills in the config (e.g. website URL + instructions).
2. MATE provisions an isolated project named `trial_<tier>_<token>`, creates the agent(s) from the
   tier template, and issues a `wk_…` **widget key**.
3. The wizard embeds the standard [widget chat](WIDGET_INTEGRATION.md) so the visitor can test the
   agent immediately.
4. The visitor submits their details → a **lead** is stored (with a snapshot of the price estimate
   and a reference to the trial).
5. Trials are disposable: a daily job removes expired ones (see **Cleanup** below). Leads are kept.

---

## Managing Leads

Open the **MATE Dashboard → Wizard Leads** (`/dashboard/wizard-leads`, admin only) to:

- See every lead (name, email, company, tier, estimated price, created date).
- Open the lead's trial chat (while the trial is still alive).
- Move a lead through `new → contacted → converted → archived`.

---

## Configuration (Environment Variables)

| Variable | Default | Purpose |
|---|---|---|
| `WIZARD_CONTACT_EMAIL` | `sales@example.com` | Contact address shown on the final wizard screen and returned to the lead |
| `WIZARD_TRIAL_TTL_DAYS` | `7` | How long a trial agent lives before cleanup |
| `WIZARD_CLEANUP_ENABLED` | `true` | Enable the daily trial cleanup job |
| `WIZARD_CAPTCHA_PROVIDER` | (unset) | When set, enables the captcha hook on provisioning (pluggable; off by default) |

---

## Endpoints

Public (no login; protected by a per-session token + per-IP throttle):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/wizard/embed` | The wizard iframe page (`?tier=` to deep-link) |
| `GET` | `/wizard/mate-wizard.js` | Embeddable loader script |
| `GET` | `/wizard/api/tiers` | Tier catalogue + display prices |
| `POST` | `/wizard/api/session/start` | Start a wizard session → `session_token` |
| `POST` | `/wizard/api/session/step` | Save step inputs |
| `POST` | `/wizard/api/session/provision` | Provision the trial agent (rate-limited) |
| `POST` | `/wizard/api/lead` | Submit the lead |

Admin (dashboard auth):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/dashboard/wizard-leads` | Leads page |
| `GET` | `/dashboard/api/wizard/leads` | List leads (`?status=`) |
| `PATCH` | `/dashboard/api/wizard/leads/{id}` | Update lead status |

---

## Abuse Protection

- Every write endpoint requires the opaque `session_token` issued by `/wizard/api/session/start`.
- `start` and `provision` are throttled per-IP in process.
- Trial token usage can be capped with the standard [rate limits](RATE_LIMITS.md).
- A captcha hook (`WIZARD_CAPTCHA_PROVIDER`) can be enabled for the provision step.

---

## Related Docs

- [Embeddable Chat Widget](WIDGET_INTEGRATION.md) — the chat component reused for the test step
- [Template Library](TEMPLATE_LIBRARY.md) — how tier templates are defined and imported
- [Rate Limits](RATE_LIMITS.md) — capping trial usage
