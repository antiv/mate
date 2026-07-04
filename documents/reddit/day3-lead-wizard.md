**Title:** MATE's lead wizard: let prospects *build and test* a live AI agent on your marketing site, then capture them as a lead

---

Most "book a demo" buttons are a dead end — the prospect never sees the product. So MATE has an embeddable **Agent Builder Wizard**: a step-by-step widget you drop on your marketing site where a visitor builds, **actually chats with**, and then requests an AI agent. It provisions a real, live trial agent on the spot, then captures the visitor's details as a lead. No billing anywhere — it ends with a price estimate and a "we'll contact you," and you follow up to activate.

## How it works

1. The visitor picks a **tier** and fills in a bit of config (e.g. their website URL).
2. MATE provisions an **isolated trial project**, builds the agent(s) from that tier's template, and issues a widget key.
3. The wizard embeds the standard chat widget right there, so the visitor **tests their own agent immediately**.
4. It captures name / email / company as a **lead** (with the tier, the shown price estimate, and which site they came from), and trial projects auto-clean-up after a TTL.

## The tiers

- **Tier 1 — Website Support:** reads the prospect's own website (via the browser tool) and answers support questions about it. Live trial.
- **Tier 2 — Support + Scheduling:** everything in Tier 1 plus Google Calendar availability/booking. Live trial.
- **Tier 3 — Sales:** shows products, fills a cart, and places orders via an e-commerce integration. Trial runs on a built-in demo shop.
- **Tier 4 — Custom:** the visitor picks the MATE capabilities they care about and describes the need → forwarded as a structured lead.

## The nice touches

- **Site analysis** — for website tiers, MATE crawls the prospect's site and runs an LLM to extract a short business summary + a list of services, then injects those into the trial agent. So the agent already "knows the business" before the prospect types a word.
- **Per-partner pricing & origins** — every site that embeds the wizard can be a "partner" with its own prices, currency, contact email, and allowed domains, all managed from **Dashboard → Wizard Pricing** with no code change or restart.
- **One-line embed** — `<script src=".../wizard/mate-wizard.js" data-server="..." data-target="mate-wizard">`, with `data-*` options for tier, language (en/sr), currency and partner. Auto-resizes.
- **Leads dashboard** — every captured lead, its tier, price snapshot and source partner, in one view.

## Why I like it as a pattern

It flips lead-gen: instead of "trust us, it's good," the prospect experiences a working agent tailored to their business in ~2 minutes, and you get a warm lead that already knows what they're buying. Same platform, same templates, same widget you'd ship to the customer anyway.

Happy to go deeper on the trial provisioning / auto-cleanup or the site-analysis injection if anyone's building something similar.
