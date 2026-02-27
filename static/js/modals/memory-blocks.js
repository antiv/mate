/**
 * Memory Blocks Modal Handler
 * Handles visualization and editing of memory blocks for agents with memory tools
 */

let currentAgentName = null;
let currentBlocks = [];
let filterConditions = [];
let currentLabelSearch = null;
let currentValueSearch = null;

// Show memory blocks modal for an agent
function showMemoryBlocksModal(agentName) {
    currentAgentName = agentName;
    document.getElementById('createBlockAgentName').value = agentName;
    
    // Add default filter condition: Block Name contains 'system_instruction_'
    filterConditions = [{
        field: 'Block Name',
        operator: 'contains',
        value: 'system_instruction_'
    }];
    
    // Render the filter condition in the UI
    const conditionsEl = document.getElementById('filterConditions');
    conditionsEl.innerHTML = '';
    const conditionEl = document.createElement('div');
    conditionEl.className = 'flex items-center space-x-2';
    conditionEl.innerHTML = `
        <select onchange="updateFilterCondition(0, 'field', this.value)" class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white">
            <option value="Block Name" selected>Block Name</option>
            <option value="Content">Content</option>
        </select>
        <select onchange="updateFilterCondition(0, 'operator', this.value)" class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white">
            <option value="contains" selected>contains</option>
            <option value="equals">equals</option>
            <option value="starts with">starts with</option>
        </select>
        <input type="text" value="system_instruction_" onchange="updateFilterCondition(0, 'value', this.value)" placeholder="Search..." class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white w-32" />
        <button onclick="removeFilterCondition(0)" class="text-red-500 hover:text-red-700">
            <i class="fas fa-times"></i>
        </button>
    `;
    conditionsEl.appendChild(conditionEl);
    
    document.getElementById('memoryBlocksModal').classList.remove('hidden');
    
    // Load blocks with the default filter
    currentLabelSearch = 'system_instruction_';
    currentValueSearch = null;
    loadMemoryBlocks('system_instruction_', null);
}

// Hide memory blocks modal
function hideMemoryBlocksModal() {
    document.getElementById('memoryBlocksModal').classList.add('hidden');
    currentAgentName = null;
    currentBlocks = [];
    filterConditions = [];
    currentLabelSearch = null;
    currentValueSearch = null;
    document.getElementById('filterConditions').innerHTML = '';
}

