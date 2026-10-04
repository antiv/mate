---
title: Agent Builder Wizard and shop orders
summary: Let prospects build and try an agent on your website, then follow up on the leads and orders that come in.
audience: user
order: 160
covers:
  - server/wizard_routes.py
  - shared/utils/wizard/**
  - static/js/wizard/**
  - templates/dashboard/wizard_leads.html
  - templates/dashboard/wizard_pricing.html
  - templates/dashboard/wizard_orders.html
  - shared/utils/shop_service.py
---

# Agent Builder Wizard and shop orders

The wizard is a step-by-step form you embed on a marketing site. A visitor picks the
kind of agent they want, MATE builds a working trial agent for them on the spot, they
chat with it, and then leave their contact details. You get a lead; they get a price
estimate and a contact address. There is no billing in the wizard: activating a
customer is a conversation you have afterwards.

## What visitors can choose

| Tier | The trial agent | Trial |
|---|---|---|
| **Website Support** | Reads the visitor's own website and answers questions about it. | yes |
| **Support + Scheduling** | The same, plus checking availability and booking in a calendar. | yes, if a Google service account is configured on the server |
| **Sales** | Shows products, fills a cart and takes orders, using a demo shop. | yes |
| **Custom** | None. The visitor describes what they need. | no, lead only |

For tiers with a website, MATE reads a few pages of the visitor's site and writes the
trial agent's description and instruction from what it finds, so the first answers
are already about their business.

## Put the wizard on a site

```html
<div id="mate-wizard"></div>
<script
  src="https://your-mate.example.com/wizard/mate-wizard.js"
  data-server="https://your-mate.example.com"
  data-target="mate-wizard"
></script>
```

| Attribute | Effect |
|---|---|
| `data-tier` | Start on one tier (`tier1` to `tier4`) and skip the chooser. |
| `data-lang` | Language of the wizard: `en` or `sr`. |
| `data-currency` | Currency for the estimate, for example `EUR` or `RSD`. |
| `data-partner` | The partner this site belongs to; see below. |
| `data-contact-email` | The contact address shown at the end. |

## Prices and partners

**Control Room → Wizard Pricing** sets the estimates the wizard shows. They are
display text only, for example `€49` or `5.900 RSD`.

- **Global default** applies to every site.
- A **partner** is one site that embeds the wizard. Create one to give that site its
  own prices, contact email, default language and **Allowed origins**. The site
  refers to its partner with `data-partner`.
- With allowed origins set, the wizard only works when embedded on those addresses.
  This relies on the visitor's browser and is not a security boundary.
- Add a currency with **Add currency**. An empty price falls back to the default
  currency.

The estimate a visitor saw is saved with their lead, so later price changes do not
rewrite history.

## Follow up on leads

**Control Room → Wizard Leads** lists everyone who completed the wizard: contact,
company, partner, tier, the estimate they were shown, and their trial.

- Open a lead's trial to see the agent they built and tried, while the trial still
  exists.
- Move a lead through **New → Contacted → Converted**, or **Archived**.

## Trials are temporary

Each trial is its own project with its own agent and widget key. Trials are removed
automatically after seven days (two for Sales trials), in a nightly cleanup. The
lead stays. To keep what a prospect built, copy it out before it expires; one way is
**Clone to Project** on the Agents page, into a permanent project.

## Shop orders

An agent with the Shop tool takes orders in conversation. Orders from agents whose
shop configuration has a `partner_key` are listed in **Control Room → Shop Orders**,
grouped by partner. As with leads, MATE records the order and you fulfil it: move
each one through **New → Contacted → Fulfilled**, or **Cancelled**.

If the shop configuration has a `vendor_email`, each order is also emailed there.
See *Shop* in [Tools and MCP servers](tools-and-mcp.md).
