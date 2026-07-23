/**
 * MATE Dashboard - Session Tracking (ADK & LangGraph)
 */

(function () {
    let currentPage = 1;
    const limit = 50;
    let currentSessionData = null;

    function escapeHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatTime(isoOrTs) {
        if (!isoOrTs) return '-';
        try {
            let d;
            if (typeof isoOrTs === 'number') {
                d = new Date(isoOrTs * 1000);
            } else {
                d = new Date(isoOrTs);
            }
            if (isNaN(d.getTime())) return String(isoOrTs);
            return d.toLocaleString();
        } catch (e) {
            return String(isoOrTs);
        }
    }

    async function loadSessions(page = 1) {
        currentPage = page;
        const tbody = document.getElementById('sessionTableBody');
        if (!tbody) return;

        tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-8 text-center text-gray-500 dark:text-gray-400"><i class="fas fa-spinner fa-spin mr-2"></i>Loading sessions...</td></tr>`;

        const runtime = document.getElementById('filterRuntime')?.value || 'all';
        const appName = document.getElementById('filterAppName')?.value?.trim() || '';
        const userId = document.getElementById('filterUserId')?.value?.trim() || '';
        const search = document.getElementById('filterSearch')?.value?.trim() || '';

        const params = new URLSearchParams({
            runtime: runtime,
            page: currentPage.toString(),
            limit: limit.toString()
        });
        if (appName) params.append('app_name', appName);
        if (userId) params.append('user_id', userId);
        if (search) params.append('search', search);

        try {
            const res = await fetch(`/dashboard/api/sessions?${params.toString()}`);
            if (!res.ok) {
                throw new Error(`Server returned status ${res.status}`);
            }
            const data = await res.json();
            renderSessionsTable(data);
        } catch (err) {
            console.error('Error loading sessions:', err);
            tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-6 text-center text-red-600 dark:text-red-400">Failed to load sessions: ${escapeHtml(err.message)}</td></tr>`;
        }
    }

    function renderSessionsTable(data) {
        const tbody = document.getElementById('sessionTableBody');
        const countSpan = document.getElementById('totalCount');
        const pageSpan = document.getElementById('pageInfo');

        const sessions = data.sessions || [];
        const total = data.total || 0;
        const totalPages = data.pages || 1;

        if (countSpan) countSpan.textContent = total;
        if (pageSpan) pageSpan.textContent = `Page ${data.page} of ${totalPages}`;

        if (sessions.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-8 text-center text-gray-500 dark:text-gray-400">No sessions found matching filters.</td></tr>`;
            renderPagination(data.page, totalPages);
            return;
        }

        let html = '';
        sessions.forEach(s => {
            const runtimeBadge = s.runtime === 'langgraph'
                ? `<span class="px-2 py-0.5 text-xs font-semibold rounded bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-700"><i class="fas fa-project-diagram mr-1"></i>LangGraph</span>`
                : `<span class="px-2 py-0.5 text-xs font-semibold rounded bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300 border border-indigo-300 dark:border-indigo-700"><i class="fas fa-robot mr-1"></i>ADK</span>`;

            const shortId = s.id.length > 20 ? s.id.substring(0, 18) + '...' : s.id;
            const previewText = s.last_preview ? escapeHtml(s.last_preview) : '<span class="text-gray-400 italic">No message preview</span>';

            html += `
            <tr class="hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors">
                <td class="px-3 py-3 text-xs font-mono text-gray-900 dark:text-white" title="${escapeHtml(s.id)}">
                    <div class="flex items-center space-x-1">
                        <span>${escapeHtml(shortId)}</span>
                        <button onclick="navigator.clipboard.writeText('${escapeHtml(s.id)}')" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 p-0.5" title="Copy Session ID">
                            <i class="fas fa-copy text-[10px]"></i>
                        </button>
                    </div>
                </td>
                <td class="px-3 py-3 text-xs font-medium text-gray-900 dark:text-white">${escapeHtml(s.app_name)}</td>
                <td class="px-3 py-3 text-xs text-gray-600 dark:text-gray-300">${escapeHtml(s.user_id)}</td>
                <td class="px-3 py-3 text-xs">${runtimeBadge}</td>
                <td class="px-3 py-3 text-xs text-gray-600 dark:text-gray-300">
                    <span class="px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 font-semibold text-gray-800 dark:text-gray-200">
                        ${s.event_count || 0} events
                    </span>
                </td>
                <td class="px-3 py-3 text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">${formatTime(s.updated_at)}</td>
                <td class="px-3 py-3 text-xs text-right whitespace-nowrap space-x-2">
                    <button onclick="window.sessionsApp.inspectSession('${escapeHtml(s.runtime)}', '${escapeHtml(s.app_name)}', '${escapeHtml(s.user_id)}', '${escapeHtml(s.id)}')" class="px-2.5 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded shadow-sm inline-flex items-center touch-target">
                        <i class="fas fa-eye mr-1"></i>Inspect
                    </button>
                    <button onclick="window.sessionsApp.deleteSession('${escapeHtml(s.runtime)}', '${escapeHtml(s.app_name)}', '${escapeHtml(s.user_id)}', '${escapeHtml(s.id)}')" class="px-2 py-1 text-xs bg-red-100 hover:bg-red-200 text-red-700 dark:bg-red-900/30 dark:hover:bg-red-900/50 dark:text-red-300 rounded inline-flex items-center touch-target">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                </td>
            </tr>`;
        });

        tbody.innerHTML = html;
        renderPagination(data.page, totalPages);
    }

    function renderPagination(page, totalPages) {
        const area = document.getElementById('paginationArea');
        if (!area) return;

        if (totalPages <= 1) {
            area.innerHTML = '';
            return;
        }

        let html = `
        <div class="flex items-center space-x-1">
            <button onclick="window.sessionsApp.loadSessions(${page - 1})" ${page <= 1 ? 'disabled' : ''} class="px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-100 dark:hover:bg-gray-700">
                <i class="fas fa-chevron-left"></i>
            </button>
            <span class="px-2 text-xs text-gray-600 dark:text-gray-400">${page} / ${totalPages}</span>
            <button onclick="window.sessionsApp.loadSessions(${page + 1})" ${page >= totalPages ? 'disabled' : ''} class="px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-600 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-100 dark:hover:bg-gray-700">
                <i class="fas fa-chevron-right"></i>
            </button>
        </div>`;
        area.innerHTML = html;
    }

    async function inspectSession(runtime, appName, userId, sessionId) {
        const modal = document.getElementById('sessionInspectorModal');
        const content = document.getElementById('sessionInspectorContent');
        if (!modal || !content) return;

        modal.classList.remove('hidden');
        content.innerHTML = `
            <div class="p-8 text-center text-gray-500 dark:text-gray-400">
                <i class="fas fa-circle-notch fa-spin text-2xl text-blue-600 mb-3 block"></i>
                Fetching session events and prompt details...
            </div>`;

        try {
            const params = new URLSearchParams({
                session_id: sessionId,
                app_name: appName,
                user_id: userId,
                runtime: runtime
            });
            const res = await fetch(`/dashboard/api/sessions/detail?${params.toString()}`);
            if (!res.ok) throw new Error(`Status ${res.status}`);
            const data = await res.json();
            currentSessionData = data;
            renderSessionInspector(data);
        } catch (err) {
            console.error('Error fetching session details:', err);
            content.innerHTML = `
                <div class="p-6 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 rounded-lg">
                    <h4 class="font-bold text-base mb-1"><i class="fas fa-exclamation-triangle mr-2"></i>Failed to Load Session</h4>
                    <p class="text-sm">${escapeHtml(err.message)}</p>
                </div>`;
        }
    }

    function renderSessionInspector(session) {
        const content = document.getElementById('sessionInspectorContent');
        if (!content) return;

        const runtimeBadge = session.runtime === 'langgraph'
            ? `<span class="px-2 py-0.5 text-xs font-semibold rounded bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300 border border-emerald-300"><i class="fas fa-project-diagram mr-1"></i>LangGraph</span>`
            : `<span class="px-2 py-0.5 text-xs font-semibold rounded bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300 border border-indigo-300"><i class="fas fa-robot mr-1"></i>ADK</span>`;

        const events = session.events || [];
        const stateObj = session.state || {};

        let html = `
        <!-- Summary Header -->
        <div class="bg-gray-50 dark:bg-gray-700/50 p-4 rounded-lg border border-gray-200 dark:border-gray-700 mb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
            <div>
                <span class="text-gray-500 dark:text-gray-400 block font-medium">Session ID</span>
                <span class="font-mono text-gray-900 dark:text-white select-all font-semibold">${escapeHtml(session.id)}</span>
            </div>
            <div>
                <span class="text-gray-500 dark:text-gray-400 block font-medium">App / Agent</span>
                <span class="font-semibold text-gray-900 dark:text-white">${escapeHtml(session.app_name || session.appName || '-')}</span>
            </div>
            <div>
                <span class="text-gray-500 dark:text-gray-400 block font-medium">User ID</span>
                <span class="text-gray-900 dark:text-white">${escapeHtml(session.user_id || session.userId || '-')}</span>
            </div>
            <div>
                <span class="text-gray-500 dark:text-gray-400 block font-medium">Runtime</span>
                <div>${runtimeBadge}</div>
            </div>
        </div>

        <!-- Inspector Tabs -->
        <div class="border-b border-gray-200 dark:border-gray-700 mb-4 flex space-x-4">
            <button onclick="window.sessionsApp.switchInspectorTab('turns')" id="tabBtnTurns" class="py-2 px-3 text-sm font-semibold border-b-2 border-blue-600 text-blue-600 dark:text-blue-400 focus:outline-none">
                <i class="fas fa-comments mr-1.5"></i>Conversation View (${events.length})
            </button>
            <button onclick="window.sessionsApp.switchInspectorTab('state')" id="tabBtnState" class="py-2 px-3 text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 border-b-2 border-transparent focus:outline-none">
                <i class="fas fa-database mr-1.5"></i>Session State
            </button>
            <button onclick="window.sessionsApp.switchInspectorTab('json')" id="tabBtnJson" class="py-2 px-3 text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 border-b-2 border-transparent focus:outline-none">
                <i class="fas fa-code mr-1.5"></i>Raw JSON
            </button>
        </div>

        <!-- Tab Content: Conversation Turns -->
        <div id="inspectorTabTurns" class="space-y-4">
            ${events.length === 0 ? '<div class="text-center py-6 text-gray-500">No event turns recorded for this session.</div>' : renderConversationTurns(events)}
        </div>

        <!-- Tab Content: Session State -->
        <div id="inspectorTabState" class="hidden">
            <div class="bg-gray-900 text-gray-100 p-4 rounded-lg overflow-x-auto text-xs font-mono max-h-[60vh]">
                <pre>${escapeHtml(JSON.stringify(stateObj, null, 2))}</pre>
            </div>
        </div>

        <!-- Tab Content: Raw JSON -->
        <div id="inspectorTabJson" class="hidden">
            <div class="bg-gray-900 text-gray-100 p-4 rounded-lg overflow-x-auto text-xs font-mono max-h-[60vh]">
                <pre>${escapeHtml(JSON.stringify(session, null, 2))}</pre>
            </div>
        </div>`;

        content.innerHTML = html;
    }

    function renderConversationTurns(events) {
        let html = '<div class="space-y-4 max-h-[65vh] overflow-y-auto pr-1">';

        events.forEach((evt, idx) => {
            const author = evt.author || 'system';
            const timestamp = formatTime(evt.timestamp);
            const content = evt.content || {};
            const role = content.role || (author === 'user' ? 'user' : 'model');
            const parts = content.parts || [];
            const usage = evt.usageMetadata || evt.usage_metadata || null;

            let textParts = [];
            let thoughtParts = [];
            let functionCalls = [];
            let functionResponses = [];

            parts.forEach(p => {
                if (typeof p === 'string') {
                    textParts.push(p);
                } else if (p.thought || p.is_thought) {
                    textParts.push(p.text || '');
                    thoughtParts.push(p.text || '');
                } else if (p.text) {
                    textParts.push(p.text);
                }
                if (p.functionCall) functionCalls.push(p.functionCall);
                if (p.function_call) functionCalls.push(p.function_call);
                if (p.functionResponse) functionResponses.push(p.functionResponse);
                if (p.function_response) functionResponses.push(p.function_response);
            });

            const mainText = textParts.join('\n\n');
            const thoughtsText = thoughtParts.join('\n\n');

            if (role === 'user' || author === 'user') {
                // User Prompt Turn
                html += `
                <div class="flex flex-col bg-blue-50/70 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-lg p-3.5 shadow-sm">
                    <div class="flex items-center justify-between border-b border-blue-200/60 dark:border-blue-800/60 pb-2 mb-2">
                        <div class="flex items-center space-x-2">
                            <span class="w-6 h-6 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold"><i class="fas fa-user"></i></span>
                            <span class="font-bold text-xs text-blue-900 dark:text-blue-200">User Prompt</span>
                        </div>
                        <span class="text-[11px] text-gray-500 dark:text-gray-400 font-mono">${timestamp}</span>
                    </div>
                    <div class="text-sm text-gray-900 dark:text-gray-100 whitespace-pre-wrap leading-relaxed">${escapeHtml(mainText || 'Empty prompt')}</div>
                </div>`;
            } else {
                // Agent / Model Response Turn
                html += `
                <div class="flex flex-col bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-3.5 shadow-sm">
                    <div class="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-2 mb-2">
                        <div class="flex items-center space-x-2">
                            <span class="w-6 h-6 rounded-full bg-purple-600 text-white flex items-center justify-center text-xs font-bold"><i class="fas fa-robot"></i></span>
                            <span class="font-bold text-xs text-purple-900 dark:text-purple-200">${escapeHtml(author)}</span>
                        </div>
                        <div class="flex items-center space-x-2">
                            ${usage ? renderUsageBadge(usage) : ''}
                            <span class="text-[11px] text-gray-500 dark:text-gray-400 font-mono">${timestamp}</span>
                        </div>
                    </div>`;

                // Render Agent Thoughts if available
                if (thoughtsText) {
                    html += `
                    <details class="mb-3 bg-purple-50/50 dark:bg-purple-950/20 border border-purple-200/50 dark:border-purple-800/50 rounded p-2.5 text-xs text-purple-900 dark:text-purple-300">
                        <summary class="font-semibold cursor-pointer select-none flex items-center text-purple-700 dark:text-purple-300">
                            <i class="fas fa-brain mr-1.5"></i>Agent Reasoning &amp; Thoughts
                        </summary>
                        <div class="mt-2 whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-gray-700 dark:text-gray-300 pl-2 border-l-2 border-purple-400">
                            ${escapeHtml(thoughtsText)}
                        </div>
                    </details>`;
                }

                // Main Response Text
                if (mainText && mainText !== thoughtsText) {
                    html += `<div class="text-sm text-gray-900 dark:text-gray-100 whitespace-pre-wrap leading-relaxed mb-3">${escapeHtml(mainText)}</div>`;
                }

                // Render Tool Calls
                if (functionCalls.length > 0) {
                    functionCalls.forEach(call => {
                        html += `
                        <div class="mb-2 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded p-2.5 text-xs">
                            <div class="font-bold text-amber-800 dark:text-amber-300 flex items-center mb-1">
                                <i class="fas fa-wrench mr-1.5"></i>Tool Call: <code class="ml-1 font-mono text-amber-900 dark:text-amber-200">${escapeHtml(call.name)}</code>
                            </div>
                            <div class="bg-gray-900 text-amber-100 p-2 rounded text-[11px] font-mono overflow-x-auto">
                                <pre>${escapeHtml(JSON.stringify(call.args || {}, null, 2))}</pre>
                            </div>
                        </div>`;
                    });
                }

                // Render Tool Responses
                if (functionResponses.length > 0) {
                    functionResponses.forEach(resp => {
                        html += `
                        <div class="mb-2 bg-teal-50 dark:bg-teal-950/30 border border-teal-200 dark:border-teal-800 rounded p-2.5 text-xs">
                            <div class="font-bold text-teal-800 dark:text-teal-300 flex items-center mb-1">
                                <i class="fas fa-check-circle mr-1.5"></i>Tool Output: <code class="ml-1 font-mono text-teal-900 dark:text-teal-200">${escapeHtml(resp.name)}</code>
                            </div>
                            <div class="bg-gray-900 text-teal-100 p-2 rounded text-[11px] font-mono overflow-x-auto max-h-40">
                                <pre>${escapeHtml(JSON.stringify(resp.response || {}, null, 2))}</pre>
                            </div>
                        </div>`;
                    });
                }

                html += `</div>`;
            }
        });

        html += '</div>';
        return html;
    }

    function renderUsageBadge(usage) {
        const prompt = usage.promptTokenCount || usage.prompt_token_count || 0;
        const resp = usage.candidatesTokenCount || usage.candidates_token_count || 0;
        const total = usage.totalTokenCount || usage.total_token_count || (prompt + resp);
        return `<span class="px-2 py-0.5 text-[10px] font-mono rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-600" title="Tokens: ${prompt} prompt + ${resp} candidates = ${total} total">
            <i class="fas fa-bolt text-yellow-500 mr-0.5"></i>${total} tokens
        </span>`;
    }

    function switchInspectorTab(tab) {
        ['turns', 'state', 'json'].forEach(t => {
            const btn = document.getElementById(`tabBtn${t.charAt(0).toUpperCase() + t.slice(1)}`);
            const panel = document.getElementById(`inspectorTab${t.charAt(0).toUpperCase() + t.slice(1)}`);
            if (btn && panel) {
                if (t === tab) {
                    btn.classList.add('border-blue-600', 'text-blue-600', 'dark:text-blue-400');
                    btn.classList.remove('border-transparent', 'text-gray-500');
                    panel.classList.remove('hidden');
                } else {
                    btn.classList.remove('border-blue-600', 'text-blue-600', 'dark:text-blue-400');
                    btn.classList.add('border-transparent', 'text-gray-500');
                    panel.classList.add('hidden');
                }
            }
        });
    }

    function closeSessionInspector() {
        const modal = document.getElementById('sessionInspectorModal');
        if (modal) modal.classList.add('hidden');
        currentSessionData = null;
    }

    async function deleteSession(runtime, appName, userId, sessionId) {
        if (!confirm(`Are you sure you want to delete session "${sessionId}"?\nThis action cannot be undone.`)) {
            return;
        }

        try {
            const params = new URLSearchParams({
                session_id: sessionId,
                app_name: appName,
                user_id: userId,
                runtime: runtime
            });
            const res = await fetch(`/dashboard/api/sessions?${params.toString()}`, {
                method: 'DELETE'
            });
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || `Status ${res.status}`);
            }
            loadSessions(currentPage);
        } catch (err) {
            alert(`Failed to delete session: ${err.message}`);
        }
    }

    // Expose app instance globally
    window.sessionsApp = {
        loadSessions,
        inspectSession,
        closeSessionInspector,
        deleteSession,
        switchInspectorTab
    };

    // Auto load on init
    document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('sessionTableBody')) {
            loadSessions(1);
        }
    });

})();
