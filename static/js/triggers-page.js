/**
 * Triggers Page — MATE Dashboard
 * Manages cron / webhook / stub trigger CRUD and execution.
 */

// Handlers this file's markup calls through data-click and friends (csp-actions.js)
mateActions.allow('TriggerPage.applyTypeFilter', 'TriggerPage.closeModal', 'TriggerPage.deleteTrigger',
                  'TriggerPage.hideSecretsBanner', 'TriggerPage.loadTriggers',
                  'TriggerPage.openCreateModal', 'TriggerPage.openEditModal',
                  'TriggerPage.regenerateKey', 'TriggerPage.regenerateSigningSecret',
                  'TriggerPage.submitForm', 'TriggerPage.testFire', 'TriggerPage.toggleTrigger',
                  'TriggerPage.updateOutputVisibility', 'TriggerPage.updateTypeVisibility');
const TriggerPage = (function () {
    'use strict';

    let _triggers = [];
    let _editMode = false;
    let _table = null;

    // ------------------------------------------------------------------ //
    // Public API                                                           //
    // ------------------------------------------------------------------ //

    function init() {
        _populateSelects();
        _table = _buildTable();
        loadTriggers();
        const projectSel = document.getElementById('triggerProject');
        if (projectSel) projectSel.addEventListener('change', onProjectChange);
    }

    async function loadTriggers() {
        const projectId = document.getElementById('filterProject')?.value || '';

        const params = new URLSearchParams();
        if (projectId) params.set('project_id', projectId);

        try {
            const resp = await fetch('/dashboard/api/triggers?' + params.toString(), {
                credentials: 'same-origin',
            });
            if (!resp.ok) throw new Error('Failed to load triggers');
            const data = await resp.json();
            _triggers = data.triggers || [];
            _table.setRows(_triggers);
        } catch (err) {
            _table.setError(err.message);
            _showNotification('Failed to load triggers: ' + err.message, 'error');
        }
    }

    // The type filter narrows the loaded list; the project filter asks the API again
    function applyTypeFilter() {
        _table.reload();
    }

    function _buildTable() {
        const esc = MateTable.esc;
        return new MateTable(document.getElementById('triggerTable'), {
            noun: ['trigger', 'triggers'],
            label: 'Triggers',
            empty: 'No triggers yet. Click New Trigger to create one.',
            filters: document.getElementById('triggerFilters'),
            filter: (t) => {
                const type = document.getElementById('filterType')?.value || '';
                return !type || t.trigger_type === type;
            },
            columns: [
                {
                    key: 'name', label: 'Name',
                    search: (t) => `${t.name || ''} ${t.description || ''}`,
                    render: (t) => `<span class="m-cell-strong">${esc(t.name)}</span>`
                        + (t.description ? `<span class="m-cell-sub" title="${esc(t.description)}">${esc(t.description)}</span>` : ''),
                },
                { key: 'trigger_type', label: 'Type', value: (t) => _typeLabel(t.trigger_type), render: (t) => _typeBadge(t.trigger_type) },
                { key: 'agent_name', label: 'Agent', className: 'm-mono' },
                { key: 'schedule', label: 'Schedule / Path', className: 'm-mono', value: _scheduleText, render: _scheduleInfo },
                { key: 'output_type', label: 'Output', value: _outputLabel },
                {
                    key: 'is_enabled', label: 'Enabled', align: 'center', searchable: false,
                    render: (t) => `<button type="button" class="m-switch" role="switch" aria-checked="${!!t.is_enabled}"
                        aria-label="Enabled: ${esc(t.name)}" title="${t.is_enabled ? 'Disable' : 'Enable'}"
                        data-click="TriggerPage.toggleTrigger" data-args="[${t.id}]"></button>`,
                },
                {
                    key: 'last_fired_at', label: 'Last Run', searchable: false,
                    render: (t) => (t.last_fired_at ? _relativeTime(t.last_fired_at) : '<span class="m-muted">Never</span>') + _lastStatus(t),
                },
                {
                    label: 'Actions', align: 'right',
                    render: (t) => `<span class="m-cell-actions">
                        <button type="button" class="m-btn m-btn-ghost m-btn-icon" title="Test fire" aria-label="Test fire ${esc(t.name)}"
                            data-click="TriggerPage.testFire" data-args="[${t.id}]"><i class="fas fa-play" aria-hidden="true"></i></button>
                        <button type="button" class="m-btn m-btn-ghost m-btn-icon" title="Edit" aria-label="Edit ${esc(t.name)}"
                            data-click="TriggerPage.openEditModal" data-args="[${t.id}]"><i class="fas fa-edit" aria-hidden="true"></i></button>
                        <button type="button" class="m-btn m-btn-ghost m-btn-danger m-btn-icon" title="Delete" aria-label="Delete ${esc(t.name)}"
                            data-click="TriggerPage.deleteTrigger" data-args="[${t.id}]"><i class="fas fa-trash" aria-hidden="true"></i></button>
                    </span>`,
                },
            ],
        });
    }

    function openCreateModal() {
        _editMode = false;
        _resetForm();
        document.getElementById('triggerModalTitle').textContent = 'Create Trigger';
        document.getElementById('triggerSubmitText').textContent = 'Create Trigger';
        document.getElementById('triggerWebhookSection').hidden = true;
        updateTypeVisibility();
        updateOutputVisibility();
        mateDialog.open('triggerModal', { focus: 'triggerName' });
    }

    function openEditModal(triggerId) {
        const trigger = _triggers.find(t => t.id === triggerId);
        if (!trigger) return;
        _editMode = true;
        _resetForm();

        document.getElementById('triggerFormId').value = trigger.id;
        document.getElementById('triggerModalTitle').textContent = 'Edit Trigger: ' + trigger.name;
        document.getElementById('triggerSubmitText').textContent = 'Update Trigger';
        document.getElementById('triggerName').value = trigger.name || '';
        document.getElementById('triggerDescription').value = trigger.description || '';
        document.getElementById('triggerType').value = trigger.trigger_type || 'cron';
        // Set project first so onProjectChange() populates the agent dropdown
        document.getElementById('triggerProject').value = trigger.project_id || '';
        onProjectChange();
        document.getElementById('triggerAgent').value = trigger.agent_name || '';
        document.getElementById('triggerPrompt').value = trigger.prompt || '';
        document.getElementById('triggerCronExpr').value = trigger.cron_expression || '';
        document.getElementById('triggerOutputType').value = trigger.output_type || 'memory_block';

        // Populate output config
        const cfg = trigger.output_config || {};
        document.getElementById('outputMemoryLabel').value = cfg.label || '';
        document.getElementById('outputHttpUrl').value = cfg.url || '';
        document.getElementById('outputHttpHeaders').value = cfg.headers ? JSON.stringify(cfg.headers, null, 2) : '';
        document.getElementById('outputEmailTo').value = cfg.to || '';
        document.getElementById('outputEmailSubject').value = cfg.subject || '';

        document.getElementById('triggerRequireSignature').checked = !!trigger.require_signature;

        // Webhook URL
        if (trigger.trigger_type === 'webhook' && trigger.webhook_path) {
            const webhookUrl = window.location.origin + '/triggers/' + triggerId + '/fire';
            document.getElementById('triggerWebhookUrl').value = webhookUrl;
            document.getElementById('triggerWebhookSection').hidden = false;
        } else {
            document.getElementById('triggerWebhookSection').hidden = true;
        }

        updateTypeVisibility();
        updateOutputVisibility();
        mateDialog.open('triggerModal', { focus: 'triggerName' });
    }

    function closeModal() {
        mateDialog.close('triggerModal');
    }

    function updateTypeVisibility() {
        const type = document.getElementById('triggerType').value;
        const cronSection = document.getElementById('triggerCronSection');
        const stubWarning = document.getElementById('triggerStubWarning');

        cronSection.hidden = type !== 'cron';
        stubWarning.hidden = !['file_watch', 'event_bus'].includes(type);

        // Webhook section is only shown in edit mode when type=webhook and path exists
        if (!_editMode) {
            document.getElementById('triggerWebhookSection').hidden = true;
        }
    }

    function updateOutputVisibility() {
        const type = document.getElementById('triggerOutputType').value;
        document.getElementById('outputMemoryConfig').hidden = type !== 'memory_block';
        document.getElementById('outputHttpConfig').hidden = type !== 'http_callback';
        document.getElementById('outputEmailConfig').hidden = type !== 'email';
    }

    async function submitForm() {
        const formId = document.getElementById('triggerFormId').value;
        const triggerType = document.getElementById('triggerType').value;
        const outputType = document.getElementById('triggerOutputType').value;

        const outputConfig = _buildOutputConfig(outputType);
        if (!outputConfig) return; // validation failed

        const name = document.getElementById('triggerName').value.trim();
        const agentName = document.getElementById('triggerAgent').value;
        const projectId = document.getElementById('triggerProject').value;
        const prompt = document.getElementById('triggerPrompt').value.trim();

        if (!name || !agentName || !projectId || !prompt) {
            _showNotification('Name, agent, project and prompt are required', 'error');
            return;
        }
        if (triggerType === 'cron' && !document.getElementById('triggerCronExpr').value.trim()) {
            _showNotification('Cron expression is required for cron triggers', 'error');
            return;
        }

        const payload = {
            name,
            description: document.getElementById('triggerDescription').value.trim(),
            trigger_type: triggerType,
            agent_name: agentName,
            project_id: parseInt(projectId),
            prompt,
            cron_expression: triggerType === 'cron' ? document.getElementById('triggerCronExpr').value.trim() : null,
            output_type: outputType,
            output_config: outputConfig,
            require_signature: document.getElementById('triggerRequireSignature').checked,
        };

        const btn = document.getElementById('triggerSubmitBtn');
        const loader = document.getElementById('triggerSubmitLoader');
        const btnText = document.getElementById('triggerSubmitText');
        btn.disabled = true;
        loader.hidden = false;
        btnText.textContent = formId ? 'Updating…' : 'Creating…';

        try {
            const url = formId ? `/dashboard/api/triggers/${formId}` : '/dashboard/api/triggers';
            const method = formId ? 'PUT' : 'POST';
            const resp = await fetch(url, {
                method,
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.detail || 'Request failed');
            closeModal();
            _showSecretsBanner(result.fire_key, result.signing_secret);
            _showNotification(formId ? 'Trigger updated' : 'Trigger created');
            loadTriggers();
        } catch (err) {
            _showNotification('Error: ' + err.message, 'error');
        } finally {
            btn.disabled = false;
            loader.hidden = true;
            btnText.textContent = formId ? 'Update Trigger' : 'Create Trigger';
        }
    }

    async function deleteTrigger(triggerId) {
        const trigger = _triggers.find(t => t.id === triggerId);
        if (!trigger) return;
        if (!await showConfirm(`Delete trigger "${trigger.name}"? This cannot be undone.`, "Delete trigger", "Delete", "Cancel", "danger")) return;
        try {
            const resp = await fetch(`/dashboard/api/triggers/${triggerId}`, {
                method: 'DELETE',
                credentials: 'same-origin',
            });
            if (!resp.ok) {
                const data = await resp.json();
                throw new Error(data.detail || 'Delete failed');
            }
            _showNotification('Trigger deleted');
            loadTriggers();
        } catch (err) {
            _showNotification('Error: ' + err.message, 'error');
        }
    }

    async function toggleTrigger(triggerId) {
        try {
            const resp = await fetch(`/dashboard/api/triggers/${triggerId}/toggle`, {
                method: 'POST',
                credentials: 'same-origin',
            });
            if (!resp.ok) {
                const data = await resp.json();
                throw new Error(data.detail || 'Toggle failed');
            }
            loadTriggers();
        } catch (err) {
            _showNotification('Error: ' + err.message, 'error');
        }
    }

    async function testFire(triggerId) {
        const trigger = _triggers.find(t => t.id === triggerId);
        const name = trigger ? trigger.name : `#${triggerId}`;
        _showNotification(`Firing "${name}"…`, 'info');
        try {
            const resp = await fetch(`/dashboard/api/triggers/${triggerId}/test-fire`, {
                method: 'POST',
                credentials: 'same-origin',
            });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || 'Fire failed');
            const result = data.result || {};
            if (result.status === 'ok') {
                const preview = (result.agent_response || '').substring(0, 120);
                _showNotification(`Fired OK${preview ? ': "' + preview + (result.agent_response.length > 120 ? '…"' : '"') : ''}`, 'success', 8000);
            } else if (result.status === 'skipped') {
                _showNotification('Trigger type not yet implemented: ' + result.message, 'warning', 5000);
            } else if (result.status === 'awaiting_approval') {
                _showNotification('Waiting for approval: the agent asked before running a tool. The answer is sent once the request is decided.', 'info', 8000);
            } else {
                _showNotification('Fire returned error: ' + result.message, 'error');
            }
            loadTriggers();
        } catch (err) {
            _showNotification('Error: ' + err.message, 'error');
        }
    }

    async function regenerateSigningSecret() {
        const triggerId = document.getElementById('triggerFormId').value;
        if (!triggerId) return;
        if (!await showConfirm('Regenerate the signing secret? Senders using the old secret will start failing immediately.', 'Regenerate secret', 'Regenerate', 'Cancel', 'warning')) return;
        try {
            const resp = await fetch(`/dashboard/api/triggers/${triggerId}`, {
                method: 'PUT',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({regenerate_signing_secret: true}),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.detail || 'Failed');
            closeModal();
            _showSecretsBanner(result.fire_key, result.signing_secret);
            loadTriggers();
        } catch (err) {
            _showNotification('Error: ' + err.message, 'error');
        }
    }

    async function regenerateKey() {
        const triggerId = document.getElementById('triggerFormId').value;
        if (!triggerId) return;
        if (!await showConfirm('Regenerate the fire key? The old key will stop working immediately.', 'Regenerate key', 'Regenerate', 'Cancel', 'warning')) return;
        try {
            const resp = await fetch(`/dashboard/api/triggers/${triggerId}`, {
                method: 'PUT',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({regenerate_fire_key: true}),
            });
            const result = await resp.json();
            if (!resp.ok) throw new Error(result.detail || 'Failed');
            closeModal();
            _showSecretsBanner(result.fire_key, result.signing_secret);
            loadTriggers();
        } catch (err) {
            _showNotification('Error: ' + err.message, 'error');
        }
    }

    // ------------------------------------------------------------------ //
    // Private helpers                                                      //
    // ------------------------------------------------------------------ //

    function _populateSelects() {
        const data = window._triggerPageData || {};
        const projectSel = document.getElementById('triggerProject');
        if (!projectSel) return;

        (data.projects || []).forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.name;
            projectSel.appendChild(opt);
        });
        // Agent dropdown is populated on project selection via onProjectChange()
    }

    function onProjectChange() {
        const projectId = parseInt(document.getElementById('triggerProject').value, 10);
        const agentSel = document.getElementById('triggerAgent');
        if (!agentSel) return;

        // Remove all options except placeholder
        agentSel.innerHTML = '';

        if (!projectId) {
            agentSel.appendChild(_makeOption('', '— select project first —'));
            return;
        }

        const data = window._triggerPageData || {};
        const rootAgents = (data.agents || []).filter(a => {
            if (a.project_id !== projectId) return false;
            // Root agents have no parent_agents
            const parents = a.parent_agents;
            return !parents || (Array.isArray(parents) && parents.length === 0);
        });

        if (!rootAgents.length) {
            agentSel.appendChild(_makeOption('', '— no root agents in this project —'));
            return;
        }

        agentSel.appendChild(_makeOption('', '— select agent —'));
        rootAgents.forEach(a => {
            agentSel.appendChild(_makeOption(a.name, a.name + (a.type ? ` (${a.type})` : '')));
        });
    }

    function _makeOption(value, text) {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = text;
        return opt;
    }

    function _resetForm() {
        ['triggerFormId', 'triggerName', 'triggerDescription', 'triggerPrompt',
         'triggerCronExpr', 'outputMemoryLabel', 'outputHttpUrl', 'outputHttpHeaders',
         'outputEmailTo', 'outputEmailSubject'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        document.getElementById('triggerType').value = 'cron';
        document.getElementById('triggerOutputType').value = 'memory_block';
        const sigChk = document.getElementById('triggerRequireSignature');
        if (sigChk) sigChk.checked = false;
        const projSel = document.getElementById('triggerProject');
        if (projSel) projSel.value = '';
        // Reset agent dropdown to "select project first" state
        const agentSel = document.getElementById('triggerAgent');
        if (agentSel) {
            agentSel.innerHTML = '';
            agentSel.appendChild(_makeOption('', '— select project first —'));
        }
    }

    function _buildOutputConfig(outputType) {
        if (outputType === 'memory_block') {
            return {label: document.getElementById('outputMemoryLabel').value.trim() || null};
        }
        if (outputType === 'http_callback') {
            const url = document.getElementById('outputHttpUrl').value.trim();
            if (!url) {
                _showNotification('Callback URL is required for HTTP callback output', 'error');
                return null;
            }
            let headers = {};
            const raw = document.getElementById('outputHttpHeaders').value.trim();
            if (raw) {
                try { headers = JSON.parse(raw); } catch (e) {
                    _showNotification('Headers must be valid JSON', 'error');
                    return null;
                }
            }
            return {url, headers};
        }
        if (outputType === 'email') {
            const to = document.getElementById('outputEmailTo').value.trim();
            if (!to) {
                _showNotification('Recipient email is required', 'error');
                return null;
            }
            return {to, subject: document.getElementById('outputEmailSubject').value.trim() || 'MATE Trigger Result'};
        }
        return {};
    }

    // Both secrets are shown once and never again, so they share one banner.
    // Either may be absent: rotating one does not reissue the other.
    function _showSecretsBanner(fireKey, signingSecret) {
        const banner = document.getElementById('fireKeyBanner');
        if (!banner || (!fireKey && !signingSecret)) return;

        const keyRow = document.getElementById('fireKeyRow');
        if (fireKey) document.getElementById('fireKeyValue').textContent = fireKey;
        keyRow.hidden = !fireKey;

        const secretRow = document.getElementById('signingSecretRow');
        if (signingSecret) document.getElementById('signingSecretValue').textContent = signingSecret;
        secretRow.hidden = !signingSecret;

        banner.hidden = false;
        // Auto-hide after 60 seconds
        setTimeout(hideSecretsBanner, 60000);
    }

    function hideSecretsBanner() {
        document.getElementById('fireKeyBanner').hidden = true;
    }

    function _typeLabel(type) {
        return {cron: 'Cron', webhook: 'Webhook', file_watch: 'File Watch', event_bus: 'Event Bus'}[type] || type;
    }

    function _typeBadge(type) {
        const tones = { cron: 'm-badge-accent', webhook: 'm-badge-success', file_watch: 'm-badge-warning', event_bus: 'm-badge-violet' };
        return `<span class="m-badge ${tones[type] || ''}">${_esc(_typeLabel(type))}</span>`;
    }

    function _scheduleText(t) {
        if (t.trigger_type === 'cron') return t.cron_expression || '';
        if (t.trigger_type === 'webhook') return t.webhook_path || '';
        return '';
    }

    function _scheduleInfo(t) {
        if (t.trigger_type === 'cron') return _esc(t.cron_expression || '—');
        if (t.trigger_type === 'webhook' && t.webhook_path) return '<span class="m-muted">' + _esc(t.webhook_path) + '</span>';
        return '<span class="m-muted">—</span>';
    }

    function _outputLabel(t) {
        const labels = {memory_block: 'Memory Block', http_callback: 'HTTP', email: 'Email'};
        return labels[t.output_type] || t.output_type || '—';
    }

    function _lastStatus(t) {
        const r = t.last_result;
        if (!r) return '';
        const looks = {
            ok: ['fa-check-circle', 'm-tone-success'],
            skipped: ['fa-minus-circle', 'm-tone-warning'],
            awaiting_approval: ['fa-hourglass-half', 'm-tone-accent'],
        };
        const [icon, tone] = looks[r.status] || ['fa-exclamation-circle', 'm-tone-danger'];
        const text = _esc(r.message || r.status);
        return ` <i class="fas ${icon} ${tone}" title="${text}" aria-label="${text}"></i>`;
    }

    function _relativeTime(isoStr) {
        if (!isoStr) return '';
        const diff = Date.now() - new Date(isoStr).getTime();
        const m = Math.floor(diff / 60000);
        if (m < 1) return 'just now';
        if (m < 60) return m + 'm ago';
        const h = Math.floor(m / 60);
        if (h < 24) return h + 'h ago';
        return Math.floor(h / 24) + 'd ago';
    }

    function _esc(str) {
        if (!str) return '';
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function _showNotification(message, type, duration) {
        if (typeof showNotification === 'function') {
            showNotification(message, type, duration);
            return;
        }
        // Fallback if global showNotification not available
        console.log(`[${type || 'info'}] ${message}`);
    }

    return {
        init,
        loadTriggers,
        applyTypeFilter,
        openCreateModal,
        openEditModal,
        closeModal,
        onProjectChange,
        updateTypeVisibility,
        updateOutputVisibility,
        submitForm,
        deleteTrigger,
        toggleTrigger,
        testFire,
        regenerateKey,
        regenerateSigningSecret,
        hideSecretsBanner,
    };
})();
// A top-level const is not on window, where csp-actions.js looks names up
window.TriggerPage = TriggerPage;
