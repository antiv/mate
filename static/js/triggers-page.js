/**
 * Triggers Page — MATE Dashboard
 * Manages cron / webhook / stub trigger CRUD and execution.
 */
const TriggerPage = (function () {
    'use strict';

    let _triggers = [];
    let _editMode = false;

    // ------------------------------------------------------------------ //
    // Public API                                                           //
    // ------------------------------------------------------------------ //

    function init() {
        _populateSelects();
        loadTriggers();
        const projectSel = document.getElementById('triggerProject');
        if (projectSel) projectSel.addEventListener('change', onProjectChange);
    }

    async function loadTriggers() {
        const projectId = document.getElementById('filterProject')?.value || '';
        const typeFilter = document.getElementById('filterType')?.value || '';

        const params = new URLSearchParams();
        if (projectId) params.set('project_id', projectId);

        try {
            const resp = await fetch('/dashboard/api/triggers?' + params.toString(), {
                credentials: 'same-origin',
            });
            if (!resp.ok) throw new Error('Failed to load triggers');
            const data = await resp.json();
            _triggers = data.triggers || [];
            const filtered = typeFilter
                ? _triggers.filter(t => t.trigger_type === typeFilter)
                : _triggers;
            renderTable(filtered);
        } catch (err) {
            _showNotification('Failed to load triggers: ' + err.message, 'error');
        }
    }

    function renderTable(triggers) {
        const tbody = document.getElementById('triggerTableBody');
        if (!tbody) return;

        if (!triggers.length) {
            tbody.innerHTML = `<tr><td colspan="8" class="px-4 py-6 text-center text-gray-400 dark:text-gray-500">No triggers yet — click <strong>New Trigger</strong> to create one.</td></tr>`;
            return;
        }

        tbody.innerHTML = triggers.map(t => {
            const typeLabel = _typeLabel(t.trigger_type);
            const typeBadge = _typeBadge(t.trigger_type);
            const scheduleInfo = _scheduleInfo(t);
            const outputLabel = _outputLabel(t);
            const lastRun = t.last_fired_at ? _relativeTime(t.last_fired_at) : '<span class="text-gray-400">Never</span>';
            const lastStatus = _lastStatus(t);
            const enabledToggle = `
                <button onclick="TriggerPage.toggleTrigger(${t.id})" title="${t.is_enabled ? 'Disable' : 'Enable'}"
                    class="relative inline-flex items-center h-4 rounded-full w-7 transition-colors focus:outline-none ${t.is_enabled ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-600'}">
                    <span class="inline-block w-3 h-3 transform bg-white rounded-full transition-transform shadow ${t.is_enabled ? 'translate-x-3.5' : 'translate-x-0.5'}"></span>
                </button>`;

            return `<tr class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td class="px-4 py-2">
                    <div class="font-medium text-gray-900 dark:text-white">${_esc(t.name)}</div>
                    ${t.description ? `<div class="text-gray-400 text-xs truncate max-w-[160px]" title="${_esc(t.description)}">${_esc(t.description)}</div>` : ''}
                </td>
                <td class="px-4 py-2">${typeBadge}</td>
                <td class="px-4 py-2 text-gray-700 dark:text-gray-300 font-mono">${_esc(t.agent_name)}</td>
                <td class="px-4 py-2 text-gray-600 dark:text-gray-400 font-mono text-xs">${scheduleInfo}</td>
                <td class="px-4 py-2 text-gray-600 dark:text-gray-400">${outputLabel}</td>
                <td class="px-4 py-2 text-center">${enabledToggle}</td>
                <td class="px-4 py-2 text-gray-500 dark:text-gray-400">${lastRun} ${lastStatus}</td>
                <td class="px-4 py-2 text-right">
                    <div class="flex items-center justify-end gap-2">
                        <button onclick="TriggerPage.testFire(${t.id})" title="Test fire" class="text-green-600 dark:text-green-400 hover:text-green-800">
                            <i class="fas fa-play text-xs"></i>
                        </button>
                        <button onclick="TriggerPage.openEditModal(${t.id})" title="Edit" class="text-blue-600 dark:text-blue-400 hover:text-blue-800">
                            <i class="fas fa-edit text-xs"></i>
                        </button>
                        <button onclick="TriggerPage.deleteTrigger(${t.id})" title="Delete" class="text-red-500 dark:text-red-400 hover:text-red-700">
                            <i class="fas fa-trash text-xs"></i>
                        </button>
                    </div>
                </td>
            </tr>`;
        }).join('');
    }

    function openCreateModal() {
        _editMode = false;
        _resetForm();
        document.getElementById('triggerModalTitle').textContent = 'Create Trigger';
        document.getElementById('triggerSubmitText').textContent = 'Create Trigger';
        document.getElementById('triggerWebhookSection').classList.add('hidden');
        updateTypeVisibility();
        updateOutputVisibility();
        document.getElementById('triggerModal').classList.remove('hidden');
        document.getElementById('triggerName').focus();
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

        // Webhook URL
        if (trigger.trigger_type === 'webhook' && trigger.webhook_path) {
            const webhookUrl = window.location.origin + '/triggers/' + triggerId + '/fire';
            document.getElementById('triggerWebhookUrl').value = webhookUrl;
            document.getElementById('triggerWebhookSection').classList.remove('hidden');
        } else {
            document.getElementById('triggerWebhookSection').classList.add('hidden');
        }

        updateTypeVisibility();
        updateOutputVisibility();
        document.getElementById('triggerModal').classList.remove('hidden');
    }

    function closeModal() {
        document.getElementById('triggerModal').classList.add('hidden');
    }

    function updateTypeVisibility() {
        const type = document.getElementById('triggerType').value;
        const cronSection = document.getElementById('triggerCronSection');
        const stubWarning = document.getElementById('triggerStubWarning');

        cronSection.classList.toggle('hidden', type !== 'cron');
        stubWarning.classList.toggle('hidden', !['file_watch', 'event_bus'].includes(type));

        // Webhook section is only shown in edit mode when type=webhook and path exists
        if (!_editMode) {
            document.getElementById('triggerWebhookSection').classList.add('hidden');
        }
    }

    function updateOutputVisibility() {
        const type = document.getElementById('triggerOutputType').value;
        document.getElementById('outputMemoryConfig').classList.toggle('hidden', type !== 'memory_block');
        document.getElementById('outputHttpConfig').classList.toggle('hidden', type !== 'http_callback');
        document.getElementById('outputEmailConfig').classList.toggle('hidden', type !== 'email');
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
        };

        const btn = document.getElementById('triggerSubmitBtn');
        const loader = document.getElementById('triggerSubmitLoader');
        const btnText = document.getElementById('triggerSubmitText');
        btn.disabled = true;
        loader.classList.remove('hidden');
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
            if (result.fire_key) {
                _showFireKeyBanner(result.fire_key);
            }
            _showNotification(formId ? 'Trigger updated' : 'Trigger created');
            loadTriggers();
        } catch (err) {
            _showNotification('Error: ' + err.message, 'error');
        } finally {
            btn.disabled = false;
            loader.classList.add('hidden');
            btnText.textContent = formId ? 'Update Trigger' : 'Create Trigger';
        }
    }

    async function deleteTrigger(triggerId) {
        const trigger = _triggers.find(t => t.id === triggerId);
        if (!trigger) return;
        if (!confirm(`Delete trigger "${trigger.name}"? This cannot be undone.`)) return;
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
            } else {
                _showNotification('Fire returned error: ' + result.message, 'error');
            }
            loadTriggers();
        } catch (err) {
            _showNotification('Error: ' + err.message, 'error');
        }
    }

    async function regenerateKey() {
        const triggerId = document.getElementById('triggerFormId').value;
        if (!triggerId) return;
        if (!confirm('Regenerate the fire key? The old key will stop working immediately.')) return;
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
            if (result.fire_key) {
                _showFireKeyBanner(result.fire_key);
            }
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

    function _showFireKeyBanner(key) {
        const banner = document.getElementById('fireKeyBanner');
        const keyEl = document.getElementById('fireKeyValue');
        if (!banner || !keyEl) return;
        keyEl.textContent = key;
        banner.classList.remove('hidden');
        // Auto-hide after 60 seconds
        setTimeout(() => banner.classList.add('hidden'), 60000);
    }

    function _typeLabel(type) {
        return {cron: 'Cron', webhook: 'Webhook', file_watch: 'File Watch', event_bus: 'Event Bus'}[type] || type;
    }

    function _typeBadge(type) {
        const colors = {
            cron:       'bg-blue-100  dark:bg-blue-900  text-blue-800  dark:text-blue-200',
            webhook:    'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200',
            file_watch: 'bg-yellow-100 dark:bg-yellow-900 text-yellow-800 dark:text-yellow-200',
            event_bus:  'bg-purple-100 dark:bg-purple-900 text-purple-800 dark:text-purple-200',
        };
        const cls = colors[type] || 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300';
        return `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${cls}">${_typeLabel(type)}</span>`;
    }

    function _scheduleInfo(t) {
        if (t.trigger_type === 'cron') return _esc(t.cron_expression || '—');
        if (t.trigger_type === 'webhook' && t.webhook_path) return '<span class="text-gray-400">' + _esc(t.webhook_path) + '</span>';
        return '<span class="text-gray-400">—</span>';
    }

    function _outputLabel(t) {
        const labels = {memory_block: 'Memory Block', http_callback: 'HTTP', email: 'Email'};
        return labels[t.output_type] || t.output_type || '—';
    }

    function _lastStatus(t) {
        const r = t.last_result;
        if (!r) return '';
        const cls = r.status === 'ok' ? 'text-green-500' : r.status === 'skipped' ? 'text-yellow-500' : 'text-red-500';
        const icon = r.status === 'ok' ? 'fa-check-circle' : r.status === 'skipped' ? 'fa-minus-circle' : 'fa-exclamation-circle';
        return `<i class="fas ${icon} ${cls} ml-1" title="${_esc(r.message || r.status)}"></i>`;
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
    };
})();
