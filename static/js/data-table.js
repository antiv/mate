/**
 * The shared table (documents/design/dashboard-2.1.md, "Components"): search,
 * sorting, pagination and an empty state, on the m-table classes of
 * static/css/components.css. See docs/dev/dashboard-components.md.
 *
 *   const table = new MateTable(document.getElementById('triggerTable'), {
 *       columns: [
 *           { key: 'name', label: 'Name', render: (t) => `<b>${MateTable.esc(t.name)}</b>` },
 *           { key: 'last_fired_at', label: 'Last run' },
 *           { label: 'Actions', align: 'right', render: actions },   // no key: not sorted or searched
 *       ],
 *       noun: ['trigger', 'triggers'],
 *       empty: 'No triggers yet.',
 *       filters: document.getElementById('triggerFilters'),       // moved into the toolbar
 *       filter: (t) => !type || t.trigger_type === type,           // client side, with search
 *   });
 *   table.setRows(triggers);      // a list the page loads whole: searched, sorted and paged here
 *
 * A list the API pages passes fetch instead of setRows, and gets the page, the search and
 * the sort to send it:
 *
 *   fetch: async ({ offset, limit, q, sort, dir }) => ({ rows, total })
 *
 * A column's value(row) is what it sorts and searches by (default row[key]); render(row)
 * returns the cell's HTML (default: the value, escaped). render must escape what it puts in.
 */
