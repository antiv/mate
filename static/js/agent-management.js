// ============================================================================
// Agent Management Functions (Export, Import, Reinitialize)
// ============================================================================

// Export agents functionality
async function exportAgents() {
    try {
        if (!window.selectedProjectId) {
            showNotification('Select a project before exporting agents.', 'warning');
            return;
        }
        // Get current filter values
        const searchTerm = document.getElementById('agentSearch').value;
        const rootAgentFilter = document.getElementById('rootAgentFilter').value;
        
        // Build query parameters
        const params = new URLSearchParams();
        if (searchTerm) params.append('search', searchTerm);
        if (rootAgentFilter) params.append('root_agent', rootAgentFilter);
        params.append('project_id', window.selectedProjectId);
        
        const response = await fetch(`/dashboard/api/agents/export?${params.toString()}`, {
            method: 'GET',
            credentials: 'same-origin'
        });
        
        if (!response.ok) {
            throw new Error('Export failed');
        }
        
        const data = await response.json();
        
        // Create and download the file
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `agent-configs-${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        let exportMsg = `Exported ${data.export_info.total_agents} agent(s)`;
        if (data.memory_blocks && data.memory_blocks.length > 0) {
            exportMsg += ` and ${data.memory_blocks.length} memory block(s)`;
        }
        exportMsg += ' successfully';
        showNotification(exportMsg, 'success');
    } catch (error) {
        console.error('Export error:', error);
        showNotification('Export failed: ' + error.message, 'error');
    }
}

// Import agents functionality
async function importAgents() {
    const fileInput = document.getElementById('importFile');
    const overwrite = document.getElementById('overwriteExisting').checked;
    
    if (!fileInput.files[0]) {
        showNotification('Please select a file to import', 'error');
        return;
    }
    
    const submitBtn = document.getElementById('importSubmitBtn');
    const loader = document.getElementById('importLoader');
    const btnText = document.getElementById('importBtnText');
    
    submitBtn.disabled = true;
    loader.classList.remove('hidden');
    btnText.textContent = 'Importing...';
    
    try {
        const file = fileInput.files[0];
        const text = await file.text();
        const importData = JSON.parse(text);
        
        const response = await fetch(`/dashboard/api/agents/import?overwrite=${overwrite}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'same-origin',
            body: JSON.stringify(importData)
        });
        
        const result = await response.json();
        
        if (response.ok) {
            let message = `Import completed: ${result.imported_count} agents imported`;
            if (result.skipped_count > 0) {
                message += `, ${result.skipped_count} skipped`;
            }
            if (result.memory_blocks_imported > 0) {
                message += `, ${result.memory_blocks_imported} memory blocks imported`;
            }
            if (result.memory_blocks_skipped > 0) {
                message += `, ${result.memory_blocks_skipped} memory blocks skipped`;
            }
            if (result.errors.length > 0) {
                message += `, ${result.errors.length} errors`;
            }
            showNotification(message);
            hideImportModal();
            location.reload();
        } else {
            showNotification('Import failed: ' + result.detail, 'error');
        }
    } catch (error) {
        showNotification('Error importing agents: ' + error.message, 'error');
    } finally {
        submitBtn.disabled = false;
        loader.classList.add('hidden');
        btnText.textContent = 'Import Agents';
    }
}

// File input change handler
function handleFileSelect(event) {
    const file = event.target.files[0];
    const preview = document.getElementById('importPreview');
    const previewContent = document.getElementById('importPreviewContent');
    const submitBtn = document.getElementById('importSubmitBtn');
    
    if (file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            try {
                const data = JSON.parse(e.target.result);
                if (data.agents && Array.isArray(data.agents)) {
                    previewContent.innerHTML = `
                        <div class="text-green-600 dark:text-green-400">✓ Valid import file</div>
                        <div>Export timestamp: ${data.export_info?.timestamp || 'Unknown'}</div>
                        <div>Total agents: ${data.agents.length}</div>
                        <div class="mt-2">Agent names: ${data.agents.map(a => a.name).join(', ')}</div>
                    `;
                    preview.classList.remove('hidden');
                    submitBtn.disabled = false;
                } else {
                    previewContent.innerHTML = '<div class="text-red-600 dark:text-red-400">✗ Invalid file format</div>';
                    preview.classList.remove('hidden');
                    submitBtn.disabled = true;
                }
            } catch (error) {
                previewContent.innerHTML = '<div class="text-red-600 dark:text-red-400">✗ Invalid JSON file</div>';
                preview.classList.remove('hidden');
                submitBtn.disabled = true;
            }
        };
        reader.readAsText(file);
    } else {
        preview.classList.add('hidden');
        submitBtn.disabled = true;
    }
}

