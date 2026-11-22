---
title: "Dashboard components"
summary: The m- classes, the shared table and the dialog helper that new dashboard pages are built from.
audience: dev
order: 46
covers:
  - static/css/components.css
  - static/js/data-table.js
  - static/js/dialogs.js
  - static/js/tabs.js
  - templates/dashboard/triggers.html
  - templates/dashboard/modals/trigger_modal.html
  - templates/dashboard/agent_page.html
  - static/js/agent-page.js
  - static/css/agent-page.css
  - static/js/monaco-fields.js
---

# Dashboard components

The dashboard's colours are tokens in `static/css/redesign.css`, defined once for
the light theme and once for the dark one. Older pages use Tailwind classes, and
`redesign.css` maps those classes onto the tokens. A class the map misses keeps
Tailwind's colour, so it looks wrong in one of the themes.

New markup uses the `m-` classes of `static/css/components.css` instead. Every
colour in them is a token, so both themes are right without a remap. The prefix
keeps them apart from Tailwind's classes. The plan they come from is
`documents/design/dashboard-2.1.md`, "Components".

`base.html` loads `components.css`, `data-table.js`, `dialogs.js` and `tabs.js` on every page.

Two pages are built on them alone; copy from them:

- **Triggers:** a table with filters, a form dialog and a banner
  (`templates/dashboard/triggers.html`, `templates/dashboard/modals/trigger_modal.html`,
  `static/js/triggers-page.js`).
- **Approvals:** tabs, a client-side table and one the API pages, and a dialog of
  details (`templates/dashboard/approvals.html`, `static/js/approvals-page.js`).
