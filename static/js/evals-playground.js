/**
 * Evals page — tabs, and the Playground: one prompt or the agent's eval suite on
 * 2–3 variants of an agent (another model, other instructions, a stored version),
 * with replies, scores, latency and cost side by side.
 * Server side: /dashboard/api/evals/playground/* (shared/utils/eval_playground.py).
 */
(function () {
    'use strict';

    const API = '/dashboard/api/evals/playground';
    const MAX_VARIANTS = 3;
    const INPUT = 'w-full border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded px-2 py-1 text-xs';
    let agent = null;      // {name, model_name, instruction, versions, active_cases}
    let loaded = false;

    function el(tag, attrs, children) {
        const node = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => {
            if (k === 'text') node.textContent = v;
            else if (k === 'className') node.className = v;
            else node.setAttribute(k, v);
        });
        (children || []).forEach(c => node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
        return node;
    }

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
        if (value == null) return '—';
        return '$' + (value >= 1 ? value.toFixed(2) : value.toFixed(4));
    }

    // ── Tabs ────────────────────────────────────────────────────────────────

    function showTab(tab) {
        document.querySelectorAll('.evals-tab').forEach(b => {
            const active = b.dataset.tab === tab;
            b.setAttribute('aria-selected', String(active));
            b.classList.toggle('border-blue-600', active);
            b.classList.toggle('text-blue-600', active);
            b.classList.toggle('dark:text-blue-400', active);
            b.classList.toggle('border-transparent', !active);
            b.classList.toggle('text-gray-500', !active);
        });
        document.getElementById('suitesView').classList.toggle('hidden', tab !== 'suites');
        document.getElementById('playgroundView').classList.toggle('hidden', tab !== 'playground');
        if (tab === 'playground' && !loaded) {
            loaded = true;
            loadAgents();
        }
    }

    // ── Agent and variants ──────────────────────────────────────────────────

    async function loadAgents() {
        const select = document.getElementById('pgAgent');
        try {
            const data = await request('GET', API + '/agents');
            select.textContent = '';
            data.agents.forEach(a => select.appendChild(el('option', { value: a.name, text: a.name })));
            if (data.agents.length) await selectAgent(data.agents[0].name);
        } catch (e) {
            showNotification(e.message, 'error');
        }
    }

    async function selectAgent(name) {
        try {
            agent = await request('GET', API + '/agent/' + encodeURIComponent(name));
        } catch (e) {
            showNotification(e.message, 'error');
            return;
        }
        document.getElementById('pgVariants').textContent = '';
        addVariant();
        addVariant();
        updateMode();
        document.getElementById('pgResults').textContent = '';
    }

    function addVariant() {
        const box = document.getElementById('pgVariants');
        const index = box.children.length;
        if (!agent || index >= MAX_VARIANTS) return;

        const version = el('select', { className: INPUT, 'aria-label': 'Start from' },
            [el('option', { value: '', text: 'Current config' })]);
        agent.versions.forEach(v => version.appendChild(el('option', {
            value: String(v.id), text: 'Version ' + v.version_number + (v.tag ? ' — ' + v.tag : ''),
        })));
        const model = el('input', { type: 'text', className: INPUT, placeholder: agent.model_name || 'model',
                                    'aria-label': 'Model', autocomplete: 'off' });
        const instruction = el('textarea', { rows: '5', className: INPUT + ' font-mono',
                                             placeholder: 'Instructions unchanged', 'aria-label': 'Instructions' });
        const copy = el('button', { type: 'button', className: 'text-xs text-blue-600 dark:text-blue-400 hover:underline',
                                    text: 'Edit the current instructions' });
        copy.addEventListener('click', () => { instruction.value = agent.instruction; instruction.focus(); });
        const label = el('input', { type: 'text', className: INPUT + ' font-semibold', value: 'Variant ' + 'ABC'[index],
                                    'aria-label': 'Label', maxlength: '60' });

        const card = el('div', { className: 'pg-variant bg-white dark:bg-gray-800 rounded-lg shadow p-3 space-y-2' }, [
            label,
            el('label', { className: 'block text-[11px] text-gray-500 dark:text-gray-400', text: 'Start from' }), version,
            el('label', { className: 'block text-[11px] text-gray-500 dark:text-gray-400', text: 'Model (empty = the start\'s model)' }), model,
            el('div', { className: 'flex justify-between items-center' }, [
                el('span', { className: 'text-[11px] text-gray-500 dark:text-gray-400', text: 'Instructions (empty = unchanged)' }), copy]),
            instruction,
        ]);
        if (index >= 2) {
            const remove = el('button', { type: 'button', className: 'text-xs text-red-600 hover:underline', text: 'Remove' });
            remove.addEventListener('click', () => { card.remove(); updateAddButton(); });
            card.appendChild(remove);
        }
        card._fields = { label, version, model, instruction };
        box.appendChild(card);
        updateAddButton();
    }

    function updateAddButton() {
        document.getElementById('pgAddVariant').disabled =
            document.getElementById('pgVariants').children.length >= MAX_VARIANTS;
    }

    function updateMode() {
        const suite = document.getElementById('pgMode').value === 'suite';
        document.getElementById('pgPromptFields').classList.toggle('hidden', suite);
        const info = document.getElementById('pgSuiteInfo');
        info.classList.toggle('hidden', !suite);
        if (agent) info.textContent = agent.active_cases + ' active test case' + (agent.active_cases === 1 ? '' : 's')
            + ' × ' + document.getElementById('pgVariants').children.length + ' variants';
    }

    // ── Run and results ─────────────────────────────────────────────────────

    async function run() {
        if (!agent) return;
        const mode = document.getElementById('pgMode').value;
        const body = {
            agent_name: agent.name,
            mode,
            variants: [...document.querySelectorAll('#pgVariants .pg-variant')].map(card => {
                const f = card._fields;
                return {
                    label: f.label.value.trim(),
                    version_id: f.version.value ? Number(f.version.value) : null,
                    model_name: f.model.value.trim() || null,
                    instruction: f.instruction.value.trim() ? f.instruction.value : null,
                };
            }),
        };
        if (mode === 'prompt') {
            body.prompt = document.getElementById('pgPrompt').value;
            body.expected_output = document.getElementById('pgExpected').value;
            body.eval_method = document.getElementById('pgMethod').value;
            if (!body.prompt.trim()) {
                showNotification('Write a prompt to run', 'error');
                return;
            }
        }
        const button = document.getElementById('pgRun');
        const status = document.getElementById('pgStatus');
        button.disabled = true;
        status.textContent = 'Running… each variant answers in turn';
        try {
            renderResults(await request('POST', API + '/run', body));
            status.textContent = '';
        } catch (e) {
            status.textContent = '';
            showNotification(e.message, 'error');
        } finally {
            button.disabled = false;
        }
    }

    function cellClass(extra) {
        return 'px-2 py-1 align-top ' + (extra || '');
    }

    function renderResults(data) {
        const out = document.getElementById('pgResults');
        out.textContent = '';
        const variants = data.variants;

        // Summary: one row per figure, one column per variant
        const head = el('tr', { className: 'text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700' },
            [el('th', { className: cellClass('font-medium'), text: '' })]
                .concat(variants.map(v => el('th', { className: cellClass('font-semibold text-gray-900 dark:text-white') }, [
                    v.label, el('div', { className: 'font-mono font-normal text-[11px] text-gray-500 dark:text-gray-400', text: v.model_name || '' }),
                ]))));
        const scored = variants.some(v => v.summary.scored);
        const rows = [
            ['Score', v => v.summary.avg_score == null ? '—' : v.summary.avg_score.toFixed(3)
                + (data.mode === 'suite' ? ' (' + v.summary.passed + '/' + v.summary.scored + ' passed)' : '')],
            ['Avg latency', v => v.summary.avg_latency_ms == null ? '—' : (v.summary.avg_latency_ms / 1000).toFixed(1) + 's'],
            ['Cost', v => money(v.summary.cost_usd) + (v.summary.unpriced_calls ? ' + ' + v.summary.unpriced_calls + ' unpriced calls' : '')],
            ['Tokens', v => v.summary.tokens.toLocaleString()],
            ['Errors', v => v.error ? v.error : String(v.summary.errors)],
        ].filter(([name]) => name !== 'Score' || scored);
        const summary = el('table', { className: 'w-full text-xs' }, [el('thead', {}, [head]), el('tbody', {
            className: 'divide-y divide-gray-100 dark:divide-gray-700' }, rows.map(([name, value]) => el('tr', {
                className: 'text-gray-700 dark:text-gray-300' }, [el('td', { className: cellClass('text-gray-500 dark:text-gray-400'), text: name })]
                .concat(variants.map(v => el('td', { className: cellClass(name === 'Errors' && (v.error || v.summary.errors) ? 'text-red-600 dark:text-red-400' : ''), text: value(v) }))))))]);
        out.appendChild(el('div', { className: 'bg-white dark:bg-gray-800 rounded-lg shadow p-4 overflow-x-auto' }, [
            el('h3', { className: 'text-sm font-semibold text-gray-900 dark:text-white mb-2', text: 'Comparison' }), summary]));

        // Each prompt, with every variant's reply
        data.cases.forEach(c => {
            const header = el('div', { className: 'text-xs space-y-1' }, [
                el('div', { className: 'font-medium text-gray-900 dark:text-white whitespace-pre-wrap', text: c.input }),
            ]);
            if (c.expected_output) header.appendChild(el('div', { className: 'text-gray-500 dark:text-gray-400 whitespace-pre-wrap',
                                                                  text: 'Expected: ' + c.expected_output }));
            const replies = el('div', { className: 'grid grid-cols-1 lg:grid-cols-' + variants.length + ' gap-2' },
                c.results.map((r, i) => {
                    const meta = [];
                    if (r) {
                        if (r.score != null) meta.push('score ' + r.score.toFixed(2) + (r.passed ? ' ✓' : ' ✗'));
                        meta.push((r.latency_ms / 1000).toFixed(1) + 's');
                        meta.push(money(r.cost_usd));
                    }
                    return el('div', { className: 'border border-gray-200 dark:border-gray-700 rounded p-2 text-xs space-y-1 min-w-0' }, [
                        el('div', { className: 'flex justify-between gap-2 text-gray-500 dark:text-gray-400' }, [
                            el('span', { className: 'font-semibold', text: variants[i].label }),
                            el('span', { text: meta.join(' · ') })]),
                        el('div', {
                            className: 'whitespace-pre-wrap break-words ' + (r && r.output != null ? 'text-gray-800 dark:text-gray-200' : 'text-red-600 dark:text-red-400'),
                            text: r ? (r.output != null ? r.output : r.error) : (variants[i].error || 'Not run'),
                        }),
                        r && r.output != null && r.error ? el('div', { className: 'text-amber-600', text: r.error }) : el('span'),
                    ]);
                }));
            out.appendChild(el('div', { className: 'bg-white dark:bg-gray-800 rounded-lg shadow p-4 space-y-2' }, [header, replies]));
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.evals-tab').forEach(b => b.addEventListener('click', () => showTab(b.dataset.tab)));
        document.getElementById('pgAgent').addEventListener('change', e => selectAgent(e.target.value));
        document.getElementById('pgMode').addEventListener('change', updateMode);
        document.getElementById('pgAddVariant').addEventListener('click', () => { addVariant(); updateMode(); });
        document.getElementById('pgRun').addEventListener('click', run);
    });
})();
