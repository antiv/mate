/**
 * The agent page (templates/dashboard/agent_page.html): an agent's settings in the
 * agent editor, a new agent, or a copy of one (/dashboard/agents/new?from=<name>).
 *
 * The form's fields have the prefix "agent". The right-hand sections hold the fields
 * of the configuration dialogs (config_modals.html); the shared listeners of
 * agent-tools.js and agent-config.js keep their controls and JSON in step as you
 * type, and this file does the same for the guardrails, whose dialog did it on Save.
 *
 * Saving makes a version, as before: POST /dashboard/api/agents for a new agent,
 * PUT /dashboard/api/agents/<id> for this one. The save bar shows while something
 * changed; leaving the page then asks first.
 */
mateActions.allow('mateAgentPage.save', 'mateAgentPage.discard',
                  'mateAgentPage.remove', 'mateAgentPage.exportAgent', 'mateAgentPage.approvals',
                  'mateAgentPage.addParent', 'mateAgentPage.removeParent',
                  'mateAgentPage.roleKey', 'mateAgentPage.addRoleFromInput', 'mateAgentPage.removeRole',
                  'mateAgentPage.clearGuardrails', 'mateAgentPage.reloadSessions',
                  'mateAgentPage.reloadUsage', 'mateAgentPage.reloadTraces');

