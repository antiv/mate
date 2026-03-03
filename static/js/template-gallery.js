// ============================================================================
// Template Gallery - Load, display, and import agent templates
// ============================================================================

let allTemplates = [];

async function loadTemplates() {
    const grid = document.getElementById('templateGrid');
    const loading = document.getElementById('templateLoading');
    const empty = document.getElementById('templateEmpty');
    const search = document.getElementById('templateSearch')?.value || '';
    const category = document.getElementById('categoryFilter')?.value || '';

    loading.classList.remove('hidden');
    empty.classList.add('hidden');
    document.querySelectorAll('.template-card').forEach(el => el.remove());

    try {
        const params = new URLSearchParams();
        if (search) params.append('search', search);
        if (category) params.append('category', category);
        const url = `/dashboard/api/templates${params.toString() ? '?' + params.toString() : ''}`;
        const data = await apiCall(url);
        allTemplates = data.templates || [];
        renderTemplates(allTemplates);
    } catch (error) {
        console.error('Failed to load templates:', error);
        showNotification('Failed to load templates: ' + (error.message || 'Unknown error'), 'error');
        allTemplates = [];
    } finally {
        loading.classList.add('hidden');
    }
}

function renderTemplates(templates) {
    const grid = document.getElementById('templateGrid');
    const loading = document.getElementById('templateLoading');
    const empty = document.getElementById('templateEmpty');

    loading.classList.add('hidden');
    document.querySelectorAll('.template-card').forEach(el => el.remove());

    if (!templates || templates.length === 0) {
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    const categoryColors = {
        support: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
        research: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
        code: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200',
        content: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200',
        demo: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200',
    };

    templates.forEach((t) => {
        const card = document.createElement('div');
        card.className = 'template-card bg-white dark:bg-gray-800 rounded-lg shadow p-4 flex flex-col';
        const cat = (t.category || 'demo').toLowerCase();
        const badgeClass = categoryColors[cat] || categoryColors.demo;
        card.innerHTML = `
            <div class="flex-1">
                <div class="flex items-start justify-between mb-2">
                    <h3 class="font-semibold text-gray-900 dark:text-white">${escapeHtml(t.name || t.id || 'Unnamed')}</h3>
                    <span class="px-2 py-0.5 text-xs rounded ${badgeClass}">${escapeHtml(cat)}</span>
                </div>
                <p class="text-sm text-gray-600 dark:text-gray-400 line-clamp-3">${escapeHtml(t.description || '')}</p>
                ${t.version ? `<p class="text-xs text-gray-500 dark:text-gray-500 mt-2">v${escapeHtml(t.version)}</p>` : ''}
            </div>
            <div class="mt-4">
                <button onclick="importTemplate('${escapeHtml(t.id)}')" class="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg flex items-center justify-center">
                    <i class="fas fa-download mr-2"></i>Import
                </button>
            </div>
        `;
        grid.appendChild(card);
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function importTemplate(templateId) {
    const projectName = prompt('Project name (leave empty to use template default):');
    if (projectName === null) return;

    try {
        const body = { template_id: templateId };
        if (projectName.trim()) body.project_name = projectName.trim();

        const result = await apiCall('/dashboard/api/templates/import', 'POST', body);

        if (result.success) {
            showNotification(
                `Imported: ${result.agents_created} agents, ${result.memory_blocks_created} memory blocks. Project: ${result.project_name}`,
                'success'
            );
            window.location.href = `/dashboard/agents?project_id=${result.project_id}`;
        } else {
            const errMsg = result.error || result.detail || 'Import failed';
            showNotification(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg), 'error');
        }
    } catch (error) {
        const msg = error?.detail || error?.message || 'Import failed';
        showNotification(typeof msg === 'string' ? msg : JSON.stringify(msg), 'error');
    }
}

// Debounced search
let searchTimeout;
document.addEventListener('DOMContentLoaded', function () {
    loadTemplates();

    const searchEl = document.getElementById('templateSearch');
    const categoryEl = document.getElementById('categoryFilter');
    if (searchEl) {
        searchEl.addEventListener('input', function () {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(loadTemplates, 300);
        });
    }
    if (categoryEl) {
        categoryEl.addEventListener('change', loadTemplates);
    }
});
