// The shared table's searching, sorting and paging (static/js/data-table.js).
// Run by shared/test/test_static_js_units.py, or alone: node --test shared/test/js/data_table.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const MateTable = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'data-table.js'));

const TRIGGERS = [
    { id: 1, name: 'Daily report', agent: 'reporter', type: 'cron', runs: 10, last: '2026-11-20T09:00:00Z' },
    { id: 2, name: 'GitHub push', agent: 'reviewer', type: 'webhook', runs: 2, last: null },
    { id: 3, name: 'item 10', agent: 'reporter', type: 'cron', runs: 10, last: '2026-11-21T09:00:00Z' },
    { id: 4, name: 'item 2', agent: 'Writer', type: 'webhook', runs: 0, last: '' },
];

// A table without the DOM: what visibleRows() needs, as the constructor would set it
function table(options = {}) {
    const t = Object.create(MateTable.prototype);
    Object.assign(t, {
        columns: options.columns || [
            { key: 'name', label: 'Name' },
            { key: 'agent', label: 'Agent' },
            { key: 'runs', label: 'Runs' },
            { key: 'last', label: 'Last run' },
            { label: 'Actions', render: () => '<button>x</button>' },
        ],
        rows: TRIGGERS,
        filter: options.filter || null,
        query: options.query || '',
        sort: options.sort || null,
    });
    return t;
}

const ids = (rows) => rows.map((r) => r.id);

test('search matches every word, in any case, across the columns', () => {
    assert.deepEqual(ids(table({ query: 'REPORTER' }).visibleRows()), [1, 3]);
    assert.deepEqual(ids(table({ query: 'item reporter' }).visibleRows()), [3]);
    assert.deepEqual(ids(table({ query: '  ' }).visibleRows()), [1, 2, 3, 4]);
    assert.deepEqual(ids(table({ query: 'nothing like it' }).visibleRows()), []);
});

test('search skips columns marked searchable: false and columns without a key', () => {
    const t = table({ query: 'button' });
    assert.deepEqual(ids(t.visibleRows()), []);
    const columns = [{ key: 'name', label: 'Name' }, { key: 'agent', label: 'Agent', searchable: false }];
    assert.deepEqual(ids(table({ columns, query: 'reporter' }).visibleRows()), []);
});

test('a column can search by other text than it shows', () => {
    const columns = [{ key: 'type', label: 'Type', search: (r) => (r.type === 'cron' ? 'Cron schedule' : 'Webhook') }];
    assert.deepEqual(ids(table({ columns, query: 'schedule' }).visibleRows()), [1, 3]);
});

test('the filter and the search apply together', () => {
    const t = table({ filter: (r) => r.type === 'cron', query: 'item' });
    assert.deepEqual(ids(t.visibleRows()), [3]);
});

test('text sorts in natural order and ignores case', () => {
    assert.deepEqual(ids(table({ sort: { key: 'name', dir: 'asc' } }).visibleRows()), [1, 2, 4, 3]);
    assert.deepEqual(ids(table({ sort: { key: 'agent', dir: 'asc' } }).visibleRows()), [1, 3, 2, 4]);
});

test('numbers sort by value, and equal values keep their order', () => {
    assert.deepEqual(ids(table({ sort: { key: 'runs', dir: 'asc' } }).visibleRows()), [4, 2, 1, 3]);
    assert.deepEqual(ids(table({ sort: { key: 'runs', dir: 'desc' } }).visibleRows()), [1, 3, 2, 4]);
});

test('blank values go last in both directions', () => {
    assert.deepEqual(ids(table({ sort: { key: 'last', dir: 'asc' } }).visibleRows()), [1, 3, 2, 4]);
    assert.deepEqual(ids(table({ sort: { key: 'last', dir: 'desc' } }).visibleRows()), [3, 1, 2, 4]);
});

test('a sort on a column that cannot sort is ignored', () => {
    const columns = [{ key: 'name', label: 'Name', sortable: false }];
    assert.deepEqual(ids(table({ columns, sort: { key: 'name', dir: 'desc' } }).visibleRows()), [1, 2, 3, 4]);
});

test('a column sorts by its value function', () => {
    const columns = [{ key: 'name', label: 'Name', value: (r) => r.name.length }];
    assert.deepEqual(ids(table({ columns, sort: { key: 'name', dir: 'asc' } }).visibleRows()), [4, 3, 2, 1]);
});

test('sortRows does not change the list it is given', () => {
    const rows = [{ v: 2 }, { v: 1 }];
    MateTable.sortRows(rows, (r) => r.v, 'asc');
    assert.deepEqual(rows, [{ v: 2 }, { v: 1 }]);
});

test('paging', () => {
    assert.equal(MateTable.pageCount(0, 25), 1);
    assert.equal(MateTable.pageCount(25, 25), 1);
    assert.equal(MateTable.pageCount(26, 25), 2);
    assert.equal(MateTable.clampPage(5, 30, 25), 2);
    assert.equal(MateTable.clampPage(0, 30, 25), 1);
    assert.equal(MateTable.clampPage(3, 0, 25), 1);
});

test('the count names the range only when there is more than a page', () => {
    const noun = ['trigger', 'triggers'];
    assert.equal(MateTable.rangeText(1, 25, 1, noun), '1 trigger');
    assert.equal(MateTable.rangeText(1, 25, 3, noun), '3 triggers');
    assert.equal(MateTable.rangeText(1, 25, 80, noun), '1–25 of 80 triggers');
    assert.equal(MateTable.rangeText(4, 25, 80, noun), '76–80 of 80 triggers');
});

test('esc escapes markup and quotes, and blanks null', () => {
    assert.equal(MateTable.esc(`<a href="x" title='y'>&</a>`), '&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;');
    assert.equal(MateTable.esc(null), '');
    assert.equal(MateTable.esc(0), '0');
});
