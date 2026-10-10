---
title: "Content-Security-Policy"
summary: What the CSP allows, how to move from report-only to enforce, and how to add a host.
audience: dev
order: 44
covers:
  - server/csp.py
  - static/js/csp-actions.js
  - static/js/workroom-canvas-frame.js
---

# Content-Security-Policy

MATE sends a Content-Security-Policy (CSP) with every HTML page. A CSP limits
the damage a cross-site scripting bug can do. Even if an attacker gets a script
into a page, the browser refuses to load code from a host the policy does not
list, to send data to one, or to let another site frame the dashboard.

## What the policy allows

| Directive | Allows |
|---|---|
| `script-src` | `'self'`, inline scripts that carry the response's nonce (see below), WebAssembly (`'wasm-unsafe-eval'`, for Pyodide), and the CDNs the templates use: `cdn.tailwindcss.com`, `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `unpkg.com`, `d3js.org` |
| `style-src` | `'self'`, inline styles, `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `fonts.googleapis.com` |
| `font-src` | `'self'`, `data:`, `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `fonts.gstatic.com` |
| `connect-src` | `'self'` (including the Work Room's WebSocket), `cdn.jsdelivr.net`, `cdnjs.cloudflare.com` |
| `img-src` | `'self'`, `data:`, `blob:`, any `https:` host (answers show images from anywhere) |
| `worker-src` | `'self'`, `blob:` (Monaco, Ace and Pyodide workers) |
| `frame-src` | `'self'`, `dartpad.dev` (the Work Room's Dart runner) |
| `object-src` | nothing |
| `base-uri` | `'self'` |
| `frame-ancestors` | `'self'` for the dashboard. For the widget chat page, see below |

## Two policies while pages are converted

An injected script is stopped only if the policy has no `'unsafe-inline'` for
scripts. Many templates still have `onclick=` handlers, which need it. So there
are two versions of the policy, which differ only in `script-src`:

- **Strict:** inline `<script>` blocks run only with this response's nonce
  (`'nonce-…'`), and inline handlers (`onclick=`, `javascript:` URLs) do not
  run at all.
- **Legacy:** `'unsafe-inline'` instead of the nonce. Every page works with it.

| `CSP_MODE` | Header(s) sent |
|---|---|
| `report-only` (default) | the strict policy as `Content-Security-Policy-Report-Only` |
| `enforce` | the legacy policy as `Content-Security-Policy`, plus the strict one as `-Report-Only` |
| `off` | none |

So under `enforce`, nothing that works today breaks, and the log names the pages
that still have inline code. Once every template is converted, `enforce` will
enforce the strict policy, and `'unsafe-inline'` goes away.

### The nonce

The middleware (`add_csp_header`) makes a new nonce for every request before the
page renders, and puts it in `request.state.csp_nonce`. A template gives it to
each inline script:

```html
<script nonce="{{ request.state.csp_nonce }}">
```

A page that sets its own policy through `set_csp_header` (the widget chat page)
passes the request, so its policy carries the same nonce.

### Replacing inline handlers

`static/js/csp-actions.js` (loaded by `base.html`) handles clicks, changes,
input, keyup and submits for the whole page through event delegation:

```html
<button data-click="closeModal">                          <!-- closeModal() -->
<button data-click="controlAdkServer" data-args='["start"]'>
<a href="#" data-click="showTokensModal" data-args='["$event"]'>
<select data-change="applyFilters">
```

- `data-args` is a JSON array. `"$event"` becomes the event, `"$el"` the
  element and `"$value"` its value (what `this.value` was). In a Jinja template, build it with `tojson` inside single quotes:
  `data-args='{{ [agent.name] | tojson }}'`. In HTML built by JavaScript, use
  `data-args="${mateActions.attr([t.id, t.name])}"`, which escapes it for the
  attribute. Values stay data, so a name with a quote in it cannot break out
  into code, as it could inside an `onclick="…('${name}')"` string.
- The function runs with `this` set to the element. Nested handlers run
  innermost first, and `event.stopPropagation()` stops the outer ones.
  `data-click="stop"` is the built-in for `onclick="event.stopPropagation()"`.
  `data-stop` on an element stops propagation after its handler runs, and
  `data-prevent` calls `preventDefault()` before it, for handlers that began
  with `event.stopPropagation();` or `event.preventDefault();`.
  It only stops other `data-click` handlers: a listener added with
  `addEventListener` on an outer element has already run by then.
- Only functions the page allows can be called:
  `mateActions.allow('closeModal', 'sessionsApp.loadSessions')`. A script file
  allows the functions it defines, at its top, so every page that loads it gets
  them; a page's inline script allows the ones it defines itself. Markup that
  gets injected into a page therefore cannot call an arbitrary global. Names are
  resolved when the event fires, so a page may allow a function before the
  script that defines it has run.
- For HTML that JavaScript creates and keeps a reference to, adding a listener
  (`el.addEventListener('click', …)`) is simpler than `data-click`. The same
  goes for a modal that closes on a click on its backdrop
  (`onclick="if (event.target === this) …"`): add a listener that checks
  `event.target`.
- Names are looked up on `window`. A page object declared with a top-level
  `const` (`const AlertPage = …`) is not on `window`, so assign it there
  (`window.AlertPage = AlertPage`) before its methods can be actions.

### Converting a page

1. Put the nonce on each inline `<script>`.
2. Replace every `on*=` attribute in the template, and in HTML its scripts
   build, with `data-*` actions or listeners, and allow the functions it calls.
3. Replace `href="javascript:…"` with a button and an action.
4. Add the template to `CONVERTED` in `shared/test/test_csp.py`, and a script
   that builds HTML to `CONVERTED_JS`. The test checks they stay free of inline
   handlers and un-nonced scripts.
5. Bump the `?v=` of every script file you changed, in each template that
   loads it, so browsers do not pair the new markup with a cached old script.
6. Open the page in a browser with the strict policy enforced, use what you
   changed, and check that no `securitypolicyviolation` events fire. Also check
   that every `data-*` action in the DOM can run, with `mateActions.can(name)`,
   after opening each modal, since some markup only exists then.

Converted so far:

- `base.html`, `login.html`, `dashboard/index.html`
- the Agents page and the Visual Builder (`dashboard/agents.html`,
  `dashboard/agents_visual.html`), the modals they include, and the scripts
  that build their markup (`agent-management.js`, `modals/file-search.js`,
  `modals/memory-blocks.js`, `modals/version-history.js`,
  `modals/widget-keys.js`)
- the monitoring pages: alerts, audit logs, guardrail logs, sessions, traces,
  rate limits, integrations and triggers (with `modals/trigger_modal.html`),
  and the scripts that build their markup (`alerts-page.js`, `sessions.js`,
  `traces.js`, `triggers-page.js`)
- evals, usage, users, migrations, docs, the template gallery
  (`dashboard/templates.html`, `template-gallery.js`) and the wizard pages
  (`wizard_leads.html`, `wizard_orders.html`, `wizard_pricing.html`)
- the Work Room (`dashboard/workroom.html`) and the chat script it loads
  (`standalone/chat.js`), the widget chat and admin pages (`widget/chat.html`,
  `widget/admin.html`, `widget/admin.js`) and the standalone chat page

Every page is converted. `widget/admin.html` does not extend `base.html`, so it
loads `csp-actions.js` itself. `standalone/chat.js` runs on pages that may not
load it, so its "Open in Canvas" button has a listener of its own.

## The Work Room canvas

The canvas runs code an agent wrote: HTML with inline scripts and `onclick=`,
libraries from any CDN. A `srcdoc` iframe would inherit the Work Room's strict
policy and block all of that, so the code runs on a page of its own,
`/dashboard/workroom/canvas`, with its own policy (`canvas_policy()`):

- `sandbox allow-scripts allow-modals`. The browser gives the page an opaque
  origin however it is opened, in the canvas iframe or in a tab of its own, so
  the code cannot reach the dashboard's cookies, storage or DOM.
- Inline scripts, `'unsafe-eval'` and any `https:` source are allowed, for the
  agent's code.
- `frame-ancestors 'self'`: only MATE may frame it.
- It is enforced in every `CSP_MODE`, including `off`, since the sandbox is what
  makes the page safe to serve.

The page is empty. Its inline script (`workroom-canvas-frame.js`, inlined
because Chrome refuses a sandboxed page's requests to a loopback or private
address) says it is ready to the window that framed or opened it, and writes
the document that window sends back. It listens only to that window, and only
on MATE's own origin.

"Open in new tab" opens the same page. It used to open the code as a `blob:`
URL, which ran it on MATE's origin with no sandbox.

Python is different: Pyodide needs workers that a sandbox breaks, so it runs in
a `srcdoc` iframe under the Work Room's policy. The iframe loads Pyodide from a
listed CDN and the runner from `static/js/workroom-python.js`. The code is a
`<script type="application/json">` element, data the browser never runs, so no
inline script is needed.

## Also allowed

- **`eval()` on ADK's dev UI.** A library bundled into ADK's dev UI
  (`/dev-ui/`, admins only) calls `new Function()`. Only pages under `/dev-ui/`
  get `'unsafe-eval'`, and only while the dev UI is served: it is off by
  default when `MATE_ENV=production` (`ADK_DEV_UI` overrides that). MATE's own
  pages never get it.

  The dev UI's markup is ADK's, so it gets the legacy policy only, with no
  nonce and no strict report.

## Violation reports

In report-only mode the browser blocks nothing. It reports what it would have
blocked to `/csp-report`, and the server logs each distinct violation once, as a warning:

```
WARNING [server.csp] CSP violation: script-src-elem blocked 'https://cdn.example.com/lib.js' on /dashboard/agents
```

Query strings are removed from the logged URLs, since a page URL can carry a
widget key. The endpoint needs no login, as browsers send reports without one,
so its input is treated as hostile:

- A report body over 16 KB is refused before it is read.
- Logging works in 10-minute windows. Each distinct violation is logged once
  per window, and at most 100 are logged per window. When a window ends, the
  log says how many reports it dropped. A flood of fake reports can therefore
  hide real ones for one window at most, not until a restart.

An inline handler on a page that is not converted yet is logged as
`script-src-attr blocked 'inline'`, and an inline script without the nonce as
`script-src-elem blocked 'inline'`. Once the log stays quiet in normal use,
set `CSP_MODE=enforce`.

## The widget

Customer sites frame the widget chat page (`/widget/chat`), so it cannot use
the dashboard's `frame-ancestors 'self'`. Its `frame-ancestors` lets through
the same sites as the widget key's origin check (`WIDGET_ORIGIN_STRICT`):

- No allowlist, or `WIDGET_ORIGIN_STRICT` off: any site may frame it
  (`frame-ancestors *`). With strict mode off, a site that is not on the
  allowlist is only logged, so framing does not block it either.
- An allowlist with `WIDGET_ORIGIN_STRICT=true`: the allowlist's origins, plus
  MATE itself for the previews in the dashboard and the widget admin panel.
  Entries mean what they mean to the origin check:
  - `https://shop.example.com` covers that scheme, host and port.
  - `https://*.example.com` covers its subdomains and `example.com` itself.
  - `*.example.com` covers its subdomains and `example.com` itself, on any
    scheme and port.
- An entry that is not a plain origin (it has a path, credentials, spaces or
  `;`) is left out rather than copied into the header. So is an IPv6 address,
  which a CSP cannot name; use a host name for such a site.
- The widget's error pages ("Invalid widget key", "not enabled for this site")
  may be framed by any site, so the embedding page shows the message rather
  than a frame the browser refused.

`frame-ancestors` adds one thing the origin check cannot do. The origin check
reads the embedding page from the `Referer` header, which a page can withhold
(`referrerpolicy="no-referrer"`), and a request without one is let through.
The browser enforces `frame-ancestors` whatever the page sends. So with
`CSP_MODE=enforce` and `WIDGET_ORIGIN_STRICT=true`, a site that is not on the
allowlist cannot frame the widget even when it hides its referrer.

## Allowing another host

If a deployment loads scripts, styles, fonts or frames from another host, for
example a self-hosted copy of a library or an extra CDN, list it in
`CSP_EXTRA_SOURCES`. Separate hosts with commas or spaces:

```
CSP_EXTRA_SOURCES=https://cdn.example.com,https://*.mycompany.com
```

The hosts are added to `script-src`, `style-src`, `font-src`, `connect-src` and
`frame-src`. A value that is not a host source is ignored with a warning.

## Known gaps

- **Python in the Work Room canvas** runs without a sandbox, on MATE's origin
  (see above). Python code can reach the page through Pyodide's `js` module.
- **Standalone builds** (`standalone_server.py`) do not send this policy yet.