// Load memory blocks for current agent
async function loadMemoryBlocks(labelSearch = null, valueSearch = null) {
    const listEl = document.getElementById('memoryBlocksList');
    const loadingEl = document.getElementById('memoryBlocksLoading');
    const emptyEl = document.getElementById('memoryBlocksEmpty');
    
    listEl.classList.add('hidden');
    emptyEl.classList.add('hidden');
    loadingEl.classList.remove('hidden');
    
    try {
        const url = `/dashboard/api/agents/${encodeURIComponent(currentAgentName)}/memory-blocks${labelSearch || valueSearch ? '?' + new URLSearchParams({ label_search: labelSearch || '', value_search: valueSearch || '' }).toString() : ''}`;
        const response = await fetch(url, {
            credentials: 'same-origin'
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentBlocks = data.blocks || [];
            renderMemoryBlocks(currentBlocks);
        } else {
            showNotification(data.error || 'Failed to load memory blocks', 'error');
            emptyEl.classList.remove('hidden');
        }
    } catch (error) {
        console.error('Error loading memory blocks:', error);
        showNotification('Error loading memory blocks', 'error');
        emptyEl.classList.remove('hidden');
    } finally {
        loadingEl.classList.add('hidden');
    }
}

// Render memory blocks list
function renderMemoryBlocks(blocks) {
    const listEl = document.getElementById('memoryBlocksList');
    const emptyEl = document.getElementById('memoryBlocksEmpty');
    
    if (blocks.length === 0) {
        listEl.classList.add('hidden');
        emptyEl.classList.remove('hidden');
        return;
    }
    
    listEl.classList.remove('hidden');
    emptyEl.classList.add('hidden');
    
    listEl.innerHTML = blocks.map((block, index) => {
        // Always prefer block_id (UUID) over label for API calls
        // For agent memory blocks, label can be used as block_id, but for shared blocks, must use UUID
        // Check explicitly for null/undefined, not just falsy values
        const blockId = (block.block_id !== null && block.block_id !== undefined) ? block.block_id : 
                       (block.id !== null && block.id !== undefined) ? block.id : null;
        const label = block.label || block.block_label || 'Unnamed';
        const value = block.value || '';
        const description = block.description || '';
        const limit = block.limit || '';
        
        // Determine identifier for API calls:
        // - If block_id exists (not null/undefined), always use it (required for shared blocks, works for agent blocks)
        // - If block_id is missing, fall back to label (only valid for agent blocks)
        // This handles both shared blocks (require UUID) and agent blocks (can use label)
        const identifier = (blockId !== null && blockId !== undefined && blockId !== '') ? blockId : label;
        const displayId = (blockId !== null && blockId !== undefined && blockId !== '') ? blockId : label;
        const shortId = displayId.length > 20 ? displayId.substring(0, 20) + '...' : displayId;
        
        // Format value preview (first 200 chars)
        const valuePreview = value.length > 200 ? value.substring(0, 200) + '...' : value;
        const isJson = value.trim().startsWith('{') || value.trim().startsWith('[');
        
        return `
            <div class="bg-gray-50 dark:bg-gray-700 rounded-lg p-4 border border-gray-200 dark:border-gray-600">
                <div class="flex items-start justify-between mb-2">
                    <div class="flex items-center space-x-2 flex-1">
                        <i class="fas fa-database text-gray-400"></i>
                        <span class="font-medium text-gray-900 dark:text-white">${escapeHtml(label)}</span>
                        <span class="text-xs text-gray-500 dark:text-gray-400">block...${shortId.substring(Math.max(0, shortId.length - 12))}</span>
                        <button onclick="copyToClipboard('${identifier}')" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" title="Copy block ID">
                            <i class="fas fa-copy text-xs"></i>
                        </button>
                    </div>
                    <button onclick="editMemoryBlock('${identifier}', '${escapeHtml(label)}')" class="px-3 py-1 text-xs font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300">
                        Edit
                    </button>
                </div>
                ${description ? `<p class="text-sm text-gray-600 dark:text-gray-400 mb-2">${escapeHtml(description)}</p>` : ''}
                <div class="bg-white dark:bg-gray-800 rounded p-3 border border-gray-200 dark:border-gray-600">
                    <pre class="text-xs text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-mono">${escapeHtml(valuePreview)}</pre>
                </div>
            </div>
        `;
    }).join('');
}

// Add filter condition
function addFilterCondition() {
    const conditionsEl = document.getElementById('filterConditions');
    const conditionIndex = filterConditions.length;
    
    const condition = {
        field: 'Block Name',
        operator: 'contains',
        value: ''
    };
    
    filterConditions.push(condition);
    
    const conditionEl = document.createElement('div');
    conditionEl.className = 'flex items-center space-x-2';
    conditionEl.innerHTML = `
        <select onchange="updateFilterCondition(${conditionIndex}, 'field', this.value)" class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white">
            <option value="Block Name" selected>Block Name</option>
            <option value="Content">Content</option>
        </select>
        <select onchange="updateFilterCondition(${conditionIndex}, 'operator', this.value)" class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white">
            <option value="contains" selected>contains</option>
            <option value="equals">equals</option>
            <option value="starts with">starts with</option>
        </select>
        <input type="text" onchange="updateFilterCondition(${conditionIndex}, 'value', this.value)" placeholder="Search..." class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white w-32" />
        <button onclick="removeFilterCondition(${conditionIndex})" class="text-red-500 hover:text-red-700">
            <i class="fas fa-times"></i>
        </button>
    `;
    
    conditionsEl.appendChild(conditionEl);
}

// Update filter condition
function updateFilterCondition(index, key, value) {
    if (filterConditions[index]) {
        filterConditions[index][key] = value;
    }
}

// Remove filter condition
function removeFilterCondition(index) {
    filterConditions.splice(index, 1);
    const conditionsEl = document.getElementById('filterConditions');
    conditionsEl.innerHTML = '';
    filterConditions.forEach((condition, idx) => {
        const conditionEl = document.createElement('div');
        conditionEl.className = 'flex items-center space-x-2';
        conditionEl.innerHTML = `
            <select onchange="updateFilterCondition(${idx}, 'field', this.value)" class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white">
                <option value="Block Name" ${condition.field === 'Block Name' ? 'selected' : ''}>Block Name</option>
                <option value="Content" ${condition.field === 'Content' ? 'selected' : ''}>Content</option>
            </select>
            <select onchange="updateFilterCondition(${idx}, 'operator', this.value)" class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white">
                <option value="contains" ${condition.operator === 'contains' ? 'selected' : ''}>contains</option>
                <option value="equals" ${condition.operator === 'equals' ? 'selected' : ''}>equals</option>
                <option value="starts with" ${condition.operator === 'starts with' ? 'selected' : ''}>starts with</option>
            </select>
            <input type="text" value="${escapeHtml(condition.value)}" onchange="updateFilterCondition(${idx}, 'value', this.value)" placeholder="Search..." class="px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg dark:bg-gray-700 dark:text-white w-32" />
            <button onclick="removeFilterCondition(${idx})" class="text-red-500 hover:text-red-700">
                <i class="fas fa-times"></i>
            </button>
        `;
        conditionsEl.appendChild(conditionEl);
    });
}

// Search memory blocks
function searchMemoryBlocks() {
    let labelSearch = null;
    let valueSearch = null;
    
    filterConditions.forEach(condition => {
        if (condition.field === 'Block Name' && condition.value) {
            labelSearch = condition.value;
        } else if (condition.field === 'Content' && condition.value) {
            valueSearch = condition.value;
        }
    });
    
    // Store current search conditions
    currentLabelSearch = labelSearch;
    currentValueSearch = valueSearch;
    
    loadMemoryBlocks(labelSearch, valueSearch);
}

// Edit memory block
async function editMemoryBlock(blockId, label) {
    try {
        const response = await fetch(`/dashboard/api/agents/${encodeURIComponent(currentAgentName)}/memory-blocks/${encodeURIComponent(blockId)}`, {
            credentials: 'same-origin'
        });
        
        const data = await response.json();
        
        if (data.success && data.block) {
            const block = data.block;
            // Store the identifier to use for updates
            // For shared blocks: must use block_id (UUID format)
            // For agent blocks: can use label or block_id
            // Priority: block_id > block_label > original blockId parameter
            const identifier = block.block_id || block.id || block.block_label || blockId;
            document.getElementById('editBlockId').value = identifier;
            document.getElementById('editBlockAgentName').value = currentAgentName;
            document.getElementById('editBlockLabel').value = block.label || block.block_label || label || '';
            document.getElementById('editBlockValue').value = block.value || '';
            document.getElementById('editBlockDescription').value = block.description || '';
            document.getElementById('editBlockLimit').value = block.limit || block.metadata?.limit || '';
            document.getElementById('editBlockReadOnly').checked = block.metadata?.read_only || false;
            document.getElementById('editBlockPreserveOnMigration').checked = block.metadata?.preserve_on_migration || false;
            
            updateCharCount('editBlockValue', 'editBlockValueCharCount');
            
            document.getElementById('editMemoryBlockModal').classList.remove('hidden');
        } else {
            showNotification(data.error || 'Failed to load memory block', 'error');
        }
    } catch (error) {
        console.error('Error loading memory block:', error);
        showNotification('Error loading memory block', 'error');
    }
}

// Hide edit memory block modal
function hideEditMemoryBlockModal() {
    document.getElementById('editMemoryBlockModal').classList.add('hidden');
    document.getElementById('editMemoryBlockForm').reset();
}

// Update memory block
async function updateMemoryBlock(event) {
    if (event) {
        event.preventDefault();
    }
    
    const blockId = document.getElementById('editBlockId').value;
    const agentName = document.getElementById('editBlockAgentName').value;
    const label = document.getElementById('editBlockLabel').value;
    const value = document.getElementById('editBlockValue').value;
    const description = document.getElementById('editBlockDescription').value;
    const limit = document.getElementById('editBlockLimit').value;
    const readOnly = document.getElementById('editBlockReadOnly').checked;
    const preserveOnMigration = document.getElementById('editBlockPreserveOnMigration').checked;
    
    const formData = new FormData();
    formData.append('value', value);
    if (description) formData.append('description', description);
    if (limit) formData.append('character_limit', limit);
    formData.append('read_only', readOnly);
    formData.append('preserve_on_migration', preserveOnMigration);
    
    try {
        const response = await fetch(`/dashboard/api/agents/${encodeURIComponent(agentName)}/memory-blocks/${encodeURIComponent(blockId)}`, {
            method: 'PUT',
            credentials: 'same-origin',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            showNotification('Memory block updated successfully');
            hideEditMemoryBlockModal();
            // Reload with the same search conditions that were active before editing
            loadMemoryBlocks(currentLabelSearch, currentValueSearch);
        } else {
            showNotification(data.error || 'Failed to update memory block', 'error');
        }
    } catch (error) {
        console.error('Error updating memory block:', error);
        showNotification('Error updating memory block', 'error');
    }
}

// Delete memory block
async function deleteMemoryBlock() {
    const blockId = document.getElementById('editBlockId').value;
    const agentName = document.getElementById('editBlockAgentName').value;
    
    if (!confirm('Are you sure you want to delete this memory block?')) {
        return;
    }
    
    try {
        const response = await fetch(`/dashboard/api/agents/${encodeURIComponent(agentName)}/memory-blocks/${encodeURIComponent(blockId)}`, {
            method: 'DELETE',
            credentials: 'same-origin'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showNotification('Memory block deleted successfully');
            hideEditMemoryBlockModal();
            // Reload with the same search conditions that were active before editing
            loadMemoryBlocks(currentLabelSearch, currentValueSearch);
        } else {
            showNotification(data.error || 'Failed to delete memory block', 'error');
        }
    } catch (error) {
        console.error('Error deleting memory block:', error);
        showNotification('Error deleting memory block', 'error');
    }
}

// Show create memory block modal
function showCreateMemoryBlockModal() {
    document.getElementById('createBlockAgentName').value = currentAgentName;
    document.getElementById('createMemoryBlockModal').classList.remove('hidden');
}

// Hide create memory block modal
function hideCreateMemoryBlockModal() {
    document.getElementById('createMemoryBlockModal').classList.add('hidden');
    document.getElementById('createMemoryBlockForm').reset();
    document.getElementById('createBlockValueCharCount').textContent = '0 Chars';
}

// Create memory block
async function createMemoryBlock() {
    const agentName = document.getElementById('createBlockAgentName').value;
    const label = document.getElementById('createBlockLabel').value;
    const value = document.getElementById('createBlockValue').value;
    const description = document.getElementById('createBlockDescription').value;
    const limit = document.getElementById('createBlockLimit').value;
    const readOnly = document.getElementById('createBlockReadOnly').checked;
    const preserveOnMigration = document.getElementById('createBlockPreserveOnMigration').checked;
    
    if (!label || !value) {
        showNotification('Label and value are required', 'error');
        return;
    }
    
    const formData = new FormData();
    formData.append('label', label);
    formData.append('value', value);
    if (description) formData.append('description', description);
    if (limit) formData.append('character_limit', limit);
    formData.append('read_only', readOnly);
    formData.append('preserve_on_migration', preserveOnMigration);
    
    try {
        const response = await fetch(`/dashboard/api/agents/${encodeURIComponent(agentName)}/memory-blocks`, {
            method: 'POST',
            credentials: 'same-origin',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            showNotification('Memory block created successfully');
            hideCreateMemoryBlockModal();
            // Reload with the same search conditions that were active before creating
            loadMemoryBlocks(currentLabelSearch, currentValueSearch);
        } else {
            showNotification(data.error || 'Failed to create memory block', 'error');
        }
    } catch (error) {
        console.error('Error creating memory block:', error);
        showNotification('Error creating memory block', 'error');
    }
}

// Update character count
function updateCharCount(textareaId, countId) {
    const textarea = document.getElementById(textareaId);
    const countEl = document.getElementById(countId);
    if (textarea && countEl) {
        textarea.addEventListener('input', function() {
            countEl.textContent = `${this.value.length} Chars`;
        });
        countEl.textContent = `${textarea.value.length} Chars`;
    }
}

// Copy to clipboard
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('Copied to clipboard');
    }).catch(err => {
        console.error('Failed to copy:', err);
    });
}

// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Initialize character count listeners on page load
document.addEventListener('DOMContentLoaded', function() {
    updateCharCount('editBlockValue', 'editBlockValueCharCount');
    updateCharCount('createBlockValue', 'createBlockValueCharCount');
});
