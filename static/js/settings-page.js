/**
 * Settings page — MATE Dashboard
 * Server-wide defaults: the default image model, and model prices.
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

// Model prices: what each call costs on the Usage page (shared/utils/model_pricing.py)
(function () {
    'use strict';

    const API = '/dashboard/api/settings/model-prices';
    const SOURCES = { manual: 'set by hand', openrouter: 'OpenRouter', litellm: 'LiteLLM' };

    async function request(method, url, body) {
        const resp = await fetch(url, {
            method,
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || 'Request failed');
        return data;
    }

    function money(value) {
        return value == null ? '—' : '$' + (value >= 1 || value === 0 ? value.toFixed(2) : value.toPrecision(3));
    }

    function cell(text, className) {
        const td = document.createElement('td');
        td.className = 'px-2 py-1 ' + (className || '');
        td.textContent = text;
        return td;
    }

    function priceInput(value, label) {
        const input = document.createElement('input');
        input.type = 'number';
        input.min = '0';
        input.step = 'any';
        input.placeholder = label;
        input.setAttribute('aria-label', label);
        input.value = value == null ? '' : value;
        input.className = 'w-20 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded px-1 py-0.5 text-xs';
        return input;
    }

    function button(text, className, onClick) {
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = text;
        b.className = 'px-2 py-0.5 text-xs rounded ' + className;
        b.addEventListener('click', onClick);
        return b;
    }

    function row(model) {
        const tr = document.createElement('tr');
        tr.className = 'text-gray-700 dark:text-gray-300';
        tr.append(cell(model.model_name, 'font-mono break-all'), cell(model.calls.toLocaleString(), 'text-right'),
                  cell(model.unpriced_calls ? model.unpriced_calls.toLocaleString() : '', 'text-right text-amber-600 dark:text-amber-400'));

        const price = cell(model.source == null ? 'no price'
            : money(model.input_usd_per_mtok) + ' / ' + money(model.output_usd_per_mtok) + ' (' + (SOURCES[model.source] || model.source) + ')',
            model.source == null ? 'text-amber-600 dark:text-amber-400' : '');
        tr.appendChild(price);

        const manual = model.manual || {};
        const input = priceInput(manual.input_usd_per_mtok, 'in');
        const output = priceInput(manual.output_usd_per_mtok, 'out');
        const edit = document.createElement('td');
        edit.className = 'px-2 py-1 whitespace-nowrap';
        const save = button('Save', 'bg-blue-600 hover:bg-blue-700 text-white', async () => {
            if (input.value === '' || output.value === '') {
                showNotification('Enter both prices, 0 for free', 'error');
                return;
            }
            try {
                render(await request('PUT', API, {
                    model_name: model.model_name,
                    input_usd_per_mtok: Number(input.value),
                    output_usd_per_mtok: Number(output.value),
                }));
                showNotification('Price saved, and the model\'s calls repriced', 'success');
            } catch (e) {
                showNotification(e.message, 'error');
            }
        });
        edit.append(input, document.createTextNode(' / '), output, document.createTextNode(' '), save);
        if (model.manual) {
            edit.append(document.createTextNode(' '), button('Remove', 'border border-gray-300 dark:border-gray-600', async () => {
                try {
                    render(await request('DELETE', API + '?model=' + encodeURIComponent(model.model_name)));
                    showNotification('Manual price removed', 'success');
                } catch (e) {
                    showNotification(e.message, 'error');
                }
            }));
        }
        tr.appendChild(edit);
        return tr;
    }

    function render(data) {
        const body = document.getElementById('modelPricesBody');
        body.textContent = '';
        if (!data.models.length) {
            const empty = cell('No model calls logged yet.', 'text-gray-500 dark:text-gray-400 py-3');
            empty.colSpan = 5;
            body.appendChild(document.createElement('tr')).appendChild(empty);
            return;
        }
        data.models.forEach(m => body.appendChild(row(m)));
    }

    document.addEventListener('DOMContentLoaded', async function () {
        document.getElementById('fillMissingCosts').addEventListener('click', async () => {
            try {
                const data = await request('POST', API + '/fill-missing');
                render(data);
                showNotification(data.priced + ' calls priced', 'success');
            } catch (e) {
                showNotification(e.message, 'error');
            }
        });
        document.getElementById('newPriceAdd').addEventListener('click', () => {
            const nameInput = document.getElementById('newPriceModel');
            const name = nameInput.value.trim();
            if (!name) return;
            const tr = row({ model_name: name, calls: 0, unpriced_calls: 0, source: null, manual: null });
            document.getElementById('modelPricesBody').prepend(tr);
            tr.querySelector('input').focus();
            nameInput.value = '';
        });
        try {
            render(await request('GET', API));
        } catch (e) {
            document.getElementById('modelPricesBody').textContent = '';
            showNotification(e.message, 'error');
        }
    });
})();