(function () {
    'use strict';

    const P = 'agent';
    // Roles nobody holds as a member: what each chip says instead of a member count
    const SPECIAL_ROLES = { widget: 'widget visitors', admin: 'platform administrators' };
    // The rule the Members page checks (server/member_routes.py, AGENT_ROLE)
    const ROLE_NAME = /^[A-Za-z0-9_.-]{1,64}$/;
    const FLASH_KEY = 'mateAgentPageFlash';

    const data = JSON.parse(document.getElementById('agent-page-data').textContent || '{}');
    const mode = data.mode;                 // edit | new | copy
    const config = data.config || null;     // the agent edited, or the one copied
    const roleCounts = new Map((data.roles || []).map((r) => [r.name, r.members]));

    let parents = [];
    let roles = [];
    let baseline = null;
    let saving = false;
    let instructionEditor = null;

    const el = (id) => document.getElementById(id);
    const field = (suffix) => el(P + suffix);

    function notify(message, type) {
        if (typeof showNotification === 'function') showNotification(message, type || 'success');
    }

    // ── Values ────────────────────────────────────────────────────────────

    // A config value as its form field holds it: JSON pretty-printed, empty for {} and []
    function asField(value) {
        if (value === null || value === undefined || value === '') return '';
        let parsed = value;
        if (typeof value === 'string') {
            try { parsed = JSON.parse(value); } catch (e) { return value; }
        }
        if (parsed && typeof parsed === 'object') {
            if (Array.isArray(parsed) ? parsed.length === 0 : Object.keys(parsed).length === 0) return '';
            return JSON.stringify(parsed, null, 2);
        }
        return String(value);
    }

    function asList(value) {
        if (Array.isArray(value)) return value.filter((v) => typeof v === 'string' && v);
        if (typeof value === 'string' && value.trim()) {
            try { return asList(JSON.parse(value)); } catch (e) { return []; }
        }
        return [];
    }

    const truthy = (value) => value === true || value === 'true' || value === 1 || value === '1';

    // A JSON field: its Monaco editor's text while the JSON view is open
    function jsonValue(suffix) {
        const editor = (typeof monacoEditors !== 'undefined') && monacoEditors[P + suffix + 'Editor'];
        return editor ? getJsonFromEditor(editor) : (field(suffix) ? field(suffix).value : '');
    }

    function instructionValue() {
        return instructionEditor ? instructionEditor.getValue() : field('Instruction').value;
    }

    /** The form as the agents API takes it. */
    function collect() {
        const form = new FormData();
        const value = (suffix) => (field(suffix) ? field(suffix).value : '');
        form.append('name', value('Name').trim());
        form.append('project_id', value('Project'));
        form.append('type', value('Type'));
        form.append('model_name', value('Model'));
        form.append('model_base_url', value('ModelBaseUrl'));
        form.append('fallback_model', value('FallbackModel'));
        form.append('ai_disclosure', value('AiDisclosure'));
        form.append('ai_disclosure_waiver', value('AiDisclosureWaiver'));
        form.append('model_api_key', value('ModelApiKey'));
        form.append('description', value('Description').trim());
        form.append('instruction', instructionValue().trim());
        form.append('parent_agents', parents.length ? JSON.stringify(parents) : '');
        form.append('allowed_for_roles', roles.length ? JSON.stringify(roles) : '');
        form.append('tool_config', jsonValue('ToolConfig'));
        form.append('mcp_servers_config', jsonValue('McpServersConfig'));
        form.append('planner_config', jsonValue('PlannerConfig'));
        form.append('generate_content_config', jsonValue('GenerateContentConfig'));
        form.append('input_schema', jsonValue('InputSchema'));
        form.append('output_schema', jsonValue('OutputSchema'));
        form.append('include_contents', value('IncludeContents'));
        form.append('guardrail_config', jsonValue('GuardrailConfig'));
        form.append('max_iterations', value('MaxIterations'));
        form.append('disabled', field('Disabled').checked);
        form.append('hardcoded', field('Hardcoded').checked);
        form.append('expose_as_model', field('ExposeAsModel').checked);
        form.append('debug_mode', field('DebugMode').checked);
        return form;
    }

    const snapshot = () => JSON.stringify(Array.from(collect().entries()));

    // ── Filling the form ──────────────────────────────────────────────────

    function fill() {
        const c = config || {};
        const set = (suffix, value) => { if (field(suffix)) field(suffix).value = value == null ? '' : value; };
        set('Name', mode === 'copy' ? `${c.name}_copy` : (c.name || ''));
        set('Type', c.type || 'llm');
        const projectId = c.project_id != null ? c.project_id : data.defaultProjectId;
        set('Project', projectId != null ? String(projectId) : '');
        set('Model', c.model_name);
        set('FallbackModel', c.fallback_model);
        set('Description', c.description);
        set('Instruction', c.instruction);
        set('ModelBaseUrl', c.model_base_url);
        // A copy never saw the original key: an empty field, not the stored sentinel
        set('ModelApiKey', mode === 'copy' && c.model_api_key === '__stored__' ? '' : c.model_api_key);
        set('AiDisclosure', c.ai_disclosure);
        set('AiDisclosureWaiver', c.ai_disclosure_waiver);
        set('IncludeContents', c.include_contents);
        set('MaxIterations', c.max_iterations);
        set('ToolConfig', asField(c.tool_config));
        set('McpServersConfig', asField(c.mcp_servers_config));
        set('PlannerConfig', asField(c.planner_config));
        set('GenerateContentConfig', asField(c.generate_content_config));
        set('InputSchema', asField(c.input_schema));
        set('OutputSchema', asField(c.output_schema));
        set('GuardrailConfig', asField(c.guardrail_config));
        field('Disabled').checked = truthy(c.disabled);
        field('Hardcoded').checked = truthy(c.hardcoded);
        field('ExposeAsModel').checked = truthy(c.expose_as_model);
        field('DebugMode').checked = truthy(c.debug_mode);
        parents = asList(c.parent_agents);
        roles = asList(c.allowed_for_roles);

        // The dialogs' presets from the JSON, and the summaries from both
        syncJsonToToolConfig(P);
        syncJsonToPlannerConfig(P);
        syncJsonToContentConfig(P);
        syncJsonToGuardrailConfig(P);
        setupToolListeners(P);
        setupPlannerListeners(P);
        setupContentGenerationListeners(P);
        initializeConfigSummaryHandling([P]);
        renderParents();
        renderRoles();
        showMaxIterations();
        updateInstructionInfo();
    }

    // ── Section states ────────────────────────────────────────────────────

    function parsed(suffix) {
        try { return JSON.parse(jsonValue(suffix) || 'null'); } catch (e) { return null; }
    }

    function setState(key, text) {
        const state = el('agentState-' + key);
        if (!state) return;
        state.textContent = text || '—';
        state.classList.toggle('is-set', !!text);
    }

    function updateStates() {
        const tools = parsed('ToolConfig') || {};
        const on = Object.keys(tools).filter((k) => tools[k] && k !== 'require_confirmation').length;
        setState('tools', on ? `${on} on` : '');
        const mcp = parsed('McpServersConfig') || {};
        const servers = Object.keys(mcp.mcpServers || mcp || {}).length;
        setState('mcp', servers ? String(servers) : '');
        const memory = tools.memory_blocks && tools.memory_blocks.enabled !== false;
        setState('memory', memory ? 'on' : '');
        if (el('agentMemoryHint')) el('agentMemoryHint').hidden = !!memory;
        if (typeof updateMemoryBlocksSectionVisibility === 'function') updateMemoryBlocksSectionVisibility(P, tools);
        const guardrails = ((parsed('GuardrailConfig') || {}).guardrails || []).filter((g) => g && g.enabled !== false);
        setState('guardrails', guardrails.length ? `${guardrails.length} on` : '');
        const planner = parsed('PlannerConfig');
        setState('planner', planner && planner.type ? String(planner.type).replace(/Planner$/, '') : '');
        setState('generation', parsed('GenerateContentConfig') ? 'set' : '');
        const io = [parsed('InputSchema') && 'in', parsed('OutputSchema') && 'out',
                    field('IncludeContents').value && 'contents'].filter(Boolean);
        setState('io', io.join(' · '));
        setState('endpoint', field('ModelBaseUrl').value.trim() ? 'set' : '');
        setState('disclosure', field('AiDisclosureWaiver').value.trim() ? 'hidden' : 'on');
        const advanced = [field('Type').value === 'loop' && field('MaxIterations').value && `${field('MaxIterations').value} iterations`,
                          field('Hardcoded').checked && 'hardcoded'].filter(Boolean);
        setState('advanced', advanced.join(' · '));
    }

    function showMaxIterations() {
        field('MaxIterationsField').hidden = field('Type').value !== 'loop';
    }

    function updateInstructionInfo() {
        const info = el('agentInstructionInfo');
        if (!info) return;
        const count = instructionValue().length;
        let text = `${count.toLocaleString()} character${count === 1 ? '' : 's'}`;
        if (mode === 'edit' && config && config.updated_at) {
            const when = new Date(config.updated_at);
            if (!isNaN(when)) text += ` · last saved ${when.toLocaleString()}`;
        }
        info.textContent = text;
    }

    // ── Pickers ───────────────────────────────────────────────────────────

    function chip(label, extra, onRemoveAction, value) {
        const span = document.createElement('span');
        span.className = 'm-chip';
        span.textContent = label;
        if (extra) {
            const small = document.createElement('small');
            small.textContent = extra;
            span.appendChild(small);
        }
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.setAttribute('aria-label', `Remove ${label}`);
        remove.dataset.click = onRemoveAction;
        remove.dataset.args = JSON.stringify([value]);
        remove.innerHTML = '<i class="fas fa-times" aria-hidden="true"></i>';
        span.appendChild(remove);
        return span;
    }

    function renderParents() {
        const box = el('agentParentChips');
        box.replaceChildren(...parents.map((name) => chip(name, '', 'mateAgentPage.removeParent', name)));
        field('Parents').value = parents.length ? JSON.stringify(parents) : '';
        Array.from(el('agentParentAdd').options).forEach((o) => { o.hidden = !!o.value && parents.includes(o.value); });
        el('agentParentAdd').value = '';
        el('agentParentsHint').textContent = parents.length
            ? 'A sub-agent: its parents hand it work.'
            : 'None: a root agent, with an app of its own.';
    }

    function renderRoles() {
        const box = el('agentRoleChips');
        box.replaceChildren(...roles.map((name) => {
            const held = roleCounts.get(name) || 0;
            const who = SPECIAL_ROLES[name] || `${held} member${held === 1 ? '' : 's'}`;
            return chip(name, who, 'mateAgentPage.removeRole', name);
        }));
        field('Roles').value = roles.length ? JSON.stringify(roles) : '';
        el('agentRolesHint').textContent = roles.length
            ? 'Members who hold one of these roles can run the agent, besides the workspace\'s owners and admins.'
            : 'No role: only the workspace\'s owners and admins, and platform administrators, can run this agent.';
    }

    function addParent(name) {
        if (name && !parents.includes(name)) parents.push(name);
        renderParents();
        changed();
    }

    function removeParent(name) {
        parents = parents.filter((p) => p !== name);
        renderParents();
        changed();
    }

    function addRole(name) {
        const error = el('agentRolesError');
        name = (name || '').trim();
        error.hidden = true;
        if (!name) return;
        if (!ROLE_NAME.test(name)) {
            error.textContent = 'A role is letters, digits, "_", "." and "-", at most 64 characters.';
            error.hidden = false;
            return;
        }
        if (!roles.includes(name)) roles.push(name);
        el('agentRoleAdd').value = '';
        renderRoles();
        changed();
    }

    function removeRole(name) {
        roles = roles.filter((r) => r !== name);
        renderRoles();
        changed();
    }

    // ── Dirty state ───────────────────────────────────────────────────────

    function isDirty() {
        return baseline !== null && snapshot() !== baseline;
    }

    function changed() {
        updateStates();
        updateInstructionInfo();
        if (mode !== 'edit') return;
        const dirty = isDirty();
        el('agentSaveBar').hidden = !dirty;
    }

    function markClean() {
        baseline = snapshot();
        changed();
    }

    // ── The instruction editor ────────────────────────────────────────────

    function startInstructionEditor() {
        if (typeof initMonacoEditor !== 'function' || typeof require === 'undefined') return;
        const container = el('agentInstructionEditor');
        container.hidden = false;
        initMonacoEditor('agentInstructionEditor', field('Instruction').value, 'markdown').then((editor) => {
            instructionEditor = editor;
            field('Instruction').hidden = true;
            editor.onDidChangeModelContent(() => {
                field('Instruction').value = editor.getValue();
                changed();
            });
        }).catch(() => { container.hidden = true; });
    }

    // ── Saving ────────────────────────────────────────────────────────────

    function flash(message) {
        try { sessionStorage.setItem(FLASH_KEY, message); } catch (e) { /* shown on no page then */ }
    }

    function pageUrl(name, hash) {
        const url = '/dashboard/agents/' + encodeURIComponent(name) + (data.backToVisual ? '?back=visual' : '');
        return hash ? url + hash : url;
    }

    function leave(url) {
        baseline = null;   // nothing to ask about
        window.location.href = url;
    }

    function invalid(message, suffix) {
        notify(message, 'error');
        if (suffix && field(suffix)) field(suffix).focus();
        return true;
    }

    function check(form) {
        if (!form.get('name')) return invalid('A name is required.', 'Name');
        if (!form.get('project_id')) return invalid('Select a project for this agent.', 'Project');
        if (!form.get('description')) return invalid('A description is required.', 'Description');
        if (!form.get('instruction')) return invalid('An instruction is required.');
        return false;
    }

    async function folderStep(form) {
        // A root agent that is not hardcoded runs from a folder under agents/
        const name = form.get('name');
        const hardcoded = form.get('hardcoded') === 'true';
        const wasHardcoded = !!(config && mode === 'edit' && truthy(config.hardcoded));
        if (parents.length) return null;
        if (mode !== 'edit' && !hardcoded) {
            const yes = await showConfirm(`This is a root agent. Create a folder for it under agents/${name}/, from the template agent?`,
                'Create agent folder', 'Create folder', 'Skip', 'info');
            return yes ? 'create' : null;
        }
        if (mode === 'edit' && wasHardcoded && !hardcoded) {
            const yes = await showConfirm(`This agent is no longer hardcoded. Create a folder for it under agents/${name}/?`,
                'Create agent folder', 'Create folder', 'Skip', 'info');
            return yes ? 'create' : null;
        }
        if (mode === 'edit' && !wasHardcoded && hardcoded) {
            const yes = await showConfirm(`This agent is now hardcoded. Delete its folder agents/${name}/ if there is one?`,
                'Delete agent folder', 'Delete', 'Keep', 'warning');
            return yes ? 'delete' : null;
        }
        return null;
    }

    async function folderAction(action, name) {
        if (!action) return '';
        try {
            const response = await fetch(`/dashboard/api/agents/${encodeURIComponent(name)}/${action}-folder`, {
                method: action === 'create' ? 'POST' : 'DELETE', credentials: 'same-origin' });
            const result = await response.json();
            if (!result.success) return ` The folder was not ${action}d: ${result.message || response.status}.`;
            if (action === 'create') return result.skipped ? ' Its folder was there already.' : ` Folder created at agents/${name}/.`;
            return result.folder_deleted ? ' Its folder was deleted.' : '';
        } catch (e) {
            return ` The folder was not ${action}d.`;
        }
    }

    async function save() {
        if (saving) return;
        const form = collect();
        if (check(form)) return;
        const folder = await folderStep(form);
        const button = el('agentSaveBtn');
        saving = true;
        button.disabled = true;
        try {
            const editing = mode === 'edit';
            const response = await fetch(editing ? `/dashboard/api/agents/${config.id}` : '/dashboard/api/agents', {
                method: editing ? 'PUT' : 'POST', credentials: 'same-origin', body: form });
            let result = {};
            try { result = await response.json(); } catch (e) { result = {}; }
            if (!response.ok || !result.success) {
                notify(result.message || result.detail || `Not saved (${response.status})`, 'error');
                return;
            }
            const name = form.get('name');
            const folderNote = await folderAction(folder, name);
            if (editing) {
                // The agent server builds it again from the saved configuration
                fetch(`/dashboard/api/agents/${encodeURIComponent(name)}/reinitialize`,
                      { method: 'POST', credentials: 'same-origin' }).catch(() => {});
            }
            flash((editing ? 'Saved.' : 'Agent created.') + folderNote);
            leave(pageUrl(name));
        } catch (e) {
            notify(`Not saved: ${e.message}`, 'error');
        } finally {
            saving = false;
            button.disabled = false;
        }
    }

    async function discard() {
        if (!isDirty()) return;
        const yes = await showConfirm('Discard the changes made since the last save?',
            'Discard changes', 'Discard', 'Keep editing', 'danger');
        if (yes) leave(window.location.href);
    }

    // ── The header's actions ──────────────────────────────────────────────

    function closeMenu() {
        const menu = el('agentMenu');
        if (menu) menu.open = false;
    }

    // ── Tabs: Versions and Sessions, loaded when first opened ─────────────

    const loaded = {};
    let sessionsTable = null;

    function sessionsApp() {
        const select = el('agentSessionsApp');
        return select ? select.value : (data.apps || [])[0];
    }

    function sessionLink(s) {
        const params = new URLSearchParams({ session_id: s.id, app_name: s.app_name, user_id: s.user_id });
        return `<a class="m-link" href="/dashboard/sessions?${params}">Open</a>`;
    }

    function startSessions() {
        const esc = MateTable.esc;
        sessionsTable = new MateTable(el('agentSessionsTable'), {
            noun: ['session', 'sessions'],
            label: 'Sessions',
            empty: 'No conversations yet.',
            columns: [
                { key: 'id', label: 'Session', sortable: false, className: 'm-mono',
                  render: (s) => `<span title="${esc(s.id)}">${esc(s.id.length > 20 ? s.id.slice(0, 18) + '…' : s.id)}</span>` },
                { key: 'user_id', label: 'User', sortable: false },
                { key: 'runtime', label: 'Runtime', sortable: false,
                  render: (s) => (s.runtime === 'langgraph' ? 'LangGraph' : 'ADK') },
                { key: 'event_count', label: 'Events', sortable: false, align: 'right' },
                { key: 'updated_at', label: 'Updated', sortable: false,
                  render: (s) => esc(typeof formatDateTime === 'function' ? formatDateTime(s.updated_at) : s.updated_at) },
                { label: 'Inspect', align: 'right', render: sessionLink },
            ],
            fetch: async ({ offset, limit, q }) => {
                const params = new URLSearchParams({ app_name: sessionsApp(), exact_app: 'true',
                                                     page: String(Math.floor(offset / limit) + 1), limit: String(limit) });
                if (q) params.set('search', q);
                const response = await fetch(`/dashboard/api/sessions?${params}`, { credentials: 'same-origin' });
                if (!response.ok) throw new Error(`The sessions did not load (${response.status})`);
                const result = await response.json();
                return { rows: result.sessions || [], total: result.total || 0 };
            },
        });
    }

    function reloadSessions() {
        if (sessionsTable) sessionsTable.reload({ firstPage: true });
    }

    const when = (value) => MateTable.esc(value ? (typeof formatDateTime === 'function' ? formatDateTime(value) : value) : '—');
    const percent = (value) => (value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`);
    const number = (value) => (value === null || value === undefined ? '—' : Number(value).toLocaleString());
    const cost = (value) => (value === null || value === undefined ? '—' : `$${Number(value).toFixed(value < 0.01 ? 4 : 2)}`);

    async function getJson(url) {
        const response = await fetch(url, { credentials: 'same-origin' });
        if (!response.ok) throw new Error(`Not loaded (${response.status})`);
        return response.json();
    }

    function truncate(text, length) {
        text = String(text || '');
        return text.length > length ? `${text.slice(0, length - 1)}…` : text;
    }

    // The test cases with their latest result, and the score of each version
    async function startEvals() {
        const esc = MateTable.esc;
        const name = encodeURIComponent(config.name);
        const latest = (c) => c.latest_result || null;
        const cases = new MateTable(el('agentEvalsCases'), {
            noun: ['test case', 'test cases'],
            label: 'Test cases',
            empty: 'No test cases yet. Add them in Evals, or from a rated-down answer.',
            columns: [
                { key: 'input', label: 'Input', render: (c) => esc(truncate(c.input, 120)) },
                { key: 'expected_output', label: 'Expected', render: (c) => esc(truncate(c.expected_output, 120)) },
                { key: 'eval_method', label: 'Method' },
                { key: 'score', label: 'Latest score', align: 'right', searchable: false,
                  value: (c) => (latest(c) ? latest(c).score : null),
                  render: (c) => esc(latest(c) ? percent(latest(c).score) : 'Not run') },
                { key: 'passed', label: 'Result', searchable: false,
                  value: (c) => (latest(c) ? (latest(c).passed ? 'passed' : 'failed') : ''),
                  render: (c) => (!latest(c) ? '—' : latest(c).passed
                      ? '<span class="m-badge m-badge-success">Passed</span>'
                      : '<span class="m-badge m-badge-danger">Failed</span>') },
                { key: 'run_at', label: 'Run', searchable: false,
                  value: (c) => (latest(c) ? latest(c).run_at : null),
                  render: (c) => when(latest(c) && latest(c).run_at) },
            ],
        });
        const history = new MateTable(el('agentEvalsHistory'), {
            noun: ['version', 'versions'],
            label: 'Score by version',
            search: false,
            empty: 'No version has been evaluated yet.',
            columns: [
                { key: 'version_number', label: 'Version', render: (h) => `v${esc(h.version_number)}` },
                { key: 'avg_score', label: 'Average score', align: 'right', render: (h) => esc(percent(h.avg_score)) },
                { key: 'pass_rate', label: 'Pass rate', align: 'right', render: (h) => esc(percent(h.pass_rate)) },
                { key: 'run_at', label: 'Last run', render: (h) => when(h.run_at) },
            ],
        });
        try {
            const [found, scores] = await Promise.all([getJson(`/dashboard/api/evals/agent/${name}`),
                                                      getJson(`/dashboard/api/evals/agent/${name}/history`)]);
            cases.setRows(found.test_cases || []);
            history.setRows((scores.history || []).slice().reverse());
        } catch (e) {
            cases.setError(e.message);
        }
    }

    // Model calls of the agent: totals per model, and each call
    let usageTable = null;

    function renderUsageModels(models) {
        el('agentUsageModels').replaceChildren(...(models || []).map((m) => {
            const card = document.createElement('div');
            card.className = 'm-card';
            card.innerHTML = `<div class="m-card-body agent-usage-model">
                <span class="m-section-title m-mono">${MateTable.esc(m.model_name || 'unknown model')}</span>
                <dl class="m-details">
                    <dt>Calls</dt><dd>${number(m.calls)}</dd>
                    <dt>Tokens in</dt><dd>${number(m.prompt_tokens)}</dd>
                    <dt>Tokens out</dt><dd>${number(m.response_tokens)}</dd>
                    <dt>Cost</dt><dd>${cost(m.cost_usd)}</dd>
                </dl></div>`;
            return card;
        }));
    }

    function startUsage() {
        const esc = MateTable.esc;
        usageTable = new MateTable(el('agentUsageTable'), {
            noun: ['call', 'calls'],
            label: 'Model calls',
            search: false,
            empty: 'No model calls in this period.',
            columns: [
                { key: 'timestamp', label: 'Time', sortable: false, render: (l) => when(l.timestamp) },
                { key: 'user_id', label: 'User', sortable: false },
                { key: 'model_name', label: 'Model', sortable: false, className: 'm-mono' },
                { key: 'prompt_tokens', label: 'In', sortable: false, align: 'right', render: (l) => esc(number(l.prompt_tokens)) },
                { key: 'response_tokens', label: 'Out', sortable: false, align: 'right', render: (l) => esc(number(l.response_tokens)) },
                { key: 'cost_usd', label: 'Cost', sortable: false, align: 'right', render: (l) => esc(cost(l.cost_usd)) },
                { key: 'status', label: 'Status', sortable: false,
                  render: (l) => (l.status && String(l.status).toUpperCase() !== 'SUCCESS'
                      ? `<span class="m-badge m-badge-danger" title="${esc(l.error_description || '')}">${esc(l.status)}</span>`
                      : esc(l.status || '')) },
            ],
            fetch: async ({ offset, limit }) => {
                const params = new URLSearchParams({ agent_name: config.name, hours: el('agentUsageHours').value,
                                                     page: String(Math.floor(offset / limit) + 1), limit: String(limit) });
                const result = await getJson(`/dashboard/api/usage/logs?${params}`);
                if (result.error) throw new Error(result.error);
                renderUsageModels(result.by_model);
                return { rows: result.logs || [], total: result.total_records || 0 };
            },
        });
    }

    function reloadUsage() {
        if (usageTable) usageTable.reload({ firstPage: true });
    }

    // The traces that ran the agent; Open shows one on the Traces page
    let tracesTable = null;

    function traceStart(t) {
        const starts = (t.spans || []).map((span) => span.start_time).filter(Boolean).sort();
        return starts[0] || null;
    }

    async function loadTraces() {
        const params = new URLSearchParams({ agent_name: config.name, hours: el('agentTracesHours').value, limit: '50' });
        try {
            const result = await getJson(`/dashboard/api/traces?${params}`);
            tracesTable.setRows((result.traces || []).map((t) => Object.assign({ started: traceStart(t) }, t)));
        } catch (e) {
            tracesTable.setError(e.message);
        }
    }

    function traceLink(t) {
        const params = new URLSearchParams({ trace_id: t.trace_id, hours: el('agentTracesHours').value });
        return `<a class="m-link" href="/dashboard/traces?${params}">Open</a>`;
    }

    function startTraces() {
        const esc = MateTable.esc;
        tracesTable = new MateTable(el('agentTracesTable'), {
            noun: ['trace', 'traces'],
            label: 'Traces',
            empty: 'No traces of this agent in this period.',
            columns: [
                { key: 'started', label: 'Started', searchable: false, render: (t) => when(t.started) },
                { key: 'root_name', label: 'Root span' },
                { key: 'total_duration_ms', label: 'Duration', align: 'right', searchable: false,
                  render: (t) => esc(t.total_duration_ms != null ? `${number(t.total_duration_ms)} ms` : '—') },
                { key: 'span_count', label: 'Spans', align: 'right', searchable: false },
                { label: 'Inspect', align: 'right', render: traceLink },
            ],
        });
        loadTraces();
    }

    function reloadTraces() {
        if (tracesTable) loadTraces();
    }

    function openTab(id) {
        const name = id.replace(/^tab-/, '');
        if (name === 'versions' && !loaded.versions) loadVersionHistory(config.id, config.name);
        if (name === 'sessions' && !loaded.sessions) startSessions();
        if (name === 'evals' && !loaded.evals) startEvals();
        if (name === 'usage' && !loaded.usage) startUsage();
        if (name === 'traces' && !loaded.traces) startTraces();
        loaded[name] = true;
        // The tab is in the address, so a reload (after a rollback, say) comes back to it
        try { history.replaceState(null, '', name === 'settings' ? window.location.pathname + window.location.search : `#${name}`); } catch (e) { /* no address change */ }
    }

    function startTabs() {
        const list = document.querySelector('#agentPage [data-m-tabs]');
        if (!list || mode !== 'edit') return;
        list.addEventListener('m-tab-change', (event) => openTab(event.detail.id));
        const wanted = window.location.hash.replace('#', '');
        if (wanted && el(`tab-${wanted}`)) mateTabs.select(`tab-${wanted}`);
    }

    async function remove() {
        closeMenu();
        const back = el('agentBackLink').getAttribute('href');
        deleteAgent(config.id, config.name, truthy(config.hardcoded), () => leave(back));
    }

    async function exportAgent() {
        closeMenu();
        try {
            const params = new URLSearchParams({ root_agent: config.name, project_id: config.project_id });
            const response = await fetch(`/dashboard/api/agents/export?${params}`, { credentials: 'same-origin' });
            if (!response.ok) throw new Error(String(response.status));
            const exported = await response.json();
            const url = URL.createObjectURL(new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' }));
            const a = document.createElement('a');
            a.href = url;
            a.download = `${config.name}-${new Date().toISOString().split('T')[0]}.json`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (e) {
            notify(`Export failed: ${e.message}`, 'error');
        }
    }

    function approvals() {
        // The dialog's settings go into the Tools field; Save stores them with the rest
        window.mateApprovalSettings.open({ name: field('Name').value, tool_config: jsonValue('ToolConfig') || '{}' },
            async (toolConfig) => {
                field('ToolConfig').value = asField(toolConfig);
                syncJsonToToolConfig(P);
                updateToolConfigSummary(P);
                changed();
                return true;
            });
    }

    // ── Guardrails: presets and JSON in step ──────────────────────────────

    function writeGuardrailJson(config) {
        const text = JSON.stringify(config, null, 2);
        field('GuardrailConfig').value = text;
        const editor = (typeof monacoEditors !== 'undefined') && monacoEditors[P + 'GuardrailConfigEditor'];
        if (editor) setJsonInEditor(editor, text);
    }

    function watchGuardrails() {
        const fields = el('agentGuardrailFields');
        if (!fields) return;
        const onEdit = (event) => {
            if (event.target === field('GuardrailConfig')) syncJsonToGuardrailConfig(P);
            else writeGuardrailJson(buildGuardrailConfigFromPresets(P));
        };
        fields.addEventListener('input', onEdit);
        fields.addEventListener('change', onEdit);
    }

    function clearGuardrails() {
        clearJsonField(P, 'GuardrailConfig');
        syncJsonToGuardrailConfig(P);
        changed();
    }

    function roleKey(event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            addRole(el('agentRoleAdd').value);
        }
    }

    // ── Start ─────────────────────────────────────────────────────────────

    function start() {
        fill();
        field('Type').addEventListener('change', showMaxIterations);
        watchGuardrails();
        // Enter in the role field adds the role rather than submitting the form
        el('agentRoleAdd').addEventListener('keydown', (event) => { if (event.key === 'Enter') event.preventDefault(); });
        el('agentForm').addEventListener('input', changed);
        el('agentForm').addEventListener('change', changed);
        // Clear, the dialogs (file search, memory blocks, approvals) and the pickers' chips
        document.addEventListener('click', () => setTimeout(changed, 0));
        startInstructionEditor();
        startTabs();
        if (mode === 'edit' && typeof loadFileSearchData === 'function') loadFileSearchData(P);
        markClean();

        window.addEventListener('beforeunload', (event) => {
            if (isDirty()) {
                event.preventDefault();
                event.returnValue = '';
            }
        });
        try {
            const message = sessionStorage.getItem(FLASH_KEY);
            if (message) {
                sessionStorage.removeItem(FLASH_KEY);
                notify(message);
            }
        } catch (e) { /* no flash */ }
    }

    window.mateAgentPage = {
        save, discard, remove, exportAgent, approvals,
        addParent, removeParent, removeRole, roleKey, clearGuardrails,
        addRoleFromInput: () => addRole(el('agentRoleAdd').value),
        // After a rollback: the page of the version's name, on the tab shown
        reloadAs: (name) => leave(pageUrl(name, window.location.hash)),
        reloadSessions, reloadUsage, reloadTraces,
        isDirty,
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
