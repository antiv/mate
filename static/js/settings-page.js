/**
 * Settings page — MATE Dashboard
 * Server-wide defaults; for now the default image model.
 */
(function () {
    'use strict';

    const SOURCES = {
        dashboard: 'set on this page',
        IMAGE_MODEL: 'from the IMAGE_MODEL environment variable',
        default: 'built-in default',
    };

    function render(state) {
        document.getElementById('imageModelInput').value = state.stored || '';
        document.getElementById('imageModelInput').placeholder = state.env || state.builtin;
        document.getElementById('imageModelEffective').textContent = state.effective;
        document.getElementById('imageModelSource').textContent = '(' + (SOURCES[state.source] || state.source) + ')';
        const check = document.getElementById('imageModelCheck');
        check.textContent = '';
        const icon = document.createElement('i');
        const text = document.createElement('span');
        if (state.check.ok) {
            icon.className = 'fas fa-check-circle text-green-600 dark:text-green-400 mr-1';
            text.textContent = 'set' + (state.check.provider ? ' for ' + state.check.provider : '');
        } else {
            icon.className = 'fas fa-exclamation-triangle text-amber-500 mr-1';
            text.textContent = state.check.error || 'not set';
        }
        check.append(icon, text);
    }

    async function request(method, body) {
        const resp = await fetch('/dashboard/api/settings/image-model', {
            method,
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || 'Request failed');
        return data;
    }

    async function save(model) {
        try {
            render(await request('PUT', { model }));
            showNotification(model ? 'Default image model saved' : 'Default image model cleared', 'success');
        } catch (e) {
            showNotification(e.message, 'error');
        }
    }

    document.addEventListener('DOMContentLoaded', async function () {
        document.getElementById('imageModelSave').addEventListener('click',
            () => save(document.getElementById('imageModelInput').value.trim()));
        document.getElementById('imageModelClear').addEventListener('click', () => save(''));
        document.getElementById('imageModelInput').addEventListener('keydown', e => {
            if (e.key === 'Enter') save(e.target.value.trim());
        });
        try {
            render(await request('GET'));
        } catch (e) {
            showNotification(e.message, 'error');
        }
    });
})();
