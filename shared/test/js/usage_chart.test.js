// How the Usage tab's charts read the API's hourly counts (static/js/usage-chart.js).
// In a zone with summer time, where a day can have 23 or 25 hours.
'use strict';

process.env.TZ = 'Europe/Belgrade';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const chart = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'usage-chart.js'));

const row = (date, hour, model, tokens, cost) => ({
    date, hour, model_name: model, prompt_tokens: tokens, response_tokens: 0, cost_usd: cost });

test('a day\'s period has one column an hour, without gaps', () => {
    const cols = chart.columns(24, new Date(2026, 10, 23, 14, 30));
    assert.equal(cols.length, 25);
    assert.deepEqual([cols[0].key, cols[24].key], ['2026-11-22T14', '2026-11-23T14']);
    assert.equal(cols[24].label, '14:00');
});

test('a longer period has one column a day, each once, over a clock change', () => {
    // The clocks go back on 25 October 2026 and forward on 29 March 2026
    for (const now of [new Date(2026, 9, 27, 0, 30), new Date(2026, 2, 31, 23, 30)]) {
        const keys = chart.columns(168, now).map((c) => c.key);
        assert.equal(new Set(keys).size, keys.length);
        assert.ok(keys.length === 8 || keys.length === 9, keys.join());
        for (let i = 1; i < keys.length; i += 1) {
            assert.equal((Date.parse(keys[i]) - Date.parse(keys[i - 1])) / 86400000, 1);
        }
    }
});

test('the hours are UTC, the days the viewer\'s', () => {
    // 23:00 UTC is already the next day in Belgrade
    const s = chart.series([row('2026-11-21', 23, 'a', 10, 1), row('2026-11-21', 22, 'a', 5, 1)], 168, new Date(2026, 10, 23, 12));
    const day = (key) => s.tokens.a[s.columns.findIndex((c) => c.key === key)];
    assert.deepEqual([day('2026-11-21'), day('2026-11-22')], [5, 10]);
});

test('nothing is lost: the columns add up to the rows, even one outside them', () => {
    const rows = [row('2026-11-23', 9, 'a', 10, 0.5), row('2026-11-20', 1, 'b', 7, null), row('2026-01-01', 0, 'a', 100, 1)];
    const s = chart.series(rows, 168, new Date(2026, 10, 23, 12));
    const sum = (list) => list.reduce((x, y) => x + y, 0);
    assert.deepEqual([sum(s.tokens.a), sum(s.tokens.b), sum(s.cost.a)], [110, 7, 1.5]);
});

test('a model with no price is said, and one that cost nothing is a price', () => {
    const at = new Date(2026, 10, 23, 12);
    assert.deepEqual([chart.series([row('2026-11-23', 9, 'a', 5, null)], 24, at)].map((s) => [s.hasCost, s.missingCost]), [[false, true]]);
    assert.deepEqual([chart.series([row('2026-11-23', 9, 'a', 5, 0)], 24, at)].map((s) => [s.hasCost, s.missingCost]), [[true, false]]);
    // A failed call used nothing: no price is missing for it
    assert.equal(chart.series([row('2026-11-23', 9, 'a', 0, null)], 24, at).missingCost, false);
});

test('models come by name, and the smallest past the limit are the others', () => {
    const rows = ['h', 'g', 'f', 'e', 'd', 'c', 'b', 'a'].map((m, i) => row('2026-11-23', 9, m, 100 - i, 1));
    const s = chart.series(rows, 24, new Date(2026, 10, 23, 12));
    assert.deepEqual(s.models, ['d', 'e', 'f', 'g', 'h', chart.OTHER]);
    assert.equal(s.tokens[chart.OTHER].reduce((x, y) => x + y, 0), 95 + 94 + 93);
    // As many as fit are all named
    assert.deepEqual(chart.series(rows.slice(0, chart.MAX_MODELS), 24, new Date(2026, 10, 23, 12)).models,
        ['c', 'd', 'e', 'f', 'g', 'h']);
});

test('a model keeps its colour when others come and go', () => {
    const slot = chart.slots();
    assert.deepEqual(['a', 'b'].map(slot), [0, 1]);
    assert.deepEqual(['c', 'b', 'a'].map(slot), [2, 1, 0]);
    assert.equal(slot(chart.OTHER), -1);
    ['d', 'e', 'f'].forEach(slot);
    assert.equal(slot('past the colours'), -1);
});

test('a model without a colour left is one of the others, not a second grey one', () => {
    const at = new Date(2026, 10, 23, 12);
    const slot = chart.slots();
    const draw = (names) => chart.series(names.map((m, i) => row('2026-11-23', 9, m, 100 - i, 1)), 24, at, (m) => slot(m) >= 0).models;
    assert.deepEqual(draw(['a', 'b', 'c', 'd', 'e', 'f']), ['a', 'b', 'c', 'd', 'e', 'f']);
    assert.deepEqual(draw(['new', 'a', 'b']), ['a', 'b', chart.OTHER]);
});

test('a column is stacked in the order of the colours, the others last', () => {
    const slot = chart.slots();
    ['z', 'm', 'a'].forEach(slot);
    assert.deepEqual(chart.inOrder([chart.OTHER, 'a', 'm', 'z'], slot), ['z', 'm', 'a', chart.OTHER]);
});

test('no rows make empty columns', () => {
    const s = chart.series([], 24, new Date(2026, 10, 23, 12));
    assert.deepEqual([s.models, s.hasCost, s.columns.length], [[], false, 25]);
    assert.deepEqual(chart.series(undefined, 24, new Date(2026, 10, 23, 12)).models, []);
});
