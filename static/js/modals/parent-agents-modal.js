// ============================================================================
// Parent Agents Selection Modal Functions
// ============================================================================

let currentParentAgentsPrefix = null;
let currentParentAgentsList = [];

async function openParentAgentsModal(prefix) {
    currentParentAgentsPrefix = prefix;
    const modal = document.getElementById(`${prefix}ParentAgentsModal`);
    if (!modal) return;
    
    // Get project ID from the form
    const projectSelect = document.getElementById(`${prefix}Project`);
    if (!projectSelect || !projectSelect.value) {
        if (typeof showNotification === 'function') {
            showNotification('Please select a project first', 'error');
        } else {
            showNotification('Please select a project first', 'warning');
        }
        return;
    }
    
    const projectId = projectSelect.value;
    
    // Show modal
    modal.classList.remove('hidden');
    document.body.classList.add('config-modal-open');
    
    // Show loading state
    document.getElementById(`${prefix}ParentAgentsLoading`).classList.remove('hidden');
    document.getElementById(`${prefix}ParentAgentsError`).classList.add('hidden');
    document.getElementById(`${prefix}ParentAgentsList`).classList.add('hidden');
    document.getElementById(`${prefix}ParentAgentsEmpty`).classList.add('hidden');
    
    // Get current agent name (if editing) to exclude it from the list
    const currentAgentName = prefix === 'editAgent' 
        ? document.getElementById('editAgentName')?.value 
        : (prefix === 'copyAgent' ? document.getElementById('copyAgentName')?.value : null);
    
    try {
        // Fetch agents from the same project
        const response = await fetch(`/dashboard/api/agents?project_id=${encodeURIComponent(projectId)}`, {
            credentials: 'same-origin'
        });
        
        if (!response.ok) {
            throw new Error(`Failed to fetch agents: ${response.statusText}`);
        }
        
        const data = await response.json();
        const agents = data.configs || [];
        
        // Filter out the current agent if editing
        const availableAgents = agents.filter(agent => {
            if (currentAgentName && agent.name === currentAgentName) {
                return false;
            }
            return true;
        });
        
        currentParentAgentsList = availableAgents;
        
        // Hide loading
        document.getElementById(`${prefix}ParentAgentsLoading`).classList.add('hidden');
        
        if (availableAgents.length === 0) {
            document.getElementById(`${prefix}ParentAgentsSearchContainer`).classList.add('hidden');
            document.getElementById(`${prefix}ParentAgentsEmpty`).classList.remove('hidden');
            return;
        }
        
        // Show search container
        document.getElementById(`${prefix}ParentAgentsSearchContainer`).classList.remove('hidden');
        
        // Clear search field
        const searchInput = document.getElementById(`${prefix}ParentAgentsSearch`);
        if (searchInput) {
            searchInput.value = '';
        }
        const searchClearBtn = document.getElementById(`${prefix}ParentAgentsSearchClear`);
        if (searchClearBtn) {
            searchClearBtn.classList.add('hidden');
        }
        
        // Get currently selected parent agents
        const parentsField = document.getElementById(`${prefix}Parents`);
        let selectedParents = [];
        if (parentsField && parentsField.value) {
            try {
                selectedParents = JSON.parse(parentsField.value);
                if (!Array.isArray(selectedParents)) {
                    selectedParents = [];
                }
            } catch (e) {
                selectedParents = [];
            }
        }
        
        // Build the list
        renderParentAgentsList(prefix, availableAgents, selectedParents);
        
    } catch (error) {
        console.error('Error loading agents:', error);
        document.getElementById(`${prefix}ParentAgentsLoading`).classList.add('hidden');
        document.getElementById(`${prefix}ParentAgentsSearchContainer`).classList.add('hidden');
        const errorDiv = document.getElementById(`${prefix}ParentAgentsError`);
        errorDiv.querySelector('p').textContent = `Error loading agents: ${error.message}`;
        errorDiv.classList.remove('hidden');
    }
}

