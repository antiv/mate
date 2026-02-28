/**
 * Widget Keys management for the MATE dashboard.
 *
 * Functions: showWidgetKeysModal, hideWidgetKeysModal, generateWidgetKey,
 *            toggleWidgetKey, deleteWidgetKey, showWidgetEmbedCode, copyEmbedCode
 */

let _widgetKeysAgent = '';
let _widgetKeysProjectId = null;

function showWidgetKeysModal(agentName, projectId) {
    _widgetKeysAgent = agentName;
    _widgetKeysProjectId = projectId;
    document.getElementById('widgetKeysAgentName').textContent = agentName;
    document.getElementById('widgetKeysModal').classList.remove('hidden');
    document.getElementById('widgetKeyLabel').value = '';
    document.getElementById('widgetKeyOrigins').value = '';
    _loadWidgetKeys();
}

function hideWidgetKeysModal() {
    document.getElementById('widgetKeysModal').classList.add('hidden');
}

function _loadWidgetKeys() {
    const list = document.getElementById('widgetKeysList');
    list.innerHTML = '<div class="text-xs text-gray-400 text-center py-2"><i class="fas fa-spinner fa-spin mr-1"></i> Loading...</div>';

    let url = '/dashboard/api/widget-keys';
    if (_widgetKeysProjectId) url += '?project_id=' + _widgetKeysProjectId;

    fetch(url, { headers: _authHeaders() })
        .then(r => r.json())
        .then(res => {
            const keys = (res.keys || []).filter(k => k.agent_name === _widgetKeysAgent);
            if (keys.length === 0) {
                list.innerHTML = '<div class="text-xs text-gray-400 text-center py-4">No widget keys yet. Generate one below.</div>';
                return;
            }
            list.innerHTML = keys.map(k => _renderKeyCard(k)).join('');
        })
        .catch(() => {
            list.innerHTML = '<div class="text-xs text-red-500 text-center py-2">Failed to load keys.</div>';
        });
}

function _renderKeyCard(k) {
    const statusClass = k.is_active
        ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300'
        : 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300';
    const statusText = k.is_active ? 'Active' : 'Inactive';
    const origins = k.allowed_origins ? k.allowed_origins.join(', ') : 'All origins';
    const maskedKey = k.api_key.slice(0, 12) + '...' + k.api_key.slice(-6);

    return `
        <div class="border border-gray-200 dark:border-gray-700 rounded-lg p-3">
            <div class="flex items-center justify-between mb-2">
                <div>
                    <span class="text-xs font-medium text-gray-900 dark:text-white">${_esc(k.label || 'Unnamed')}</span>
                    <span class="ml-2 px-1.5 py-0.5 text-[10px] rounded-full ${statusClass}">${statusText}</span>
                </div>
                <div class="flex items-center space-x-1">
                    <button onclick="showWidgetEmbedCode(${k.id})" class="text-blue-600 hover:text-blue-800 dark:text-blue-400 p-1" title="Embed code">
                        <i class="fas fa-code text-xs"></i>
                    </button>
                    <button onclick="toggleWidgetKey(${k.id}, ${!k.is_active})" class="text-yellow-600 hover:text-yellow-800 dark:text-yellow-400 p-1" title="${k.is_active ? 'Deactivate' : 'Activate'}">
                        <i class="fas fa-${k.is_active ? 'pause' : 'play'} text-xs"></i>
                    </button>
                    <button onclick="deleteWidgetKey(${k.id})" class="text-red-600 hover:text-red-800 dark:text-red-400 p-1" title="Delete">
                        <i class="fas fa-trash text-xs"></i>
                    </button>
                </div>
            </div>
            <div class="text-[10px] text-gray-500 dark:text-gray-400 font-mono">${_esc(maskedKey)}</div>
            <div class="text-[10px] text-gray-400 dark:text-gray-500 mt-1">Origins: ${_esc(origins)}</div>
        </div>
    `;
}

function generateWidgetKey() {
    const label = document.getElementById('widgetKeyLabel').value.trim();
    const originsRaw = document.getElementById('widgetKeyOrigins').value.trim();
    const allowed_origins = originsRaw ? originsRaw.split(',').map(s => s.trim()).filter(Boolean) : null;

    fetch('/dashboard/api/widget-keys', {
        method: 'POST',
        headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({
            project_id: _widgetKeysProjectId,
            agent_name: _widgetKeysAgent,
            label: label || _widgetKeysAgent + ' widget',
            allowed_origins,
        }),
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showNotification('Widget key generated', 'success');
            document.getElementById('widgetKeyLabel').value = '';
            document.getElementById('widgetKeyOrigins').value = '';
            _loadWidgetKeys();
        } else {
            showNotification(res.detail || 'Failed to generate key', 'error');
        }
    })
    .catch(() => showNotification('Network error', 'error'));
}

function toggleWidgetKey(keyId, activate) {
    fetch('/dashboard/api/widget-keys/' + keyId, {
        method: 'PUT',
        headers: { ..._authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: activate }),
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showNotification(activate ? 'Key activated' : 'Key deactivated', 'success');
            _loadWidgetKeys();
        }
    });
}

function deleteWidgetKey(keyId) {
    if (!confirm('Delete this widget key? Any sites using it will stop working.')) return;
    fetch('/dashboard/api/widget-keys/' + keyId, {
        method: 'DELETE',
        headers: _authHeaders(),
    })
    .then(r => r.json())
    .then(res => {
        if (res.success) {
            showNotification('Widget key deleted', 'success');
            _loadWidgetKeys();
        }
    });
}

function showWidgetEmbedCode(keyId) {
    fetch('/dashboard/api/widget-keys/' + keyId + '/embed-code', { headers: _authHeaders() })
        .then(r => r.json())
        .then(res => {
            if (res.success) {
                document.getElementById('widgetEmbedCode').textContent = res.embed_code;
                const baseUrl = window.location.origin;
                const adminUrl = baseUrl + '/widget/admin?key=' + encodeURIComponent(res.api_key);
                const adminLink = document.getElementById('widgetAdminLink');
                adminLink.href = adminUrl;
                adminLink.textContent = adminUrl;
                document.getElementById('widgetEmbedModal').classList.remove('hidden');
            }
        });
}

function copyEmbedCode() {
    const code = document.getElementById('widgetEmbedCode').textContent;
    navigator.clipboard.writeText(code).then(() => {
        showNotification('Embed code copied to clipboard', 'success');
    });
}

function _authHeaders() {
    // Try cookie first
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
        var c = cookies[i].trim();
        if (c.indexOf('auth_token=') === 0) {
            return { 'Authorization': 'Bearer ' + c.substring('auth_token='.length) };
        }
    }
    // Fall back to whatever the dashboard already uses (basic auth from browser cache)
    return {};
}

function _esc(text) {
    const d = document.createElement('div');
    d.textContent = text || '';
    return d.innerHTML;
}
