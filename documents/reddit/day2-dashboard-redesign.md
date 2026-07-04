**Title:** MATE dashboard got a full visual redesign — design tokens, light/dark, and a cleaner Workroom

---

Shipped a top-to-bottom visual refresh of the MATE dashboard. It's not just new paint — the whole UI now runs on a proper **design-token system**, so colors, surfaces, borders and shadows are consistent everywhere and theming is a single source of truth.

## What changed

- **Design tokens** — one set of CSS variables drives the entire dashboard: `--bg-page` / `--bg-surface`, `--text-primary/secondary/tertiary`, `--border` / `--border-strong`, `--accent` (+ hover/soft), plus semantic `--success` / `--warning` / `--danger` families and a unified `--shadow-card`. Every template pulls from these instead of hardcoded hex values.
- **Light & dark mode** — the dashboard now follows your OS preference and supports an explicit theme toggle. Both themes are first-class, not an afterthought.
- **Redesigned login** — new login page with built-in theme toggling as the first thing you see.
- **Reworked navigation** — the base layout and sidebar were rebuilt for clearer structure and less visual noise.
- **Cleaner Workroom** — the chat + canvas workspace got a substantial layout pass: better proportions, tidier controls, easier to read long agent conversations.
- **Refreshed overview, agents, and usage views** — the landing dashboard, agent management, and token-usage analytics all adopted the new system.

## Why it matters

If you build agents in MATE all day, the dashboard *is* the product. The token system means future features slot in looking native instead of bolting on one-off styles, and the light/dark support finally makes long sessions comfortable regardless of your setup.

Screenshots across the dashboard were updated to the new look. Would love feedback on the dark theme specifically — and if there's a view that still feels off, tell me and I'll prioritize it.