function renderParentAgentsList(prefix, agents, selectedParents, searchTerm = '') {
    const listContainer = document.getElementById(`${prefix}ParentAgentsList`);
    const noResultsDiv = document.getElementById(`${prefix}ParentAgentsNoResults`);
    listContainer.innerHTML = '';
    
    // Filter agents based on search term
    const filteredAgents = searchTerm 
        ? agents.filter(agent => {
            const searchLower = searchTerm.toLowerCase();
            const nameMatch = agent.name?.toLowerCase().includes(searchLower) || false;
            const typeMatch = agent.type?.toLowerCase().includes(searchLower) || false;
            const descMatch = agent.description?.toLowerCase().includes(searchLower) || false;
            return nameMatch || typeMatch || descMatch;
        })
        : agents;
    
    if (filteredAgents.length === 0 && searchTerm) {
        listContainer.classList.add('hidden');
        noResultsDiv.classList.remove('hidden');
        return;
    }
    
    noResultsDiv.classList.add('hidden');
    
    filteredAgents.forEach(agent => {
        const isChecked = selectedParents.includes(agent.name);
        const agentItem = document.createElement('div');
        agentItem.className = 'flex items-center space-x-2 p-2 rounded border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/50';
        agentItem.setAttribute('data-agent-name', agent.name || '');
        agentItem.setAttribute('data-agent-type', agent.type || '');
        agentItem.setAttribute('data-agent-description', agent.description || '');
        agentItem.innerHTML = `
            <input type="checkbox" 
                   id="${prefix}ParentAgent_${agent.id}" 
                   value="${agent.name}" 
                   ${isChecked ? 'checked' : ''}
                   class="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700">
            <label for="${prefix}ParentAgent_${agent.id}" class="flex-1 text-xs text-gray-700 dark:text-gray-300 cursor-pointer">
                <span class="font-medium">${agent.name}</span>
                ${agent.type ? `<span class="text-gray-500 dark:text-gray-400 ml-2">(${agent.type})</span>` : ''}
                ${agent.description ? `<span class="text-gray-400 dark:text-gray-500 ml-2 text-[10px]">- ${agent.description.substring(0, 50)}${agent.description.length > 50 ? '...' : ''}</span>` : ''}
            </label>
        `;
        listContainer.appendChild(agentItem);
    });
    
    listContainer.classList.remove('hidden');
}

function filterParentAgentsList(prefix) {
    const searchInput = document.getElementById(`${prefix}ParentAgentsSearch`);
    const searchClearBtn = document.getElementById(`${prefix}ParentAgentsSearchClear`);
    const searchTerm = searchInput ? searchInput.value.trim() : '';
    
    // Show/hide clear button
    if (searchClearBtn) {
        if (searchTerm) {
            searchClearBtn.classList.remove('hidden');
        } else {
            searchClearBtn.classList.add('hidden');
        }
    }
    
    // Get currently selected parent agents
    const parentsField = document.getElementById(`${prefix}Parents`);
    let selectedParents = [];
    if (parentsField && parentsField.value) {
        try {
            selectedParents = JSON.parse(parentsField.value);
            if (!Array.isArray(selectedParents)) {
                selectedParents = [];
            }
        } catch (e) {
            selectedParents = [];
        }
    }
    
    // Re-render list with filter
    renderParentAgentsList(prefix, currentParentAgentsList, selectedParents, searchTerm);
}

function clearParentAgentsSearch(prefix) {
    const searchInput = document.getElementById(`${prefix}ParentAgentsSearch`);
    const searchClearBtn = document.getElementById(`${prefix}ParentAgentsSearchClear`);
    
    if (searchInput) {
        searchInput.value = '';
    }
    if (searchClearBtn) {
        searchClearBtn.classList.add('hidden');
    }
    
    filterParentAgentsList(prefix);
}

function closeParentAgentsModal(prefix) {
    const modal = document.getElementById(`${prefix}ParentAgentsModal`);
    if (!modal) return;
    modal.classList.add('hidden');
    
    // Clear search
    clearParentAgentsSearch(prefix);
    
    // Remove the body class if no visible config modals remain
    const anyVisible = Array.from(document.querySelectorAll('[id$="ConfigModal"], [id$="SchemaModal"], [id$="ParentAgentsModal"]'))
        .some(element => !element.classList.contains('hidden'));
    if (!anyVisible) {
        document.body.classList.remove('config-modal-open');
    }
}

function applyParentAgentsSelection(prefix) {
    const checkboxes = document.querySelectorAll(`#${prefix}ParentAgentsList input[type="checkbox"]:checked`);
    const selectedAgents = Array.from(checkboxes).map(cb => cb.value);
    
    // Update the parent agents field
    const parentsField = document.getElementById(`${prefix}Parents`);
    if (parentsField) {
        parentsField.value = JSON.stringify(selectedAgents);
        
        // Update Monaco editor if it exists
        if (typeof monacoEditors !== 'undefined' && monacoEditors && monacoEditors[`${prefix}ParentsEditor`]) {
            if (typeof setJsonInEditor === 'function') {
                setJsonInEditor(monacoEditors[`${prefix}ParentsEditor`], parentsField.value);
            }
        }
    }
    
    closeParentAgentsModal(prefix);
}

// Export functions to window for global access
window.openParentAgentsModal = openParentAgentsModal;
window.renderParentAgentsList = renderParentAgentsList;
window.filterParentAgentsList = filterParentAgentsList;
window.clearParentAgentsSearch = clearParentAgentsSearch;
window.closeParentAgentsModal = closeParentAgentsModal;
window.applyParentAgentsSelection = applyParentAgentsSelection;

