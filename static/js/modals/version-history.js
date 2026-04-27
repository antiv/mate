/* eslint-disable */
/**
 * Agent Config Version History – modal, diff viewer, rollback, tagging.
 * Relies on Monaco Editor (already loaded by the agents page).
 */

(function () {
    'use strict';

    let _agentConfigId = null;
    let _agentName = '';
    let _versions = [];
    let _selectedVersionId = null;
    let _diffEditor = null;

    // ── Public API (attached to window) ──────────────────────────────────

    window.showVersionHistoryModal = function (configId, agentName) {
        _agentConfigId = configId;
        _agentName = agentName;
        _selectedVersionId = null;
        document.getElementById('vhAgentName').textContent = '— ' + agentName;
        document.getElementById('vhVersionList').innerHTML = '<div class="p-3 text-xs text-gray-400 text-center"><i class="fas fa-spinner fa-spin mr-1"></i>Loading…</div>';
        document.getElementById('vhDiffTitle').textContent = 'Select a version to view changes';
        document.getElementById('vhDiffActions').classList.add('hidden');
        _clearDiff();
        document.getElementById('versionHistoryModal').classList.remove('hidden');
        _fetchVersions();
    };

    window.hideVersionHistoryModal = function () {
        document.getElementById('versionHistoryModal').classList.add('hidden');
        if (_diffEditor) {
            _diffEditor.dispose();
            _diffEditor = null;
        }
    };

    window.rollbackToVersion = async function () {
        if (!_selectedVersionId || !_agentConfigId) return;
        const ver = _versions.find(v => v.id === _selectedVersionId);
        const label = ver ? `v${ver.version_number}` : `#${_selectedVersionId}`;
        const confirmed = await showConfirmDialog(
            `Rollback agent "${_agentName}" to version ${label}?\n\nThis will overwrite the current configuration and create a new rollback version.`,
            'Confirm Rollback',
            'Rollback',
            'Cancel',
            'warning'
        );
        if (!confirmed) return;

        try {
            const resp = await fetch(`/dashboard/api/agents/${_agentConfigId}/rollback/${_selectedVersionId}`, {
                method: 'POST',
                credentials: 'same-origin',
            });
            const data = await resp.json();
            if (data.success) {
                showNotification(`Rolled back to version ${label}`, 'success');
                _fetchVersions();
                // Refresh the edit form behind the modal with the new config
                if (data.config) {
                    _refreshEditForm(data.config);
                }
                // Trigger agent reinitialize
                fetch(`/dashboard/api/agents/${_agentName}/reinitialize`, { method: 'POST', credentials: 'same-origin' }).catch(() => {});
            } else {
                showNotification(data.message || 'Rollback failed', 'error');
            }
        } catch (err) {
            showNotification('Rollback error: ' + err.message, 'error');
        }
    };

    // ── Internal helpers ─────────────────────────────────────────────────

    async function _fetchVersions() {
        try {
            const resp = await fetch(`/dashboard/api/agents/${_agentConfigId}/versions`, { credentials: 'same-origin' });
            const data = await resp.json();
            _versions = data.versions || [];
            document.getElementById('vhVersionCount').textContent = _versions.length;
            _renderVersionList();
        } catch (err) {
            document.getElementById('vhVersionList').innerHTML =
                '<div class="p-3 text-xs text-red-400 text-center">Failed to load versions</div>';
        }
    }

    function _changeTypeIcon(type) {
        switch (type) {
            case 'create':   return '<i class="fas fa-plus-circle text-green-500 mr-1" title="Created"></i>';
            case 'update':   return '<i class="fas fa-pen text-blue-500 mr-1" title="Updated"></i>';
            case 'rollback': return '<i class="fas fa-undo text-orange-500 mr-1" title="Rollback"></i>';
            default:         return '<i class="fas fa-circle text-gray-400 mr-1"></i>';
        }
    }

    function _renderVersionList() {
        const list = document.getElementById('vhVersionList');
        if (_versions.length === 0) {
            list.innerHTML = '<div class="p-3 text-xs text-gray-400 text-center">No versions yet</div>';
            return;
        }
        const html = _versions.map(v => {
            const isSelected = v.id === _selectedVersionId;
            const date = v.created_at ? new Date(v.created_at) : null;
            const timeStr = date ? date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
            return `
            <div class="px-3 py-2 cursor-pointer border-b border-gray-100 dark:border-gray-700 hover:bg-blue-50 dark:hover:bg-gray-700/50 transition-colors ${isSelected ? 'bg-blue-50 dark:bg-gray-700 ring-1 ring-inset ring-blue-400 dark:ring-blue-600' : ''}"
                 onclick="window._vhSelectVersion(${v.id})">
                <div class="flex items-center justify-between">
                    <span class="text-xs font-mono font-semibold text-gray-800 dark:text-gray-200">
                        ${_changeTypeIcon(v.change_type)}v${v.version_number}
                    </span>
                    <span class="text-[10px] text-gray-400">${timeStr}</span>
                </div>
                ${v.tag ? `<span class="inline-block mt-0.5 px-1.5 py-0.5 text-[10px] bg-yellow-100 dark:bg-yellow-900/40 text-yellow-700 dark:text-yellow-300 rounded">${_escHtml(v.tag)}</span>` : ''}
                ${v.changed_by ? `<div class="text-[10px] text-gray-400 mt-0.5"><i class="fas fa-user mr-0.5"></i>${_escHtml(v.changed_by)}</div>` : ''}
                <div class="flex items-center mt-1 space-x-1">
                    <button onclick="event.stopPropagation(); window._vhTagVersion(${v.id}, ${JSON.stringify(v.tag || '').replace(/"/g, '&quot;')})" class="text-[10px] text-gray-400 hover:text-yellow-600 dark:hover:text-yellow-400" title="Tag this version">
                        <i class="fas fa-tag"></i>
                    </button>
                </div>
            </div>`;
        }).join('');
        list.innerHTML = html;
    }

    window._vhSelectVersion = function (versionId) {
        _selectedVersionId = versionId;
        _renderVersionList();
        _showDiff(versionId);
    };

    window._vhTagVersion = async function (versionId, currentTag) {
        const newTag = prompt('Enter a tag (e.g. "v1-production") or leave empty to clear:', currentTag || '');
        if (newTag === null) return;
        try {
            const resp = await fetch(`/dashboard/api/agents/versions/${versionId}/tag`, {
                method: 'PUT',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag: newTag }),
            });
            const data = await resp.json();
            if (data.success) {
                showNotification(newTag ? `Tagged as "${newTag}"` : 'Tag removed');
                _fetchVersions();
            } else {
                showNotification(data.message || 'Failed to tag', 'error');
            }
        } catch (err) {
            showNotification('Error tagging version', 'error');
        }
    };

    window._vhRunEvals = async function () {
        if (!_selectedVersionId) return;
        const btn = document.getElementById('vhRunEvalsBtn');
        const resultEl = document.getElementById('vhEvalsResult');
        if (!btn || !resultEl) return;

        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Running…';
        resultEl.classList.add('hidden');

        try {
            const resp = await fetch(`/dashboard/api/evals/version/${_selectedVersionId}/run`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ results: [] }),
            });
            const data = await resp.json();

            if (!data.results && !data.passed && !data.failed) {
                resultEl.innerHTML = '<span class="text-gray-500">No active test cases for this agent. Add test cases on the <a href="/dashboard/evals" class="text-blue-500 underline">Evals page</a>.</span>';
                resultEl.classList.remove('hidden');
                return;
            }

            if (data.detail || data.error) {
                showNotification(data.detail || data.error, 'error');
                return;
            }

            const avg = data.avg_score !== null && data.avg_score !== undefined ? (data.avg_score * 100).toFixed(1) + '%' : '—';
            const passRate = data.pass_rate !== null && data.pass_rate !== undefined ? (data.pass_rate * 100).toFixed(0) + '%' : '—';
            const regressionHtml = data.regression_alert
                ? '<span class="ml-2 text-red-600 font-semibold"><i class="fas fa-exclamation-triangle mr-1"></i>Regression detected</span>'
                : '';

            resultEl.innerHTML = `
                <div class="flex flex-wrap items-center gap-3">
                    <span class="text-gray-500">Evals:</span>
                    <span class="text-green-600 dark:text-green-400 font-semibold">${data.passed || 0} passed</span>
                    <span class="text-red-500">${data.failed || 0} failed</span>
                    <span class="text-gray-500">avg <strong>${avg}</strong></span>
                    <span class="text-gray-500">pass rate <strong>${passRate}</strong></span>
                    ${regressionHtml}
                </div>`;
            resultEl.classList.remove('hidden');
            showNotification('Evals complete', 'success');
        } catch (err) {
            showNotification('Eval run error: ' + err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-flask mr-1"></i>Run Evals';
        }
    };

    function _showDiff(versionId) {
        const ver = _versions.find(v => v.id === versionId);
        if (!ver) return;
        const idx = _versions.indexOf(ver);
        // Previous version (versions array is newest-first, so previous = idx+1)
        const prevVer = idx < _versions.length - 1 ? _versions[idx + 1] : null;

        const leftLabel = prevVer ? `v${prevVer.version_number}` : '(empty)';
        const rightLabel = `v${ver.version_number}`;
        document.getElementById('vhDiffTitle').textContent = `${leftLabel}  →  ${rightLabel}`;
        document.getElementById('vhDiffActions').classList.remove('hidden');
        document.getElementById('vhDiffActions').style.display = 'flex';

        const leftJson = prevVer ? JSON.stringify(prevVer.config_snapshot, null, 2) : '{}';
        const rightJson = JSON.stringify(ver.config_snapshot, null, 2);

        _renderMonacoDiff(leftJson, rightJson, leftLabel, rightLabel);
    }

    function _renderMonacoDiff(leftText, rightText, leftLabel, rightLabel) {
        const container = document.getElementById('vhDiffContainer');
        container.innerHTML = '';

        if (_diffEditor) {
            _diffEditor.dispose();
            _diffEditor = null;
        }

        // Monaco may already be loaded globally via require
        if (typeof monaco !== 'undefined') {
            _createDiffEditor(container, leftText, rightText);
            return;
        }
        // Fall back to require
        if (typeof require !== 'undefined' && typeof require.config === 'function') {
            require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs' } });
            require(['vs/editor/editor.main'], function () {
                _createDiffEditor(container, leftText, rightText);
            });
        } else {
            // Plain text fallback
            container.innerHTML = `<pre class="p-3 text-xs overflow-auto h-full whitespace-pre-wrap font-mono text-gray-700 dark:text-gray-300">${_escHtml(rightText)}</pre>`;
        }
    }

    function _createDiffEditor(container, leftText, rightText) {
        const isDark = document.documentElement.classList.contains('dark') ||
                       document.body.classList.contains('dark');
        _diffEditor = monaco.editor.createDiffEditor(container, {
            readOnly: true,
            renderSideBySide: true,
            theme: isDark ? 'vs-dark' : 'vs',
            automaticLayout: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 12,
        });
        _diffEditor.setModel({
            original: monaco.editor.createModel(leftText, 'json'),
            modified: monaco.editor.createModel(rightText, 'json'),
        });
    }

    function _clearDiff() {
        const container = document.getElementById('vhDiffContainer');
        if (container) container.innerHTML = '<div class="flex items-center justify-center h-full text-xs text-gray-400"><i class="fas fa-code-branch mr-2"></i>Select a version to compare</div>';
        if (_diffEditor) {
            _diffEditor.dispose();
            _diffEditor = null;
        }
    }

    function _refreshEditForm(freshConfig) {
        // Update in-memory configs array so the rest of the page stays consistent
        if (typeof configs !== 'undefined' && Array.isArray(configs)) {
            const idx = configs.findIndex(c => c.id == freshConfig.id);
            if (idx !== -1) {
                configs[idx] = freshConfig;
            }
        }
        // Re-render the agents table with updated data
        if (typeof filterAgents === 'function') {
            filterAgents();
        }
        // Re-populate the edit form using the existing editAgent() helper
        if (typeof editAgent === 'function') {
            editAgent(freshConfig);
        }
    }

    function _escHtml(s) {
        const d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }
})();
