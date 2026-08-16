/**
 * Alerts Page — MATE Dashboard
 * CRUD for alert rules plus a non-destructive test fire.
 */
const AlertPage = (function () {
    'use strict';

    let _rules = [];
    let _editId = null;

    const CONDITION_LABELS = {
        agent_error_count: 'Agent errors',
        budget_threshold: 'Budget threshold',
        guardrail_count: 'Guardrail hits',
    };

    const esc = s => String(s === null || s === undefined ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    function init() {
        loadRules();
    }

    async function loadRules() {
        const params = new URLSearchParams();
        const condition = document.getElementById('filterCondition')?.value || '';
        const scope = document.getElementById('filterScope')?.value || '';
        if (condition) params.set('condition_type', condition);
        if (scope) params.set('scope', scope);

        try {
            const resp = await fetch('/dashboard/api/alert-rules?' + params.toString(),
                { credentials: 'same-origin' });
            const data = await resp.json();
            _rules = data.alert_rules || [];
            _render();
        } catch (e) {
            document.getElementById('ruleTableBody').innerHTML =
                '<tr><td colspan="8" class="px-3 py-6 text-center text-red-500">Failed to load rules</td></tr>';
        }
    }

    function _describeCondition(rule) {
        const c = rule.condition_config || {};
        if (rule.condition_type === 'budget_threshold') {
            return `${esc(c.threshold_pct ?? 90)}% of ${esc(c.period || 'day')} budget`;
        }
        return `${esc(c.threshold ?? '?')} in ${esc(c.window_minutes ?? '?')} min`;
    }

    function _describeDestination(rule) {
        const d = rule.destination_config || {};
        return rule.destination_type === 'email' ? esc(d.to || '—') : esc(d.url || '—');
    }

    function _render() {
        const body = document.getElementById('ruleTableBody');
        if (!_rules.length) {
            body.innerHTML = '<tr><td colspan="8" class="px-3 py-6 text-center text-gray-500 dark:text-gray-400">No alert rules yet</td></tr>';
            return;
        }
        body.innerHTML = _rules.map(rule => {
            const lastFired = rule.last_fired_at
                ? new Date(rule.last_fired_at).toLocaleString()
                : 'never';
            const status = rule.is_enabled
                ? '<span class="text-green-600 dark:text-green-400">enabled</span>'
                : '<span class="text-gray-400">disabled</span>';
            const error = rule.last_error
                ? `<div class="text-red-500 mt-0.5" title="${esc(rule.last_error)}">delivery failed</div>`
                : '';
            return `
            <tr class="text-gray-700 dark:text-gray-300">
                <td class="px-3 py-2">${esc(rule.name)}</td>
                <td class="px-3 py-2">${esc(CONDITION_LABELS[rule.condition_type] || rule.condition_type)}<div class="text-gray-500">${_describeCondition(rule)}</div></td>
                <td class="px-3 py-2">${esc(rule.scope)}${rule.scope_id ? ': ' + esc(rule.scope_id) : ''}</td>
                <td class="px-3 py-2 max-w-[16rem] truncate">${_describeDestination(rule)}</td>
                <td class="px-3 py-2">${esc(rule.cooldown_seconds)}s</td>
                <td class="px-3 py-2">${esc(lastFired)}<div class="text-gray-500">${esc(rule.fire_count)} fired</div>${error}</td>
                <td class="px-3 py-2">${status}</td>
                <td class="px-3 py-2 text-right whitespace-nowrap">
                    <button onclick="AlertPage.testRule(${rule.id})" class="text-blue-600 hover:text-blue-800 mr-2" title="Test"><i class="fas fa-vial"></i></button>
                    <button onclick="AlertPage.toggleRule(${rule.id})" class="text-gray-600 hover:text-gray-800 mr-2" title="Enable/disable"><i class="fas fa-power-off"></i></button>
                    <button onclick="AlertPage.openEditModal(${rule.id})" class="text-gray-600 hover:text-gray-800 mr-2" title="Edit"><i class="fas fa-pen"></i></button>
                    <button onclick="AlertPage.deleteRule(${rule.id})" class="text-red-600 hover:text-red-800" title="Delete"><i class="fas fa-trash"></i></button>
                </td>
            </tr>`;
        }).join('');
    }

    function onConditionChange() {
        const isBudget = document.getElementById('formCondition').value === 'budget_threshold';
        document.getElementById('budgetFields').classList.toggle('hidden', !isBudget);
        document.getElementById('countFields').classList.toggle('hidden', isBudget);
    }

    function onDestinationChange() {
        const isEmail = document.getElementById('formDestination').value === 'email';
        document.getElementById('emailFields').classList.toggle('hidden', !isEmail);
        document.getElementById('httpFields').classList.toggle('hidden', isEmail);
    }

    function openCreateModal() {
        _editId = null;
        document.getElementById('ruleModalTitle').textContent = 'New Alert Rule';
        document.getElementById('ruleForm').reset();
        onConditionChange();
        onDestinationChange();
        document.getElementById('ruleModal').classList.remove('hidden');
    }

    function openEditModal(id) {
        const rule = _rules.find(r => r.id === id);
        if (!rule) return;
        _editId = id;
        document.getElementById('ruleModalTitle').textContent = 'Edit Alert Rule';
        document.getElementById('formName').value = rule.name || '';
        document.getElementById('formCondition').value = rule.condition_type;
        document.getElementById('formScope').value = rule.scope;
        document.getElementById('formScopeId').value = rule.scope_id || '';
        document.getElementById('formCooldown').value = rule.cooldown_seconds;
        document.getElementById('formDestination').value = rule.destination_type;

        const c = rule.condition_config || {};
        document.getElementById('formThreshold').value = c.threshold ?? 5;
        document.getElementById('formWindow').value = c.window_minutes ?? 15;
        document.getElementById('formThresholdPct').value = c.threshold_pct ?? 90;
        document.getElementById('formPeriod').value = c.period || 'day';
        document.getElementById('formTokenLimit').value = c.token_limit || '';

        const d = rule.destination_config || {};
        // headers come back redacted, so only the address fields are safe to prefill
        document.getElementById('formUrl').value = d.url || '';
        document.getElementById('formTo').value = d.to || '';

        onConditionChange();
        onDestinationChange();
        document.getElementById('ruleModal').classList.remove('hidden');
    }

    function closeModal() {
        document.getElementById('ruleModal').classList.add('hidden');
        _editId = null;
    }

    function _collectForm() {
        const conditionType = document.getElementById('formCondition').value;
        const destinationType = document.getElementById('formDestination').value;

        let conditionConfig;
        if (conditionType === 'budget_threshold') {
            conditionConfig = {
                threshold_pct: parseInt(document.getElementById('formThresholdPct').value, 10),
                period: document.getElementById('formPeriod').value,
            };
            const limit = document.getElementById('formTokenLimit').value;
            if (limit) conditionConfig.token_limit = parseInt(limit, 10);
        } else {
            conditionConfig = {
                threshold: parseInt(document.getElementById('formThreshold').value, 10),
                window_minutes: parseInt(document.getElementById('formWindow').value, 10),
            };
        }

        const destinationConfig = destinationType === 'email'
            ? { to: document.getElementById('formTo').value.trim() }
            : { url: document.getElementById('formUrl').value.trim() };

        return {
            name: document.getElementById('formName').value.trim(),
            scope: document.getElementById('formScope').value,
            scope_id: document.getElementById('formScopeId').value.trim(),
            condition_type: conditionType,
            condition_config: conditionConfig,
            destination_type: destinationType,
            destination_config: destinationConfig,
            cooldown_seconds: parseInt(document.getElementById('formCooldown').value, 10),
        };
    }

    async function saveRule() {
        const payload = _collectForm();
        const url = _editId
            ? `/dashboard/api/alert-rules/${_editId}`
            : '/dashboard/api/alert-rules';
        try {
            const resp = await fetch(url, {
                method: _editId ? 'PUT' : 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                showNotification(err.detail || 'Failed to save rule', 'error');
                return;
            }
            closeModal();
            showNotification('Alert rule saved');
            loadRules();
        } catch (e) {
            showNotification('Failed to save rule', 'error');
        }
    }

    async function toggleRule(id) {
        const rule = _rules.find(r => r.id === id);
        if (!rule) return;
        try {
            await fetch(`/dashboard/api/alert-rules/${id}`, {
                method: 'PUT',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_enabled: !rule.is_enabled }),
            });
            loadRules();
        } catch (e) {
            showNotification('Failed to toggle rule', 'error');
        }
    }

    async function deleteRule(id) {
        const confirmed = await showConfirm('Delete this alert rule?');
        if (!confirmed) return;
        try {
            await fetch(`/dashboard/api/alert-rules/${id}`, {
                method: 'DELETE', credentials: 'same-origin',
            });
            loadRules();
        } catch (e) {
            showNotification('Failed to delete rule', 'error');
        }
    }

    async function testRule(id) {
        try {
            const resp = await fetch(`/dashboard/api/alert-rules/${id}/test`, {
                method: 'POST', credentials: 'same-origin',
            });
            const data = await resp.json();
            if (!resp.ok) {
                showNotification(data.detail || 'Test failed', 'error');
                return;
            }
            const r = data.result || {};
            const delivery = r.delivery || {};
            showNotification(
                `Measured ${r.value} against threshold ${r.threshold}. ` +
                `Would fire: ${r.would_fire ? 'yes' : 'no'}. ` +
                `Delivery: ${delivery.ok ? 'ok' : (delivery.detail || 'failed')}`,
                delivery.ok ? 'success' : 'error');
            loadRules();
        } catch (e) {
            showNotification('Test failed', 'error');
        }
    }

    return {
        init,
        loadRules,
        openCreateModal,
        openEditModal,
        closeModal,
        onConditionChange,
        onDestinationChange,
        saveRule,
        toggleRule,
        deleteRule,
        testRule,
    };
})();