// Reinitialize a specific agent
async function reinitializeAgent(agentName) {
    const confirmed = await showConfirmDialog(
        `Reload agent "${agentName}" with fresh configuration?\n\nThis will clear all caches and force the agent to reinitialize on next request.`,
        'Reload Agent',
        'Reload',
        'Cancel',
        'info'
    );
    if (!confirmed) {
        return;
    }

    try {
        showNotification(`Reloading agent "${agentName}"...`, 'info');
        
        const response = await fetch(`/dashboard/api/agents/${agentName}/reinitialize`, {
            method: 'POST',
            credentials: 'same-origin'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('Agent cache cleared! It will reload on next request.', 'success');
        } else {
            showNotification(result.message || 'Failed to reload agent', 'error');
        }
    } catch (error) {
        console.error('Error reloading agent:', error);
        showNotification('Error reloading agent: ' + error.message, 'error');
    }
}

// Reinitialize all agents
async function reinitializeAllAgents() {
    const confirmed = await showConfirmDialog(
        'Reload ALL agents with fresh configuration?\n\nThis will clear all caches and force agents to reinitialize on next request.',
        'Reload All Agents',
        'Reload',
        'Cancel',
        'warning'
    );
    if (!confirmed) {
        return;
    }

    try {
        showNotification('Reloading all agents...', 'info');
        
        const response = await fetch('/dashboard/api/agents/reinitialize-all', {
            method: 'POST',
            credentials: 'same-origin'
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('All agent caches cleared! Agents will reload on next request.', 'success');
        } else {
            showNotification(result.message || 'Failed to reload agents', 'error');
        }
    } catch (error) {
        console.error('Error reloading agents:', error);
        showNotification('Error reloading agents: ' + error.message, 'error');
    }
}

// Function to refresh the agents page
function refreshAgentsPage() {
    location.reload();
}

// Save as Template
function showSaveTemplateModal() {
    const projectId = window.selectedProjectId;
    const rootAgentFilter = document.getElementById('rootAgentFilter');
    const rootAgent = rootAgentFilter ? rootAgentFilter.value : '';
    if (!projectId) {
        showNotification('Select a project first', 'warning');
        return;
    }
    if (!rootAgent) {
        showNotification('Select a root agent (filter by hierarchy) first', 'warning');
        return;
    }
    document.getElementById('saveTemplateId').value = '';
    document.getElementById('saveTemplateName').value = '';
    document.getElementById('saveTemplateDescription').value = '';
    document.getElementById('saveTemplateCategory').value = 'custom';
    document.getElementById('saveTemplateModal').classList.remove('hidden');
}

function hideSaveTemplateModal() {
    document.getElementById('saveTemplateModal').classList.add('hidden');
}

async function submitSaveTemplate() {
    const templateId = document.getElementById('saveTemplateId').value.trim();
    if (!templateId) {
        showNotification('Template ID is required', 'error');
        return;
    }
    const projectId = window.selectedProjectId;
    const rootAgentFilter = document.getElementById('rootAgentFilter');
    const rootAgent = rootAgentFilter ? rootAgentFilter.value : '';
    if (!projectId || !rootAgent) {
        showNotification('Project and root agent selection lost. Please try again.', 'error');
        return;
    }
    const submitBtn = document.getElementById('saveTemplateSubmitBtn');
    const loader = document.getElementById('saveTemplateLoader');
    const btnText = document.getElementById('saveTemplateBtnText');
    submitBtn.disabled = true;
    loader.classList.remove('hidden');
    btnText.textContent = 'Saving...';
    try {
        const result = await apiCall('/dashboard/api/templates/create-from-agents', 'POST', {
            project_id: projectId,
            root_agent: rootAgent,
            template_id: templateId,
            template_name: document.getElementById('saveTemplateName').value.trim() || templateId,
            description: document.getElementById('saveTemplateDescription').value.trim(),
            category: document.getElementById('saveTemplateCategory').value || 'custom',
        });
        if (result.success) {
            showNotification(`Template saved: ${result.template_id}.json`, 'success');
            hideSaveTemplateModal();
            window.location.href = '/dashboard/templates';
        } else {
            showNotification(result.error || result.detail || 'Failed to save template', 'error');
        }
    } catch (error) {
        const msg = error?.detail || error?.message || 'Failed to save template';
        showNotification(typeof msg === 'string' ? msg : JSON.stringify(msg), 'error');
    } finally {
        submitBtn.disabled = false;
        loader.classList.add('hidden');
        btnText.textContent = 'Save Template';
    }
}

// Export functions to window for global access
window.exportAgents = exportAgents;
window.importAgents = importAgents;
window.handleFileSelect = handleFileSelect;
window.reinitializeAgent = reinitializeAgent;
window.reinitializeAllAgents = reinitializeAllAgents;
window.refreshAgentsPage = refreshAgentsPage;
window.showSaveTemplateModal = showSaveTemplateModal;
window.hideSaveTemplateModal = hideSaveTemplateModal;
window.submitSaveTemplate = submitSaveTemplate;

// Attach file input handler for import when DOM is ready
function attachAgentManagementHandlers() {
    const importFileInput = document.getElementById('importFile');
    if (importFileInput) {
        importFileInput.addEventListener('change', handleFileSelect);
    }
    const saveTemplateBtn = document.getElementById('saveTemplateBtn');
    if (saveTemplateBtn) {
        saveTemplateBtn.addEventListener('click', showSaveTemplateModal);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attachAgentManagementHandlers);
} else {
    attachAgentManagementHandlers();
}

