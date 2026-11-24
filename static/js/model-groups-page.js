/**
 * Platform → Model groups (server/model_group_routes.py).
 *
 * The dialog holds a group's whole list of models and sends it on Save; the server
 * checks it. A model that LiteLLM doesn't know is confirmed by a checkbox on its row.
 */

// Handlers this file's markup calls through data-click and friends (csp-actions.js)
mateActions.allow('ModelGroupsPage.agents', 'ModelGroupsPage.close', 'ModelGroupsPage.create',
                  'ModelGroupsPage.edit', 'ModelGroupsPage.moveAndDelete', 'ModelGroupsPage.remove',
                  'ModelGroupsPage.save', 'ModelGroupsPage.showModels');
const ModelGroupsPage = (function () {
    'use strict';

    const API = '/dashboard/api/platform/model-groups';
    const NEEDS = { tools: 'calls tools', images: 'reads images' };
    const esc = MateTable.esc;
    const el = (id) => document.getElementById(id);

    let table = null;
    let groups = [];
    let editing = null;     // the group in the dialog, null while a new one is made
    let models = [];        // [{model, confirmed, known, ownRefusal}] in the dialog
    let deleting = null;

    function init() {
        table = new MateTable(el('groupTable'), {
            noun: ['group', 'groups'],
            label: 'Model groups',
            empty: 'No model groups yet. Click New group to make one.',
            columns: [
                { key: 'label', label: 'Group', value: (g) => `${g.label} ${g.name} ${g.description}`, render: _group },
                { key: 'models', label: 'Models', value: (g) => g.models.map((m) => m.model).join(' '),
                  sortable: false, render: _models },
                { label: 'They must', render: _needs },
                { key: 'agents', label: 'Agents', value: (g) => g.agents.length, render: _agents },
                { label: 'Actions', align: 'right', render: _actions },
            ],
        });
        el('groupAddForm').addEventListener('submit', add);
        el('groupModels').addEventListener('click', _onRowClick);
        el('groupModels').addEventListener('change', _onConfirm);
        load();
    }

    async function load() {
        try {
            groups = (await _request('GET', API)).groups;
            table.setRows(groups);
        } catch (e) {
            table.setError(e.message);
        }
    }

    async function _request(method, url, body) {
        const resp = await fetch(url, {
            method,
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            throw new Error(typeof data.detail === 'string' ? data.detail : `Server returned ${resp.status}`);
        }
        return data;
    }

    // ── The table ────────────────────────────────────────────────────────

    function _group(g) {
        return `<b>${esc(g.label)}</b> <code class="m-code">group/${esc(g.name)}</code>` +
            (g.description ? `<div class="m-muted">${esc(g.description)}</div>` : '');
    }

    function _models(g) {
        if (!g.models.length) return '<span class="m-badge m-badge-warning">No models</span>';
        return '<ol class="m-list-plain">' + g.models.map((m) => `<li class="m-mono">${esc(m.model)}</li>`).join('') + '</ol>';
    }

    function _needs(g) {
        const badges = [];
        if (g.needs_tools) badges.push('<span class="m-badge">Call tools</span>');
        if (g.needs_images) badges.push('<span class="m-badge">Read images</span>');
        if (g.own_servers_only) badges.push('<span class="m-badge m-badge-accent">Own servers only</span>');
        return badges.join(' ');
    }

    function _args(g) {
        return esc(JSON.stringify([g.name]));
    }

    function _agents(g) {
        if (!g.agents.length) return '0';
        return `<button type="button" class="m-link" data-click="ModelGroupsPage.agents" data-args="${_args(g)}" ` +
            `aria-label="Agents that use ${esc(g.label)}">${g.agents.length}</button>`;
    }

    function _actions(g) {
        return `<button type="button" class="m-btn m-btn-ghost m-btn-icon" data-click="ModelGroupsPage.edit" data-args="${_args(g)}" ` +
            `aria-label="Edit ${esc(g.label)}" title="Edit"><i class="fas fa-edit" aria-hidden="true"></i></button>` +
            `<button type="button" class="m-btn m-btn-ghost m-btn-danger m-btn-icon" data-click="ModelGroupsPage.remove" data-args="${_args(g)}" ` +
            `aria-label="Delete ${esc(g.label)}" title="Delete"><i class="fas fa-trash" aria-hidden="true"></i></button>`;
    }

    // ── The dialog ───────────────────────────────────────────────────────

    function create() {
        _open(null);
    }

    function edit(name) {
        _open(groups.find((g) => g.name === name));
    }

    function _open(group) {
        editing = group;
        const g = group || { name: '', label: '', description: '', needs_tools: true, models: [] };
        el('groupDialogTitle').textContent = group ? `Edit ${group.label}` : 'New group';
        el('groupNameField').hidden = !!group;
        el('groupName').value = g.name;
        el('groupLabel').value = g.label;
        el('groupDescription').value = g.description;
        el('groupTools').checked = !!g.needs_tools;
        el('groupImages').checked = !!g.needs_images;
        el('groupOwn').checked = !!g.own_servers_only;
        el('groupAddModel').value = '';
        models = g.models.map((m) => ({ ...m, confirmed: m.confirmed.slice() }));
        _error('groupError', '');
        showModels();
        mateDialog.open('groupDialog', { focus: group ? 'groupLabel' : 'groupName' });
    }

    function close(id) {
        mateDialog.close(id);
    }

    function _error(id, message) {
        el(id).textContent = message;
        el(id).hidden = !message;
    }

    function _needed() {
        return [['tools', 'groupTools'], ['images', 'groupImages']].filter(([, id]) => el(id).checked).map(([n]) => n);
    }

    function showModels() {
        el('groupNoModels').hidden = models.length > 0;
        const last = models.length - 1;
        el('groupModels').innerHTML = models.map((m, i) => {
            const name = esc(m.model);
            const button = (action, label, icon, off) =>
                `<button type="button" class="m-btn m-btn-ghost m-btn-icon" data-row="${i}" data-action="${action}" ` +
                `aria-label="${label} ${name}" title="${label}"${off ? ' disabled' : ''}><i class="fas ${icon}" aria-hidden="true"></i></button>`;
            return `<li><div class="m-list-main"><span class="m-mono">${name}</span><div class="m-row">${_abilities(m, i)}</div></div>` +
                button('up', 'Move up', 'fa-arrow-up', i === 0) + button('down', 'Move down', 'fa-arrow-down', i === last) +
                button('remove', 'Remove', 'fa-times', false) + '</li>';
        }).join('');
    }

    // What the group needs of the model: known, known not to, or for the administrator to say
    function _abilities(m, row) {
        const parts = _needed().map((need) => {
            if (m.known[need] === true) return `<span class="m-badge m-badge-success">${NEEDS[need]}</span>`;
            if (m.known[need] === false) return `<span class="m-badge m-badge-danger">not: ${NEEDS[need]}</span>`;
            const said = m.confirmed.includes(need) ? ' checked' : '';
            return `<label class="m-check"><input type="checkbox" data-row="${row}" data-need="${need}"${said}>` +
                `It ${NEEDS[need]} (LiteLLM doesn't say)</label>`;
        });
        if (el('groupOwn').checked && m.ownRefusal) {
            parts.push(`<span class="m-badge m-badge-danger" title="${esc(m.ownRefusal)}">not on your own servers</span>`);
        }
        return parts.join('');
    }

    function _onRowClick(event) {
        const button = event.target.closest('button[data-action]');
        if (!button) return;
        const row = Number(button.dataset.row);
        const action = button.dataset.action;
        const to = action === 'up' ? row - 1 : row + 1;
        const [model] = models.splice(row, 1);
        if (action !== 'remove') models.splice(to, 0, model);
        showModels();
        // The list is drawn anew: the focus goes where the model went
        const next = el('groupModels').querySelector(`button[data-row="${to}"][data-action="${action}"]:not([disabled])`) ||
            el('groupModels').querySelector(`button[data-row="${to}"]:not([disabled])`);
        if (action !== 'remove' && next) next.focus();
        else el('groupAddModel').focus();
    }

    function _onConfirm(event) {
        const { row, need } = event.target.dataset;
        if (!need) return;
        const model = models[Number(row)];
        model.confirmed = model.confirmed.filter((n) => n !== need);
        if (event.target.checked) model.confirmed.push(need);
    }

    async function add(event) {
        event.preventDefault();
        const input = el('groupAddModel');
        const name = input.value.trim();
        if (!name) return true;
        el('groupAddButton').disabled = true;
        _error('groupError', '');
        try {
            const found = await _request('GET', `${API}/check?${new URLSearchParams({ model: name })}`);
            if (models.some((m) => m.model === found.model)) throw new Error(`${found.model} is in the list already`);
            models.push({ model: found.model, confirmed: [], known: found.known, ownRefusal: found.own_server_refusal });
            input.value = '';
            showModels();
            return true;
        } catch (e) {
            _error('groupError', e.message);
            return false;
        } finally {
            el('groupAddButton').disabled = false;
            input.focus();
        }
    }

    async function save() {
        // A model typed and not added yet belongs to the list too
        if (!await add(new Event('submit'))) return;
        const body = {
            label: el('groupLabel').value.trim(),
            description: el('groupDescription').value.trim(),
            needs_tools: el('groupTools').checked,
            needs_images: el('groupImages').checked,
            own_servers_only: el('groupOwn').checked,
            models: models.map(({ model, confirmed }) => ({ model, confirmed })),
        };
        el('groupSave').disabled = true;
        try {
            if (editing) {
                await _request('PUT', `${API}/${encodeURIComponent(editing.name)}`, body);
            } else {
                await _request('POST', API, { ...body, name: el('groupName').value.trim() });
            }
            close('groupDialog');
            showNotification(editing ? 'Group saved' : 'Group created', 'success');
            load();
        } catch (e) {
            _error('groupError', e.message);
        } finally {
            el('groupSave').disabled = false;
        }
    }

    // ── Its agents, and deleting it ──────────────────────────────────────

    function agents(name) {
        _openAgents(groups.find((g) => g.name === name), false);
    }

    async function remove(name) {
        const group = groups.find((g) => g.name === name);
        if (group.agents.length) {
            _openAgents(group, true);
            return;
        }
        const sure = await showConfirm(`Delete ${group.label} (group/${name})? This can't be undone.`,
                                       'Delete group', 'Delete', 'Cancel', 'danger');
        if (sure) _delete(group, '', null);
    }

    function _openAgents(group, forDeleting) {
        deleting = forDeleting ? group : null;
        el('agentsDialogTitle').textContent = forDeleting ? `Delete ${group.label}` : `Agents that use ${group.label}`;
        el('agentsNote').textContent = forDeleting
            ? 'These agents use the group. They have to move to another one before it is deleted.'
            : 'The agents of every workspace that name this group.';
        el('agentsList').innerHTML = group.agents.map((a) =>
            `<li><div class="m-list-main"><span>${esc(a.name)}</span><span class="m-muted">${esc(a.workspace || '')}</span></div></li>`).join('');
        const targets = groups.filter((g) => g.name !== group.name && g.models.length &&
            (!group.own_servers_only || g.own_servers_only));
        el('moveField').hidden = !forDeleting;
        el('moveTo').innerHTML = targets.map((g) => `<option value="${esc(g.name)}">${esc(g.label)} (group/${esc(g.name)})</option>`).join('');
        el('moveTo').disabled = !targets.length;
        el('moveHint').textContent = targets.length ? ''
            : `There is no other group with models${group.own_servers_only ? ' and Own servers only' : ''}.`;
        el('moveAndDelete').hidden = !forDeleting;
        el('moveAndDelete').disabled = !targets.length;
        _error('agentsError', '');
        mateDialog.open('agentsDialog');
    }

    function moveAndDelete() {
        _delete(deleting, el('moveTo').value, 'agentsError');
    }

    async function _delete(group, moveTo, errorId) {
        try {
            const query = moveTo ? `?${new URLSearchParams({ move_to: moveTo })}` : '';
            await _request('DELETE', `${API}/${encodeURIComponent(group.name)}${query}`);
            if (errorId) close('agentsDialog');
            showNotification('Group deleted', 'success');
        } catch (e) {
            if (errorId) _error(errorId, e.message);
            else showNotification(e.message, 'error');
        }
        load();
    }

    document.addEventListener('DOMContentLoaded', init);

    return { agents, close, create, edit, moveAndDelete, remove, save, showModels };
})();

// data-click handlers are looked up on window
window.ModelGroupsPage = ModelGroupsPage;
