/**
 * Dashboard traces page - OpenTelemetry trace viewer
 */

async function loadTraces() {
    const hours = parseInt(document.getElementById('timeRangeSelect').value, 10) || 24;
    const tbody = document.getElementById('tracesTableBody');
    const errEl = document.getElementById('tracesError');
    errEl.classList.add('hidden');

    tbody.innerHTML = '<tr><td colspan="5" class="px-3 py-8 text-center text-gray-500 dark:text-gray-400">Loading...</td></tr>';

    try {
        const resp = await fetch(`/dashboard/api/traces?hours=${hours}&limit=50`, { credentials: 'same-origin' });
        const data = await resp.json();

        if (data.error && !data.traces) {
            errEl.textContent = data.error;
            errEl.classList.remove('hidden');
        }

        const traces = data.traces || [];
        if (traces.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="px-3 py-8 text-center text-gray-500 dark:text-gray-400">No traces found. Enable OTEL_TRACING_ENABLED and OTEL_TRACES_DB_EXPORT, then run some agent requests.</td></tr>';
            return;
        }

        tbody.innerHTML = traces.map(t => `
            <tr class="hover:bg-gray-50 dark:hover:bg-gray-700">
                <td data-label="Trace ID" class="px-3 py-2 text-xs font-mono text-gray-700 dark:text-gray-300 trace-id-cell" title="${escapeHtml(t.trace_id)}">${escapeHtml(t.trace_id)}</td>
                <td data-label="Root Span" class="px-3 py-2 text-xs text-gray-900 dark:text-white">${escapeHtml(t.root_name)}</td>
                <td data-label="Duration" class="px-3 py-2 text-xs text-gray-600 dark:text-gray-400">${t.total_duration_ms != null ? t.total_duration_ms + ' ms' : '-'}</td>
                <td data-label="Spans" class="px-3 py-2 text-xs text-gray-600 dark:text-gray-400">${t.span_count}</td>
                <td data-label="Actions" class="px-3 py-2">
                    <button type="button" data-trace-id="${escapeHtml(t.trace_id)}" data-hours="${hours}" onclick="showTraceDetail(this.dataset.traceId, parseInt(this.dataset.hours, 10))" class="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 text-xs min-h-[44px] touch-target px-2">
                        <i class="fas fa-expand-alt mr-1"></i>View
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="px-3 py-8 text-center text-red-500">Failed to load traces</td></tr>';
        errEl.textContent = String(e);
        errEl.classList.remove('hidden');
    }
}

async function showTraceDetail(traceId, hours) {
    const modalEl = document.getElementById('traceDetailModal') || document.getElementById('traceDetail');
    const contentEl = document.getElementById('traceDetailContent');
    if (!modalEl || !contentEl) return;
    modalEl.classList.remove('hidden');
    contentEl.innerHTML = '<div class="text-gray-500 dark:text-gray-400">Loading...</div>';

    try {
        const resp = await fetch(`/dashboard/api/traces?hours=${hours || 24}&trace_id=${encodeURIComponent(traceId)}`, { credentials: 'same-origin' });
        const data = await resp.json();
        const traces = data.traces || [];
        const trace = traces.find(t => t.trace_id === traceId) || traces[0];
        if (!trace) {
            contentEl.innerHTML = '<div class="text-gray-500 dark:text-gray-400">Trace not found</div>';
            return;
        }

        // Build tree from spans (parent_span_id -> children)
        const byParent = {};
        trace.spans.forEach(s => {
            const pid = s.parent_span_id || '__root__';
            if (!byParent[pid]) byParent[pid] = [];
            byParent[pid].push(s);
        });
        // Sort children by start_time for consistent display
        Object.keys(byParent).forEach(k => {
            byParent[k].sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
        });

        function renderSpan(span, depth) {
            const indent = '&nbsp;'.repeat(depth * 4);
            const attrs = span.attributes && Object.keys(span.attributes).length ? JSON.stringify(span.attributes) : '';
            const statusClass = span.status === 'ERROR' ? 'text-red-600 dark:text-red-400' : 'text-gray-700 dark:text-gray-300';
            return `
                <div class="border-l-2 border-gray-200 dark:border-gray-600 pl-2 py-1" style="margin-left: ${depth * 12}px">
                    <div class="text-xs ${statusClass}">
                        ${indent}<span class="font-medium">${escapeHtml(span.name)}</span>
                        ${span.duration_ms != null ? `<span class="text-gray-500 dark:text-gray-400 ml-2">${span.duration_ms} ms</span>` : ''}
                        ${span.status ? `<span class="ml-2 text-xs">${escapeHtml(span.status)}</span>` : ''}
                    </div>
                    ${attrs ? `<div class="text-xs text-gray-500 dark:text-gray-400 mt-1 font-mono truncate" title="${escapeHtml(attrs)}">${escapeHtml(attrs.slice(0, 80))}${attrs.length > 80 ? '...' : ''}</div>` : ''}
                </div>
            `;
        }

        function buildTree(spanId, depth) {
            const children = byParent[spanId] || [];
            let html = '';
            children.forEach(s => {
                html += renderSpan(s, depth);
                html += buildTree(s.span_id, depth + 1);
            });
            return html;
        }

        contentEl.innerHTML = `
            <div class="mb-2 text-xs text-gray-500 dark:text-gray-400">Trace ID: ${escapeHtml(trace.trace_id)}</div>
            <div class="space-y-0">${buildTree('__root__', 0)}</div>
        `;
    } catch (e) {
        contentEl.innerHTML = '<div class="text-red-500">Failed to load trace</div>';
    }
}

function closeTraceDetail() {
    const modalEl = document.getElementById('traceDetailModal') || document.getElementById('traceDetail');
    if (modalEl) modalEl.classList.add('hidden');
}

function escapeHtml(s) {
    if (!s) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

document.addEventListener('DOMContentLoaded', () => loadTraces());
