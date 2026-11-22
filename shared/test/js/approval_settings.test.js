// The approval settings editor's reading and writing (static/js/approval-settings.js),
// which must agree with shared/utils/approval_config.py.
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const settings = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'approval-settings.js'));

test('both forms are read, and what does not validate gets defaults', () => {
    assert.deepEqual(settings.read({ require_confirmation: ['a', 'b'] }),
        { tools: ['a', 'b'], approver: 'chat', notify: null, expires: null });
    assert.deepEqual(settings.read({ require_confirmation: 'a' }).tools, ['a']);
    const full = settings.read({ require_confirmation: {
        tools: ['a'], approver: 'inbox', expires_after_minutes: 30,
        notify: { type: 'slack', url: 'https://hooks.example/x' } } });
    assert.deepEqual([full.approver, full.expires, full.notify.type], ['inbox', 30, 'slack']);
    const bad = settings.read({ require_confirmation: {
        tools: ['a'], approver: 'boss', expires_after_minutes: 0, notify: { type: 'pager' } } });
    assert.deepEqual([bad.approver, bad.expires, bad.notify], ['chat', null, null]);
    assert.deepEqual(settings.read({}).tools, []);
});

test('tool names are split on commas and spaces, without repeats', () => {
    assert.deepEqual(settings.toolNames(' a, b  c,,a '), ['a', 'b', 'c']);
    assert.deepEqual(settings.toolNames(''), []);
});

test('only tools is written as the list, and other keys stay', () => {
    const out = settings.write({ memory_blocks: true }, { tools: ['a'], approver: 'chat', notify: null, expires: '' });
    assert.deepEqual(out.toolConfig, { memory_blocks: true, require_confirmation: ['a'] });
});

test('anything else is written as the object', () => {
    const out = settings.write({}, { tools: ['a'], approver: 'inbox',
        notify: { type: 'email', to: 'ops@example.com' }, expires: '120' });
    assert.deepEqual(out.toolConfig.require_confirmation, {
        tools: ['a'], approver: 'inbox', notify: { type: 'email', to: 'ops@example.com' }, expires_after_minutes: 120 });
});

test('no tools turns approvals off', () => {
    const out = settings.write({ memory_blocks: true, require_confirmation: ['a'] },
        { tools: [], approver: 'chat', notify: null, expires: '' });
    assert.deepEqual(out.toolConfig, { memory_blocks: true });
    assert.ok(settings.write({}, { tools: [], approver: 'inbox' }).error);
});

test('what does not validate is refused', () => {
    assert.ok(settings.write({}, { tools: ['a'], expires: '1.5' }).error);
    assert.ok(settings.write({}, { tools: ['a'], expires: '0' }).error);
    assert.ok(settings.write({}, { tools: ['a'], expires: '999999' }).error);
    assert.ok(settings.write({}, { tools: ['a'], notify: { type: 'slack', url: ' ' } }).error);
    assert.ok(settings.write({}, { tools: ['a'], notify: { type: 'email' } }).error);
    assert.ok(settings.write({}, { tools: ['a'], approver: 'boss' }).error);
});

test('the summary', () => {
    assert.equal(settings.summary({}), 'Off');
    assert.equal(settings.summary({ require_confirmation: ['a'] }), '1 tool · the chat decides');
    assert.equal(settings.summary({ require_confirmation: { tools: ['a', 'b'], approver: 'inbox',
        notify: { type: 'slack', url: 'u' } } }), '2 tools · an administrator decides · tells slack');
});