(function (root) {
    'use strict';

    function esc(value) {
        if (value === null || value === undefined) return '';
        return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function isBlank(v) {
        return v === null || v === undefined || v === '';
    }

    /** The rows whose text holds every word of the query, in any case. */
    function filterRows(rows, query, textOf) {
        const words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
        if (!words.length) return rows.slice();
        return rows.filter((row) => {
            const text = String(textOf(row) || '').toLowerCase();
            return words.every((w) => text.includes(w));
        });
    }

    /** Numbers by value, text in natural order ("item 2" before "item 10"); blanks go last either way. */
    function compareValues(a, b) {
        if (typeof a === 'boolean') a = a ? 1 : 0;
        if (typeof b === 'boolean') b = b ? 1 : 0;
        if (typeof a === 'number' && typeof b === 'number') return a - b;
        return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
    }

    /** A sorted copy; rows that compare equal keep their order. */
    function sortRows(rows, valueOf, dir) {
        const sign = dir === 'desc' ? -1 : 1;
        return rows
            .map((row, index) => ({ row, index, value: valueOf(row) }))
            .sort((x, y) => {
                const xb = isBlank(x.value);
                const yb = isBlank(y.value);
                if (xb || yb) return xb === yb ? x.index - y.index : (xb ? 1 : -1);
                return sign * compareValues(x.value, y.value) || x.index - y.index;
            })
            .map((entry) => entry.row);
    }

    function pageCount(total, pageSize) {
        return Math.max(1, Math.ceil(total / pageSize));
    }

    function clampPage(page, total, pageSize) {
        return Math.min(Math.max(1, page), pageCount(total, pageSize));
    }

    /** "1–25 of 80 triggers", or "3 triggers" when they fit on one page. */
    function rangeText(page, pageSize, total, noun) {
        const word = total === 1 ? noun[0] : noun[1];
        if (total <= pageSize) return `${total} ${word}`;
        const from = (page - 1) * pageSize + 1;
        const to = Math.min(page * pageSize, total);
        return `${from}–${to} of ${total} ${word}`;
    }

    class MateTable {
        constructor(container, options) {
            this.container = container;
            this.columns = options.columns;
            this.pageSize = options.pageSize || 25;
            this.noun = options.noun || ['row', 'rows'];
            this.empty = options.empty || `No ${this.noun[1]} yet.`;
            this.filter = options.filter || null;
            this.fetcher = options.fetch || null;
            this.label = options.label || '';
            this.searchable = options.search !== false;
            this.placeholder = (options.search && options.search.placeholder) || `Search ${this.noun[1]}`;
            this.sort = options.sort ? { ...options.sort } : null;
            this.page = 1;
            this.query = '';
            this.rows = [];
            this.total = 0;
            this.loaded = false;
            this.message = null;
            this._seq = 0;
            this._timer = null;
            this._build(options.filters);
            if (this.fetcher) this.reload();
            else this.setLoading();
        }

        // ── public ─────────────────────────────────────────────

        /** The page's whole list; searched, sorted and paged here. */
        setRows(rows) {
            this.rows = Array.isArray(rows) ? rows : [];
            this.loaded = true;
            this.message = null;
            this.render();
        }

        /** Ask the API again (fetch mode), or apply a changed filter to the rows (client mode). */
        reload() {
            if (!this.fetcher) {
                this.page = 1;
                this.render();
                return Promise.resolve();
            }
            const seq = ++this._seq;
            if (!this.loaded) this.setLoading();
            const col = this.sort && this._column(this.sort.key);
            return Promise.resolve(this.fetcher({
                page: this.page,
                pageSize: this.pageSize,
                offset: (this.page - 1) * this.pageSize,
                limit: this.pageSize,
                q: this.query,
                sort: col ? col.key : null,
                dir: col ? this.sort.dir : null,
            })).then((result) => {
                if (seq !== this._seq) return;   // a later request is on its way
                this.rows = (result && result.rows) || [];
                this.total = (result && result.total) || 0;
                this.loaded = true;
                this.message = null;
                if (this.page > 1 && !this.rows.length && this.total) {
                    this.page = clampPage(this.page, this.total, this.pageSize);
                    this.reload();
                    return;
                }
                this.render();
            }).catch((err) => {
                if (seq === this._seq) this.setError(err && err.message ? err.message : String(err));
            });
        }

        setLoading() {
            this._state('<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Loading…');
        }

        setError(message) {
            this.message = message;
            this._state(`<span class="m-tone-danger">Could not load the ${esc(this.noun[1])}: ${esc(message)}</span>`);
        }

        /** The rows the search and the filter leave, in the order shown (client mode). */
        visibleRows() {
            let rows = this.filter ? this.rows.filter(this.filter) : this.rows;
            rows = filterRows(rows, this.query, (row) => this._searchText(row));
            const col = this.sort && this._column(this.sort.key);
            if (col) rows = sortRows(rows, (row) => this._value(col, row), this.sort.dir);
            return rows;
        }

        render() {
            this._renderHeader();
            let pageRows;
            let total;
            if (this.fetcher) {
                pageRows = this.rows;
                total = this.total;
            } else {
                const rows = this.visibleRows();
                total = rows.length;
                this.page = clampPage(this.page, total, this.pageSize);
                const start = (this.page - 1) * this.pageSize;
                pageRows = rows.slice(start, start + this.pageSize);
            }

            if (!pageRows.length) {
                this._renderEmpty();
            } else {
                this.tbody.innerHTML = pageRows.map((row) => `<tr>${this.columns.map((col) => {
                    const cls = [col.className, col.align ? `is-${col.align}` : ''].filter(Boolean).join(' ');
                    const html = col.render ? col.render(row) : esc(this._value(col, row));
                    return `<td${cls ? ` class="${cls}"` : ''}>${html}</td>`;
                }).join('')}</tr>`).join('');
            }
            this._renderFooter(total);
        }

        // ── private ────────────────────────────────────────────

        _build(filters) {
            this.container.innerHTML = '';
            if (this.searchable || filters) {
                const toolbar = document.createElement('div');
                toolbar.className = 'm-table-toolbar';
                if (filters) {
                    filters.classList.add('m-table-filters');
                    toolbar.appendChild(filters);
                }
                if (this.searchable) {
                    const box = document.createElement('div');
                    box.className = 'm-table-search';
                    box.innerHTML = '<i class="fas fa-search" aria-hidden="true"></i>'
                        + '<input type="search" class="m-input m-input-sm" autocomplete="off">';
                    this.search = box.querySelector('input');
                    this.search.placeholder = this.placeholder;
                    this.search.setAttribute('aria-label', this.placeholder);
                    this.search.addEventListener('input', () => this._onSearch());
                    this.search.addEventListener('keydown', (e) => {
                        if (e.key === 'Escape' && this.search.value) {
                            e.stopPropagation();
                            this.clearSearch();
                        }
                    });
                    toolbar.appendChild(box);
                }
                this.container.appendChild(toolbar);
            }

            const card = document.createElement('div');
            card.className = 'm-card';
            card.innerHTML = '<div class="m-table-wrap"><table class="m-table"><thead><tr></tr></thead>'
                + '<tbody></tbody></table></div><div class="m-table-footer" hidden>'
                + '<span class="m-table-count" aria-live="polite"></span><div class="m-table-pager">'
                + '<button type="button" class="m-btn m-btn-ghost m-btn-icon" data-page="-1" aria-label="Previous page">'
                + '<i class="fas fa-chevron-left" aria-hidden="true"></i></button>'
                + '<span class="m-table-page"></span>'
                + '<button type="button" class="m-btn m-btn-ghost m-btn-icon" data-page="1" aria-label="Next page">'
                + '<i class="fas fa-chevron-right" aria-hidden="true"></i></button></div></div>';
            this.container.appendChild(card);
            const table = card.querySelector('table');
            if (this.label) table.setAttribute('aria-label', this.label);
            this.headRow = table.querySelector('thead tr');
            this.tbody = table.querySelector('tbody');
            this.footer = card.querySelector('.m-table-footer');
            this.count = card.querySelector('.m-table-count');
            this.pager = card.querySelector('.m-table-pager');
            this.pageLabel = card.querySelector('.m-table-page');
            this.pager.querySelectorAll('[data-page]').forEach((btn) => {
                btn.addEventListener('click', () => this._goTo(this.page + Number(btn.dataset.page)));
            });

            this.headRow.innerHTML = this.columns.map((col, i) => {
                const cls = col.align ? ` class="is-${col.align}"` : '';
                if (!this._sortable(col)) return `<th scope="col"${cls}>${esc(col.label)}</th>`;
                return `<th scope="col"${cls}><button type="button" class="m-table-sort" data-col="${i}">`
                    + `${esc(col.label)}<i class="fas fa-sort" aria-hidden="true"></i></button></th>`;
            }).join('');
            this.headRow.querySelectorAll('[data-col]').forEach((btn) => {
                btn.addEventListener('click', () => this._sortBy(this.columns[Number(btn.dataset.col)]));
            });
        }

        _renderHeader() {
            this.headRow.querySelectorAll('[data-col]').forEach((btn) => {
                const col = this.columns[Number(btn.dataset.col)];
                const th = btn.parentElement;
                const icon = btn.querySelector('i');
                if (this.sort && this.sort.key === col.key) {
                    th.setAttribute('aria-sort', this.sort.dir === 'desc' ? 'descending' : 'ascending');
                    icon.className = `fas ${this.sort.dir === 'desc' ? 'fa-sort-down' : 'fa-sort-up'}`;
                } else {
                    th.removeAttribute('aria-sort');
                    icon.className = 'fas fa-sort';
                }
            });
        }

        _renderEmpty() {
            const narrowed = this.query || (this.filter && this.rows.length);
            const text = narrowed
                ? (this.query ? `No ${this.noun[1]} match “${this.query}”.` : `No ${this.noun[1]} match the filters.`)
                : this.empty;
            const clear = this.query
                ? '<br><button type="button" class="m-btn m-btn-secondary m-btn-sm" data-clear>Clear search</button>'
                : '';
            this.tbody.innerHTML = `<tr class="m-table-empty"><td colspan="${this.columns.length}">${esc(text)}${clear}</td></tr>`;
            const btn = this.tbody.querySelector('[data-clear]');
            if (btn) btn.addEventListener('click', () => this.clearSearch());
        }

        _renderFooter(total) {
            this.footer.hidden = total === 0;
            this.count.textContent = rangeText(this.page, this.pageSize, total, this.noun);
            const pages = pageCount(total, this.pageSize);
            this.pager.hidden = pages <= 1;
            this.pageLabel.textContent = `Page ${this.page} of ${pages}`;
            this.pager.querySelector('[data-page="-1"]').disabled = this.page <= 1;
            this.pager.querySelector('[data-page="1"]').disabled = this.page >= pages;
        }

        _state(html) {
            this.tbody.innerHTML = `<tr class="m-table-empty"><td colspan="${this.columns.length}">${html}</td></tr>`;
            this.footer.hidden = true;
        }

        clearSearch() {
            if (!this.search) return;
            this.search.value = '';
            this._onSearch(true);
            this.search.focus();
        }

        _onSearch(now) {
            this.query = this.search.value.trim();
            this.page = 1;
            if (!this.fetcher) {
                this.render();
                return;
            }
            clearTimeout(this._timer);
            if (now) this.reload();
            else this._timer = setTimeout(() => this.reload(), 300);
        }

        _sortBy(col) {
            const same = this.sort && this.sort.key === col.key;
            this.sort = { key: col.key, dir: same && this.sort.dir === 'asc' ? 'desc' : 'asc' };
            this.page = 1;
            if (this.fetcher) this.reload();
            else this.render();
        }

        _goTo(page) {
            const total = this.fetcher ? this.total : this.visibleRows().length;
            const next = clampPage(page, total, this.pageSize);
            if (next === this.page) return;
            this.page = next;
            if (this.fetcher) this.reload();
            else this.render();
        }

        _column(key) {
            return this.columns.find((c) => c.key === key && this._sortable(c)) || null;
        }

        _sortable(col) {
            return Boolean(col.key) && col.sortable !== false;
        }

        _value(col, row) {
            return col.value ? col.value(row) : (col.key ? row[col.key] : '');
        }

        _searchText(row) {
            return this.columns
                .filter((c) => (c.key || c.value) && c.searchable !== false)
                .map((c) => {
                    const v = c.search ? c.search(row) : this._value(c, row);
                    return isBlank(v) ? '' : String(v);
                })
                .join(' ');
        }
    }

    MateTable.esc = esc;
    MateTable.filterRows = filterRows;
    MateTable.sortRows = sortRows;
    MateTable.pageCount = pageCount;
    MateTable.clampPage = clampPage;
    MateTable.rangeText = rangeText;

    if (typeof module !== 'undefined' && module.exports) module.exports = MateTable;
    else root.MateTable = MateTable;
})(typeof window !== 'undefined' ? window : globalThis);