- **The agent page:** a form in three columns, collapsible sections, pickers, a menu
  and a save bar ([below](#the-agent-page)).

## The classes

| Class | What it is |
|---|---|
| `m-page-head`, `m-page-title`, `m-page-subtitle` | A page's title row, with its main button on the right |
| `m-section-title`, `m-section-subtitle` | A heading inside a page |
| `m-card`, `m-card-body` | A surface with a border, rounded corners and the card shadow |
| `m-btn` + `m-btn-primary` / `-secondary` / `-danger` / `-ghost` | Every button. `m-btn-sm` is smaller, `m-btn-icon` square for an icon alone. `m-btn-ghost m-btn-danger` is a row's delete |
| `m-field` | A label and its control in a column. `m-hint` and `m-error` go under the control; `is-invalid` on the field marks the control |
| `m-input` | Input, select and textarea. `m-input-sm` for toolbars, `m-input-mono` for code |
| `m-check` | A label around a checkbox and its text |
| `m-stack`, `m-row`, `m-grid-2`, `m-divider` | Layout: a column with gaps, a wrapping row, two columns that stack on a phone, a rule |
| `m-badge` + `-accent` / `-success` / `-warning` / `-danger` / `-violet` | A short label in a pill |
| `m-count` | A number in a pill, beside a tab or a sidebar item |
| `m-link` | A button that reads as a link, such as a name in a table that opens its row |
| `m-details` | A `<dl>` of labels and values in two columns |
| `m-pre` | A block of code or JSON, scrolled when long |
| `m-spacer` | Takes the free space in a row, such as between a link and the buttons of a dialog's foot |
| `m-tabs`, `m-tab` | Tabs; see below |
| `m-section` | A collapsible part of a form: `<details class="m-section">` with a `<summary>` holding an icon, the title and an `m-section-state` (a word such as `3 on`; `is-set` colours it), then an `m-section-body` |
| `m-chips`, `m-chip` | The values of a picker, each with a remove button; an empty `m-chips` takes no space |
| `m-menu`, `m-menu-list` | A menu of links and buttons: `<details class="m-menu">` whose `<summary>` is the button |
| `m-savebar` | A bar pinned to the bottom of the page with **Discard** and **Save**, clear of the Help button |
| `m-switch` | An on/off switch: `<button class="m-switch" role="switch" aria-checked="true" aria-label="…">` |
| `m-notice`, `m-notice-warning`, `m-notice-floating` | An icon and a message on a tinted background; floating puts it at the bottom of the screen |
| `m-code`, `m-mono`, `m-muted`, `m-tone-*` | Inline code, monospace text, quieter text, text in a token's colour |
| `m-dialog-backdrop`, `m-dialog` (+ `-lg`, `-plain`) | A dialog; see below |
| `m-table` and the `m-table-*` classes | The shared table; `data-table.js` writes them |

Rules for new markup:

- **No Tailwind colour classes**, and no `style=` attributes. If something is
  missing, add a class to `components.css`, on the tokens.
- **No `on*=` attributes:** use `data-click` and the other actions of
  `csp-actions.js` ([Content-Security-Policy](content-security-policy.md)).
- **Hide with the `hidden` attribute** (`el.hidden = true`). `components.css` makes
  `[hidden]` win over any `display` a class sets.
- **Every control gets a label**: `<label for>` in an `m-field`, or
  `aria-label` on an icon button, a switch or a toolbar select.

Tailwind's base styles give `[type='button']` a transparent background, at the same
specificity as one class and later in the page. That is why the colours of
buttons and switches are set on two classes (`.m-btn.m-btn-primary`). A new
component that colours a `<button>` needs the same.

## The table

`static/js/data-table.js` defines `MateTable`. Give it an empty element and the
columns. It builds the toolbar, the table in an `m-card`, and a footer with the
count and the pager:

```js
const table = new MateTable(document.getElementById('triggerTable'), {
    noun: ['trigger', 'triggers'],
    label: 'Triggers',                                   // the table's aria-label
    empty: 'No triggers yet. Click New Trigger to create one.',
    filters: document.getElementById('triggerFilters'),  // moved into the toolbar
    filter: (t) => !type() || t.trigger_type === type(),
    columns: [
        { key: 'name', label: 'Name', render: (t) => `<b>${MateTable.esc(t.name)}</b>` },
        { key: 'output_type', label: 'Output', value: outputLabel },
        { key: 'last_fired_at', label: 'Last Run', searchable: false },
        { label: 'Actions', align: 'right', render: actions },
    ],
});
table.setRows(triggers);
```

A column:

| Option | Meaning |
|---|---|
| `key` | The field. A column without one is not sorted or searched (actions) |
| `label` | The header text |
| `value(row)` | What the column sorts and searches by. Default `row[key]` |
| `render(row)` | The cell's HTML. Default the value, escaped. **It must escape what it puts in**, with `MateTable.esc` |
| `search(row)` | Text to search instead of the value, such as a name and its description |
| `sortable: false`, `searchable: false` | Leave the column out of sorting or searching |
| `align` | `right` or `center` |
| `className` | Classes on each cell: `m-cell-strong`, `m-mono` |

The table:

- **Search** matches rows that contain every word typed, in any case, in any
  searchable column. Escape clears it. When nothing matches, the table says so
  and offers to clear the search.
- **Sorting:** a click on a header sorts by that column, and a second click
  reverses it. The header gets `aria-sort`. Numbers sort by value and text in
  natural order (`item 2` before `item 10`). Empty values go last either way, and
  rows that compare equal keep their order.
- **Pages** of `pageSize` rows (25 by default). The footer shows "3 triggers", or
  "26–50 of 80 triggers" with the pager. It sits inside the card, so the
  Help button (`main.dashboard-main` keeps clear of it) does not cover it.
- **`setRows(rows)`** sets the whole list; the search, the filter, the sort and the
  pages are worked out in the browser.
- **`reload()`** applies a changed `filter`, as the Triggers type filter does.
  In fetch mode it asks the API again for the same page; `reload({ firstPage: true })`
  starts from the first, as after a filter changed.
- **`setError(message)`** shows the failure in the table.

`search: false` drops the search box, for a short list such as the system
tasks.

A list the API pages passes `fetch` instead of calling `setRows`. The table calls
it with the page, the search (sent 300 ms after typing stops) and the sort. It
expects the page's rows and the total back:

```js
new MateTable(el, {
    columns,
    fetch: async ({ offset, limit, q, sort, dir }) => {
        const r = await fetch(`/dashboard/api/approvals?offset=${offset}&limit=${limit}`);
        const data = await r.json();
        return { rows: data.requests, total: data.total };
    },
});
```

A response that arrives after a newer request was sent is dropped.

## Dialogs

`static/js/dialogs.js` opens and closes dialogs written with the `m-dialog`
classes:

```html
<div id="triggerModal" class="m-dialog-backdrop" hidden>
    <div class="m-dialog m-dialog-lg" role="dialog" aria-modal="true" aria-labelledby="triggerModalTitle">
        <div class="m-dialog-head"><h3 id="triggerModalTitle" class="m-dialog-title">…</h3> close button</div>
        <div class="m-dialog-body">…</div>
        <div class="m-dialog-foot">Cancel · Save</div>
    </div>
</div>
```

```js
mateDialog.open('triggerModal', { focus: 'triggerName' });
mateDialog.close('triggerModal');
```

- **On open,** focus moves to `focus`, or else to the first field in the body.
- **While open,** Tab stays inside the dialog.
- **Escape, or a click on the backdrop,** closes it. A drag that starts in a field and
  ends on the backdrop does not.
- **On close,** focus returns to what had it before.
- **Dialogs over dialogs:** Escape and the backdrop close the one on top.
- **`onClose(result)`** in the options is called once the dialog closes.
- **`m-dialog-lg`** is for a form. On a phone it takes the whole screen.

`showConfirm(message, title, confirmText, cancelText, type)` is the dashboard's
question dialog, built on the same classes. It resolves to `true` or `false`.
The message and the title go in as text, not HTML. With `type` `danger`, the
confirm button is red and focus starts on Cancel. `cancelText` `null` shows a
single button.

## Tabs

`static/js/tabs.js` sets up every `[data-m-tabs]` on the page:

```html
<div class="m-tabs" role="tablist" data-m-tabs aria-label="Approvals">
    <button type="button" class="m-tab" role="tab" id="tab-waiting" aria-controls="panel-waiting" aria-selected="true">Waiting</button>
    <button type="button" class="m-tab" role="tab" id="tab-history" aria-controls="panel-history" aria-selected="false">History</button>
</div>
<div id="panel-waiting" role="tabpanel" aria-labelledby="tab-waiting">…</div>
<div id="panel-history" role="tabpanel" aria-labelledby="tab-history" hidden>…</div>
```

- **A click,** or the arrow keys, Home and End on a focused tab, selects it. Its
  panel is shown and the others hidden.
- **Only the selected tab** is in the Tab order.
- **The tab list fires `m-tab-change`** with `detail.id`, the selected tab's id, when
  the selection changes. The Approvals page reloads a tab's list then.
- **`mateTabs.select('tab-history')`** selects one from code.

## The agent page

`/dashboard/agents/<name>` edits an agent; `/dashboard/agents/new` makes one, and
`?from=<name>` fills it from another (Copy). Both routes come after
`/dashboard/agents/visual` in `dashboard_server.py`, which they would otherwise take,
and answer 404 for an agent of another workspace. `_agent_page()` renders
`templates/dashboard/agent_page.html` with:

- the agent, from `_get_all_agent_configs`, so its stored model key is
  `__stored__`, as on every page;
- the workspace's other agents, for the parent picker;
- `workspaces.roles_in_use()`: the agent role names its members and agents use,
  with how many members hold each, without the platform roles.

The page's state is a JSON `<script id="agent-page-data">`; `static/js/agent-page.js`
runs it as `window.mateAgentPage`.

- **The fields have the prefix `agent`** (`agentName`, `agentToolConfig`, ...),
  which the helpers in `agent-forms.js`, `agent-tools.js` and `agent-config.js`
  take.
- **The right-hand sections hold the configuration fields** of
  `config_modals.html`: each dialog's fields are a macro there
  (`tool_config_fields`, `guardrail_fields`, ...), which the Visual Builder's
  dialogs and the agent page's sections both call. The ids are the same either
  way. The tool, planner and generation listeners keep the controls and the JSON
  in step as you type; `agent-page.js` does it for the guardrails, whose dialog
  builds its JSON only on Save. Inside a section the fields' `md:` columns are one
  column (`agent-page.css`).
- **Saving** posts the same form fields the agents API always took: `POST
  /dashboard/api/agents`, or `PUT /dashboard/api/agents/<id>`. The page then
  reloads at the agent's address, which a rename changes, and shows "Saved." from
  `sessionStorage`.
- **The save bar and the question on leaving** compare the form, as the API would
  receive it, with what it held after loading.
- **The instruction** is a Monaco editor (`static/js/monaco-fields.js`, which the
  Visual Builder loads too); the textarea under it stays while Monaco loads, or if
  it doesn't.
- **File Search** reads the agent from `<prefix>Name` and `<prefix>Project` of the
  form that opened it, `agent` here and `vb` in the Visual Builder.

## Tests

- `shared/test/js/data_table.test.js` covers searching, sorting, paging and
  escaping. It runs with node's test runner:
  `node --test shared/test/js/data_table.test.js`.
- `shared/test/test_static_js_units.py` runs it with the other tests, and is
  skipped where node is not installed.
- `shared/test/test_csp.py` checks that the component scripts and the Triggers,
  Approvals and agent page templates have no inline handlers.
- `shared/test/test_agent_page.py` covers the agent page's routes across two
  workspaces, the parent picker, the stored key and `roles_in_use`.
