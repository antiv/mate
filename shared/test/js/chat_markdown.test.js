// The lists and tables of an agent's message (static/js/standalone/chat-markdown.js).
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { blocks } = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'standalone', 'chat-markdown.js'));

const bold = (text) => text.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
const html = (...lines) => blocks(lines.join('\n'), bold);

test('paragraphs, with a break for each line', () => {
    assert.equal(html('One', 'two **three**', '', 'Four'), '<p>One<br>two <b>three</b></p><p>Four</p>');
    assert.equal(html(''), '');
});

test('a numbered list keeps its numbers', () => {
    assert.equal(html('Steps:', '1. First', '2. **Second**', 'Done'),
        '<p>Steps:</p><ol><li>First</li><li><b>Second</b></li></ol><p>Done</p>');
    assert.equal(html('1. First', '', '2. Second'), '<ol><li>First</li><li>Second</li></ol>');
    // Something in between: the numbers go on
    assert.equal(html('1. First', 'A note', '2. Second'),
        '<ol><li>First</li></ol><p>A note</p><ol start="2"><li>Second</li></ol>');
});

test('a bullet list is one list, and the two kinds are two', () => {
    assert.equal(html('- a', '* b', '+ c'), '<ul><li>a</li><li>b</li><li>c</li></ul>');
    assert.equal(html('- a', '1. b'), '<ul><li>a</li></ul><ol><li>b</li></ol>');
});

test('deeper items are a list in the item above', () => {
    assert.equal(html('1. a', '   - b', '   - c', '2. d'),
        '<ol><li>a<ul><li>b</li><li>c</li></ul></li><li>d</li></ol>');
    assert.equal(html('- a', '  goes on', '- b'), '<ul><li>a<br>goes on</li><li>b</li></ul>');
    assert.equal(html('  - a', '- b'), '<ul><li>a</li></ul><ul><li>b</li></ul>');
});

test('what only looks like a list', () => {
    assert.equal(html('**Bold** start', '3.5 percent', '-dash', '- '), '<p><b>Bold</b> start<br>3.5 percent<br>-dash<br>- </p>');
});

test('a table', () => {
    assert.equal(html('Prices:', '| Item | Price |', '|:--|--:|', '| **Lamp** | 64 |', '| Desk |', '', 'End'),
        '<p>Prices:</p><div class="md-table"><table><thead><tr><th>Item</th><th class="md-right">Price</th></tr></thead>'
        + '<tbody><tr><td><b>Lamp</b></td><td class="md-right">64</td></tr>'
        + '<tr><td>Desk</td><td class="md-right"></td></tr></tbody></table></div><p>End</p>');
});

test('a table without bars at the ends, an empty header, an escaped bar', () => {
    assert.match(html('a | b', '---|---', '1 | 2'), /<th>a<\/th><th>b<\/th>.*<td>1<\/td><td>2<\/td>/);
    assert.equal(html('| | |', '|---|---|', '| x | y |'),
        '<div class="md-table"><table><tbody><tr><td>x</td><td>y</td></tr></tbody></table></div>');
    assert.match(html('| a | b |', '|---|---|', '| x \\| y | z |'), /<td>x \| y<\/td><td>z<\/td>/);
});

test('bars that are not a table', () => {
    assert.equal(html('a | b', 'c | d'), '<p>a | b<br>c | d</p>');
    assert.equal(html('| a | b |', '|---|'), '<p>| a | b |<br>|---|</p>');
    assert.equal(html('a', '---'), '<p>a<br>---</p>');
});

test('escaped text stays escaped, in lists and tables too', () => {
    const bad = '&lt;img src=x onerror=1&gt;';
    const out = html(`- ${bad}`, '', `| ${bad} | b |`, '|---|---|', `| ${bad} | ${bad} |`);
    assert.equal(out.split(bad).length - 1, 4);
    assert.doesNotMatch(out, /<img/);
});

test('a long message is quick', () => {
    const started = Date.now();
    blocks(`${'|-'.repeat(20000)}x\n${'|-'.repeat(20000)}x`, bold);
    blocks(`${' '.repeat(20000)}-${' '.repeat(20000)}`, bold);
    blocks(Array.from({ length: 2000 }, (_, i) => `${' '.repeat(i % 5)}- item`).join('\n'), bold);
    blocks(Array.from({ length: 5000 }, (_, i) => `${' '.repeat(i)}- item`).join('\n'), bold);
    assert.ok(Date.now() - started < 2000);
});
