/**
 * Approvals page — MATE Dashboard (documents/design/approvals-inbox.md, "Dashboard").
 *
 * Waiting: the requests the inbox decides, loaded whole and refreshed every 30 seconds.
 * History: every request of the workspace, a chat's included, paged by the API.
 * A row opens a dialog with the arguments; a waiting one can be approved or rejected there.
 */

// Handlers this file's markup calls through data-click and friends (csp-actions.js)
mateActions.allow('ApprovalsPage.closeDialog', 'ApprovalsPage.decide', 'ApprovalsPage.open',
                  'ApprovalsPage.reloadHistory');
const ApprovalsPage = (function () {
    'use strict';

    const API = '/dashboard/api/approvals';
    const REFRESH_MS = 30000;
    const esc = MateTable.esc;

    let waiting = null;
    let history = null;
    const rows = new Map();     // id -> the request, from whichever list showed it last
    let current = null;         // the request open in the dialog
    let deciding = false;

    function init() {
        waiting = new MateTable(document.getElementById('waitingTable'), {
            noun: ['request', 'requests'],
            label: 'Waiting requests',
            empty: 'Nothing is waiting for approval.',
            sort: { key: 'requested_at', dir: 'asc' },
            columns: [
                { key: 'requested_at', label: 'Waiting', render: (r) => `<span title="${esc(_when(r.requested_at))}">${esc(_ago(r.requested_at))}</span>` },
                { key: 'agent_name', label: 'Agent', className: 'm-cell-strong' },
                { key: 'tool_name', label: 'Tool', render: _toolLink },
                { key: 'source', label: 'Source', render: _sourceBadge },
                { key: 'user_id', label: 'Started by' },
                { key: 'expires_at', label: 'Expires', searchable: false, render: (r) => esc(_until(r.expires_at)) },
                { label: 'Actions', align: 'right', render: (r) => _openButton(r, 'Review') },
            ],
        });
        history = new MateTable(document.getElementById('historyTable'), {
            noun: ['request', 'requests'],
            label: 'Approval history',
            empty: 'No approval requests yet.',
            filters: document.getElementById('historyFilters'),
            search: { placeholder: 'Search agent, tool or user' },
            fetch: _fetchHistory,
            columns: [
                { key: 'requested_at', label: 'Requested', sortable: false, render: (r) => esc(_when(r.requested_at)) },
                { key: 'agent_name', label: 'Agent', sortable: false, className: 'm-cell-strong' },
                { key: 'tool_name', label: 'Tool', sortable: false, render: _toolLink },
                { key: 'source', label: 'Source', sortable: false, render: _sourceBadge },
                { key: 'status', label: 'Decision', sortable: false, render: _statusBadge },
                { key: 'decided_by', label: 'Decided by', sortable: false, render: _decidedBy },
                { key: 'resume_status', label: 'Run', sortable: false, render: _resumeBadge },
                { label: 'Actions', align: 'right', render: (r) => _openButton(r, 'View') },
            ],
        });

        document.querySelector('[data-m-tabs]').addEventListener('m-tab-change', (event) => {
            if (event.detail.id === 'tab-history') {
                history.reload();
            } else {
                loadWaiting();
            }
        });
        loadWaiting();
        _openLinked();
        setInterval(() => {
            if (document.hidden || current) return;
            if (!document.getElementById('panel-waiting').hidden) loadWaiting();
        }, REFRESH_MS);
    }

    async function loadWaiting() {
        try {
            const resp = await fetch(`${API}?status=pending&inbox=true&limit=500`, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
            const data = await resp.json();
            const list = data.requests || [];
            list.forEach((r) => rows.set(r.id, r));
            waiting.setRows(list);
            const count = document.getElementById('waitingCount');
            count.textContent = String(data.total || 0);
            count.hidden = !data.total;
            if (typeof refreshApprovalsCount === 'function') refreshApprovalsCount();
        } catch (err) {
            waiting.setError(err.message);
        }
    }

    // A notification links to /dashboard/approvals?id=<id>: open that request
    async function _openLinked() {
        const id = Number(new URLSearchParams(window.location.search).get('id'));
        if (!Number.isInteger(id) || id <= 0) return;
        try {
            const resp = await fetch(`${API}?id=${id}&limit=1`, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
            const request = ((await resp.json()).requests || [])[0];
            if (!request) {
                _notify('That approval request is not in this workspace.', 'error');
                return;
            }
            rows.set(request.id, request);
            open(request.id);
        } catch (err) {
            _notify(`Could not open the approval request: ${err.message}`, 'error');
        }
    }

    async function _fetchHistory({ offset, limit, q }) {
        const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
        const status = document.getElementById('historyStatus').value;
        const source = document.getElementById('historySource').value;
        if (status) params.set('status', status);
        if (source) params.set('source', source);
        if (q) params.set('q', q);
        const resp = await fetch(`${API}?${params}`, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
        const data = await resp.json();
        (data.requests || []).forEach((r) => rows.set(r.id, r));
        return { rows: data.requests || [], total: data.total || 0 };
    }

    function reloadHistory() {
        history.reload({ firstPage: true });
    }

    // ── The dialog ───────────────────────────────────────────

    function open(id) {
        const r = rows.get(id);
        if (!r) return;
        current = r;
        const decidable = r.status === 'pending' && _inboxDecides(r);
        document.getElementById('approvalDialogTitle').textContent = decidable
            ? `Approve ${r.tool_name}?` : `${r.tool_name}: ${_statusLabel(r)}`;

        const details = [
            ['Agent', esc(r.agent_name)],
            ['Tool', `<span class="m-mono">${esc(r.tool_name)}</span>`],
            ['Source', _sourceBadge(r)],
            ['Started by', esc(r.user_id)],
            ['Requested', esc(_when(r.requested_at))],
        ];
        if (r.status === 'pending') {
            if (r.expires_at) details.push(['Expires', esc(`${_when(r.expires_at)} (${_until(r.expires_at)})`)]);
            if (!_inboxDecides(r)) details.push(['Answered by', 'the person in the chat']);
            else if (r.approver === 'either') details.push(['Answered by', 'the inbox or the person in the chat, whichever is first']);
        } else {
            details.push(['Decision', _statusBadge(r)]);
            if (r.decided_at) details.push(['Decided', esc(_when(r.decided_at))]);
            if (r.decided_by) details.push(['Decided by', _decidedBy(r)]);
            if (r.note) details.push(['Note', esc(r.note)]);
            if (r.resume_status) details.push(['Run', _resumeBadge(r)]);
        }
        document.getElementById('approvalDetails').innerHTML =
            details.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('');

        const hint = _humanHint(r.hint);
        document.getElementById('approvalHint').hidden = !hint;
        document.getElementById('approvalHintText').textContent = hint;
        document.getElementById('approvalArgs').textContent = _json(r.tool_args);

        const result = r.resume_result;
        document.getElementById('approvalResult').hidden = !result;
        document.getElementById('approvalResultText').textContent = result || '';

        document.getElementById('approvalNoteField').hidden = !decidable;
        document.getElementById('approvalNote').value = '';
        document.getElementById('approvalApprove').hidden = !decidable;
        document.getElementById('approvalReject').hidden = !decidable;
        _setBusy(false);

        const link = document.getElementById('approvalSessionLink');
        link.href = '/dashboard/sessions?' + new URLSearchParams({
            session_id: r.session_id, app_name: r.app_name, user_id: r.user_id,
        });

        mateDialog.open('approvalDialog', {
            focus: decidable ? 'approvalNote' : null,
            onClose: () => { current = null; },
        });
    }

    function closeDialog() {
        mateDialog.close('approvalDialog');
    }

    async function decide(decision) {
        if (!current || deciding) return;
        const r = current;
        _setBusy(true);
        try {
            const resp = await fetch(`${API}/${r.id}/decide`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision, note: document.getElementById('approvalNote').value.trim() || null }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data.detail || `Server returned ${resp.status}`);
            closeDialog();
            _notify(decision === 'approve'
                ? `Approved. ${r.agent_name} continues with ${r.tool_name} in the background.`
                : `Rejected. ${r.agent_name} continues without running ${r.tool_name}.`, 'success');
        } catch (err) {
            // Someone else may have decided it first (409): the lists show what happened
            _setBusy(false);
            _notify(`Could not ${decision} the request: ${err.message}`, 'error');
        }
        loadWaiting();
        history.reload();
    }

    // A tool without a hint of its own gets ADK's, which tells a client how to answer,
    // not a person what is asked: show none instead
    function _humanHint(hint) {
        if (!hint || /responding with a FunctionResponse/.test(hint)) return '';
        return hint;
    }

    function _setBusy(busy) {
        deciding = busy;
        document.getElementById('approvalApprove').disabled = busy;
        document.getElementById('approvalReject').disabled = busy;
    }

    // ── Cells ────────────────────────────────────────────────

    function _openButton(r, label) {
        return `<button type="button" class="m-btn m-btn-secondary m-btn-sm" data-click="ApprovalsPage.open"
            data-args="${mateActions.attr([r.id])}" aria-label="${esc(label)} ${esc(r.tool_name)} of ${esc(r.agent_name)}">${esc(label)}</button>`;
    }

    // The tool's name opens the request too: on a phone the Actions column is off screen
    function _toolLink(r) {
        return `<button type="button" class="m-link m-mono" data-click="ApprovalsPage.open"
            data-args="${mateActions.attr([r.id])}">${esc(r.tool_name)}</button>`;
    }

    function _sourceBadge(r) {
        const tone = { trigger: 'm-badge-violet', chat: '', api: 'm-badge-accent', a2a: 'm-badge-accent', mcp: 'm-badge-accent' }[r.source];
        return `<span class="m-badge ${tone || ''}">${esc(r.source)}</span>`;
    }

    // approval_service.inbox_decides: a run nobody chats in, or an agent that sends requests here
    function _inboxDecides(r) {
        return r.source !== 'chat' || (r.approver && r.approver !== 'chat');
    }

    function _statusLabel(r) {
        if (r.status === 'pending') return _inboxDecides(r) ? 'waiting' : 'waiting in the chat';
        return r.status;
    }

    function _statusBadge(r) {
        const tone = {
            pending: 'm-badge-accent', approved: 'm-badge-success', rejected: 'm-badge-danger',
            expired: 'm-badge-warning', cancelled: '',
        }[r.status];
        return `<span class="m-badge ${tone || ''}">${esc(_statusLabel(r))}</span>`;
    }

    function _decidedBy(r) {
        if (!r.decided_by && !r.decided_via) return '<span class="m-muted">—</span>';
        const via = r.decided_via && r.decided_via !== r.decided_by
            ? ` <span class="m-muted">(${esc(r.decided_via)})</span>` : '';
        return `${esc(r.decided_by || 'system')}${via}`;
    }

    function _resumeBadge(r) {
        if (!r.resume_status) return '<span class="m-muted">—</span>';
        const look = {
            resuming: ['m-badge-accent', 'resuming'],
            ok: ['m-badge-success', 'continued'],
            error: ['m-badge-danger', 'failed'],
        }[r.resume_status] || ['', r.resume_status];
        return `<span class="m-badge ${look[0]}">${esc(look[1])}</span>`;
    }

    function _json(value) {
        if (value === null || value === undefined || value === '') return '(none)';
        if (typeof value === 'string') {
            try { return JSON.stringify(JSON.parse(value), null, 2); } catch (e) { return value; }
        }
        return JSON.stringify(value, null, 2);
    }

    // The API's times are UTC; one without a zone is read as UTC
    function _date(iso) {
        if (!iso) return null;
        return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z');
    }

    function _when(iso) {
        const d = _date(iso);
        return d ? d.toLocaleString() : '';
    }

    function _span(ms) {
        const m = Math.round(Math.abs(ms) / 60000);
        if (m < 1) return 'less than a minute';
        if (m < 60) return `${m} min`;
        const h = Math.round(m / 60);
        if (h < 48) return `${h} h`;
        return `${Math.round(h / 24)} days`;
    }

    function _ago(iso) {
        const d = _date(iso);
        return d ? _span(Date.now() - d.getTime()) : '';
    }

    function _until(iso) {
        const d = _date(iso);
        if (!d) return 'never';
        const ms = d.getTime() - Date.now();
        return ms <= 0 ? 'due now' : `in ${_span(ms)}`;
    }

    function _notify(message, type) {
        if (typeof showNotification === 'function') showNotification(message, type);
    }

    document.addEventListener('DOMContentLoaded', init);

    return { open, closeDialog, decide, reloadHistory };
})();
// A top-level const is not on window, where csp-actions.js looks names up
window.ApprovalsPage = ApprovalsPage;
