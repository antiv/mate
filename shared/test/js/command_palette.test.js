// What the command palette finds (static/js/command-palette.js).
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const palette = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'command-palette.js'));

const item = (label, keywords = '') => ({ label, keywords });
const labels = (groups, key) => ((groups.find((g) => g.key === key) || {}).items || []).map((i) => i.label);

test('every word has to match, in any case, with or without accents', () => {
    const pages = [item('Models and keys', 'Workspace provider'), item('Gde su ključevi'), item('Đorđe')];
    const found = (query) => labels(palette.match({ pages }, query), 'pages');
    assert.deepEqual(found('KEYS models'), ['Models and keys']);
    assert.deepEqual(found('kljucevi'), ['Gde su ključevi']);
    assert.deepEqual(found('KLJUČEVI gde'), ['Gde su ključevi']);
    assert.deepEqual(found('djordje'), []);
    assert.deepEqual(found('dorde'), ['Đorđe']);
    assert.deepEqual(found('keys nothing'), []);
});

test('the closest name comes first, then the order given', () => {
    const agents = [item('chess_mate_root', 'the support desk'), item('my_support'), item('supporting'),
                    item('support_root'), item('support'), item('unsupported')];
    assert.deepEqual(labels(palette.match({ agents }, 'support'), 'agents'),
        ['support', 'supporting', 'support_root', 'my_support', 'unsupported', 'chess_mate_root']);
});

test('a word of a name is found by its start', () => {
    assert.equal(palette.score(item('chess_mate_root'), 'mate'), 2);
    assert.equal(palette.score(item('Visual Builder'), 'bui vis'), 2);
    assert.equal(palette.score(item('Visual Builder'), 'zzz'), null);
});

test('without a query it offers the first few of each group, and spaces are no query', () => {
    const many = Array.from({ length: 20 }, (_, i) => item(`item ${i}`));
    for (const query of ['', '   ']) {
        const groups = palette.match({ sessions: many, agents: many, pages: many }, query);
        assert.deepEqual(groups.map((g) => [g.key, g.items.length]), [['sessions', 5], ['agents', 5], ['pages', 6]]);
    }
    assert.equal(palette.match({ agents: many }, 'item')[0].items.length, 8);
});

test('typing an agent\u2019s name finds the agent before its conversations', () => {
    const data = { sessions: [item('Conversation with mcp_probe', 'mcp_probe')], agents: [item('mcp_probe')],
                   pages: [item('MCP pros')] };
    assert.deepEqual(palette.match(data, 'mcp pro').map((g) => g.key), ['pages', 'agents', 'sessions']);
    assert.deepEqual(palette.match(data, 'mcp_probe').map((g) => g.key), ['agents', 'sessions']);
    // A conversation named as typed is closer than an agent that only mentions it
    assert.deepEqual(palette.match({ sessions: [item('Refunds')], agents: [item('desk', 'refunds')] }, 'refunds').map((g) => g.key),
        ['sessions', 'agents']);
    // Equally close: the agents first
    assert.deepEqual(palette.match({ sessions: [item('old desk')], agents: [item('my desk')] }, 'desk').map((g) => g.key),
        ['agents', 'sessions']);
});

test('a group without a match is left out, and missing data is no error', () => {
    assert.deepEqual(palette.match({ agents: [item('a')] }, 'zzz'), []);
    assert.deepEqual(palette.match({}, ''), []);
    assert.equal(palette.score({ label: null, keywords: undefined }, 'x'), null);
});
