---
title: "Content-Security-Policy"
summary: What the CSP allows, how to move from report-only to enforce, and how to add a host.
audience: dev
order: 44
status: migrated
covers:
  - server/csp.py
---

# Content-Security-Policy

MATE sends a Content-Security-Policy (CSP) with every HTML page. A CSP limits
the damage a cross-site scripting bug can do. Even if an attacker gets a script
into a page, the browser refuses to load code from a host the policy does not
list, to send data to one, or to let another site frame the dashboard.

## What the policy allows

| Directive | Allows |
|---|---|
| `script-src` | `'self'`, inline scripts, WebAssembly (`'wasm-unsafe-eval'`, for Pyodide), and the CDNs the templates use: `cdn.tailwindcss.com`, `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `unpkg.com`, `d3js.org` |
| `style-src` | `'self'`, inline styles, `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `fonts.googleapis.com` |
| `font-src` | `'self'`, `data:`, `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `fonts.gstatic.com` |
| `connect-src` | `'self'` (including the Work Room's WebSocket), `cdn.jsdelivr.net`, `cdnjs.cloudflare.com` |
| `img-src` | `'self'`, `data:`, `blob:`, any `https:` host (answers show images from anywhere) |
| `worker-src` | `'self'`, `blob:` (Monaco, Ace and Pyodide workers) |
| `frame-src` | `'self'`, `dartpad.dev` (the Work Room's Dart runner) |
| `object-src` | nothing |
| `base-uri` | `'self'` |
| `frame-ancestors` | `'self'` for the dashboard. For the widget chat page, see below |

Two things are still allowed that a strict policy would forbid:

- **Inline scripts and handlers (`'unsafe-inline'`).** The templates have many
  inline `<script>` blocks and `onclick=` handlers. Removing them page by page
  and adding nonces is the next step.
- **`eval()` on ADK's dev UI.** A library bundled into ADK's dev UI
  (`/dev-ui/`, admins only) calls `new Function()`. Only pages under `/dev-ui/`
  get `'unsafe-eval'`, and only while the dev UI is served: it is off by
  default when `MATE_ENV=production` (`ADK_DEV_UI` overrides that). MATE's own
  pages never get it.

## Report-Only first

By default the policy is sent as `Content-Security-Policy-Report-Only`. The
browser blocks nothing. It reports what it would have blocked to
`/csp-report`, and the server logs each distinct violation once, as a warning:

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

Once the log stays quiet in normal use, switch to enforcing:

| `CSP_MODE` | Effect |
|---|---|
| `report-only` (default) | Report violations, block nothing |
| `enforce` | Block violations, and still report them |
| `off` | Send no policy |

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

- **Work Room canvas.** Code an agent writes is run in the Work Room in a
  sandboxed `srcdoc` iframe, and such an iframe inherits the page's policy. A
  canvas that loads a library from an unlisted CDN (for example
  `cdn.plot.ly`) shows up as a violation. Under `enforce`, that script is
  blocked. Add the host to `CSP_EXTRA_SOURCES` if your agents rely on it.
- **Standalone builds** (`standalone_server.py`) do not send this policy yet.
