/**
 * Agent Forms Module
 * Handles CRUD operations for agents (Create, Read, Update, Delete, Copy, Import, Export)
 * 
 * NOTE: Form submission handlers are kept in the main HTML file to access template variables.
 * This file contains reusable functions for populating and manipulating forms.
 */

// ============================================================================
// Custom Alert/Confirm Helpers (replaces browser alerts)
// ============================================================================

/**
 * Show a notification toast (replaces alert)
 */
// Handlers this file's markup calls through data-click and friends (csp-actions.js)
mateActions.allow('applyConfigModal', 'clearJsonField', 'closeConfigModal', 'exportAgents',
                  'openConfigModal', 'openMemoryBlocksFromEditModal', 'resetContentConfig',
                  'resetPlannerConfig', 'resetToolConfig');

function showAlert(message, type = 'info') {
    if (typeof showNotification === 'function') {
        showNotification(message, type);
    } else {
        // Fallback to browser alert if showNotification not available
        alert(message);
    }
}

/**
 * Show a confirm dialog (replaces confirm)
 * Returns a Promise that resolves to true/false
 */
async function showConfirmDialog(message, title = 'Confirm', confirmText = 'Confirm', cancelText = 'Cancel', type = 'warning') {
    if (typeof showConfirm === 'function') {
        return await showConfirm(message, title, confirmText, cancelText, type);
    } else {
        // Fallback to browser confirm if showConfirm not available
        return confirm(message);
    }
}

// ============================================================================
// Summaries of the configuration fields
// ============================================================================

function safeJsonParse(value) {
    if (!value || typeof value !== 'string') {
        return null;
    }
    const trimmed = value.trim();
    if (!trimmed) {
        return null;
    }
    try {
        return JSON.parse(trimmed);
    } catch (error) {
        return null;
    }
}

function truncateText(text, maxLength = 160) {
    if (!text) {
        return '';
    }
    return text.length > maxLength ? text.slice(0, maxLength - 1) + '…' : text;
}

function updateSummaryElement(element, preview, fallback) {
    if (!element) return;
    const emptyText = fallback || element.dataset.emptyText || '';
    if (!preview) {
        element.textContent = emptyText;
        element.classList.add('text-gray-400', 'dark:text-gray-500');
    } else {
        element.textContent = preview;
        element.classList.remove('text-gray-400', 'dark:text-gray-500');
    }
}

function updateToolConfigSummary(prefix) {
    const textarea = document.getElementById(prefix + 'ToolConfig');
    const summaryEl = document.getElementById(prefix + 'ToolConfigSummary');
    if (!textarea || !summaryEl) return;

    const parsed = safeJsonParse(textarea.value);
    if (!parsed || (typeof parsed === 'object' && Object.keys(parsed).length === 0)) {
        updateSummaryElement(summaryEl, '', summaryEl.dataset.emptyText);
        // Hide memory blocks section if no tools configured
        updateMemoryBlocksSectionVisibility(prefix, {});
        return;
    }

    const tools = [];
    if (parsed.google_drive) tools.push('google_drive');
    if (parsed.cv_tools) tools.push('cv_tools');
    if (parsed.image_tools) {
        if (typeof parsed.image_tools === 'object' && parsed.image_tools.model) {
            tools.push(`image_tools(${parsed.image_tools.model})`);
        } else {
            tools.push('image_tools');
        }
    }
    if (parsed.memory_blocks) {
        tools.push('memory_blocks');
    }
    if (parsed.code_executor) {
        tools.push('code_executor');
    }
    if (parsed.image_data_extraction) {
        if (typeof parsed.image_data_extraction === 'object' && parsed.image_data_extraction.model) {
            tools.push(`image_data_extraction(${parsed.image_data_extraction.model})`);
        } else {
            tools.push('image_data_extraction');
        }
    }

    let preview = '';
    if (tools.length) {
        preview = `Tools: ${tools.join(', ')}`;
    } else if (typeof parsed === 'object') {
        const keys = Object.keys(parsed);
        preview = keys.length ? `Custom keys: ${keys.join(', ')}` : '';
    }

    if (!preview) {
        preview = truncateText(textarea.value.trim());
    }

    updateSummaryElement(summaryEl, preview, summaryEl.dataset.emptyText);
    
    // Update memory blocks section visibility
    updateMemoryBlocksSectionVisibility(prefix, parsed);
}

/**
 * Update memory blocks section visibility based on tool configuration
 */
function updateMemoryBlocksSectionVisibility(prefix, toolConfig) {
    const memorySection = document.getElementById(prefix + 'MemoryBlocksSection');
    if (!memorySection) return;
    
    const hasMemoryBlocks = toolConfig.memory_blocks === true || 
                            (toolConfig.memory_blocks && typeof toolConfig.memory_blocks === 'object' && 
                             toolConfig.memory_blocks.enabled !== false);
    
    if (hasMemoryBlocks) {
        memorySection.classList.remove('hidden');
    } else {
        memorySection.classList.add('hidden');
    }
}

/**
 * Open the memory blocks dialog of the agent a form edits
 */
function openMemoryBlocksFromEditModal(prefix) {
    // Get agent name from the form
    const agentNameEl = document.getElementById(prefix + 'Name');
    if (!agentNameEl || !agentNameEl.value) {
        showNotification('Please enter an agent name first', 'error');
        return;
    }
    
    const agentName = agentNameEl.value;

    // Open memory blocks modal
    if (typeof showMemoryBlocksModal === 'function') {
        showMemoryBlocksModal(agentName);
    } else {
        console.error('showMemoryBlocksModal function not available');
        showNotification('Memory blocks modal not available', 'error');
    }
}

function updateMcpServersConfigSummary(prefix) {
    const textarea = document.getElementById(prefix + 'McpServersConfig');
    const summaryEl = document.getElementById(prefix + 'McpServersConfigSummary');
    if (!textarea || !summaryEl) return;

    const parsed = safeJsonParse(textarea.value);
    if (!parsed || typeof parsed !== 'object' || Object.keys(parsed).length === 0) {
        updateSummaryElement(summaryEl, '', summaryEl.dataset.emptyText);
        return;
    }

    const servers = parsed.mcpServers || parsed.servers || parsed;
    if (servers && typeof servers === 'object') {
        const names = Object.keys(servers);
        if (names.length) {
            const prefixText = names.length === 1 ? 'Server' : `${names.length} servers`;
            updateSummaryElement(summaryEl, `${prefixText}: ${names.slice(0, 4).join(', ')}${names.length > 4 ? '…' : ''}`, summaryEl.dataset.emptyText);
            return;
        }
    }
    updateSummaryElement(summaryEl, truncateText(textarea.value.trim()), summaryEl.dataset.emptyText);
}

function updatePlannerConfigSummary(prefix) {
    const textarea = document.getElementById(prefix + 'PlannerConfig');
    const summaryEl = document.getElementById(prefix + 'PlannerConfigSummary');
    if (!textarea || !summaryEl) return;

    const parsed = safeJsonParse(textarea.value);
    if (!parsed || typeof parsed !== 'object' || Object.keys(parsed).length === 0) {
        updateSummaryElement(summaryEl, '', summaryEl.dataset.emptyText);
        return;
    }

    const parts = [];
    if (parsed.type) parts.push(`type=${parsed.type}`);
    if (parsed.thinking_mode) parts.push(`thinking=${parsed.thinking_mode}`);
    if (parsed.max_iterations !== undefined) parts.push(`max_iter=${parsed.max_iterations}`);
    updateSummaryElement(summaryEl, parts.join(' · ') || truncateText(textarea.value.trim()), summaryEl.dataset.emptyText);
}

function updateGenerateContentSummary(prefix) {
    const textarea = document.getElementById(prefix + 'GenerateContentConfig');
    const summaryEl = document.getElementById(prefix + 'GenerateContentConfigSummary');
    if (!textarea || !summaryEl) return;

    const parsed = safeJsonParse(textarea.value);
    if (!parsed || typeof parsed !== 'object' || Object.keys(parsed).length === 0) {
        updateSummaryElement(summaryEl, '', summaryEl.dataset.emptyText);
        return;
    }

    const keys = ['temperature', 'max_output_tokens', 'top_p', 'top_k', 'presence_penalty'];
    const parts = keys.filter(key => parsed[key] !== undefined).map(key => `${key}=${parsed[key]}`);
    updateSummaryElement(summaryEl, parts.join(' · ') || truncateText(textarea.value.trim()), summaryEl.dataset.emptyText);
}

function updateSchemaSummary(prefix, suffix) {
    const textarea = document.getElementById(prefix + suffix);
    const summaryEl = document.getElementById(prefix + suffix + 'Summary');
    if (!textarea || !summaryEl) return;

    const parsed = safeJsonParse(textarea.value);
    if (!parsed || typeof parsed !== 'object' || Object.keys(parsed).length === 0) {
        updateSummaryElement(summaryEl, '', summaryEl.dataset.emptyText);
        return;
    }

    const parts = [];
    if (parsed.type) parts.push(`type=${parsed.type}`);
    if (parsed.properties && typeof parsed.properties === 'object') {
        const propertyNames = Object.keys(parsed.properties);
        if (propertyNames.length) {
            const label = propertyNames.length === 1 ? 'property' : 'properties';
            parts.push(`${label}: ${propertyNames.slice(0, 4).join(', ')}${propertyNames.length > 4 ? '…' : ''}`);
        }
    }
    updateSummaryElement(summaryEl, parts.join(' · ') || truncateText(textarea.value.trim()), summaryEl.dataset.emptyText);
}

function updateGuardrailConfigSummary(prefix) {
    const textarea = document.getElementById(prefix + 'GuardrailConfig');
    const summaryEl = document.getElementById(prefix + 'GuardrailConfigSummary');
    if (!textarea || !summaryEl) return;
    const parsed = safeJsonParse(textarea.value);
    if (!parsed || !parsed.guardrails || !Array.isArray(parsed.guardrails) || parsed.guardrails.length === 0) {
        updateSummaryElement(summaryEl, '', summaryEl.dataset.emptyText);
        return;
    }
    const enabled = parsed.guardrails.filter(g => g.enabled);
    if (enabled.length === 0) {
        updateSummaryElement(summaryEl, 'All guardrails disabled', summaryEl.dataset.emptyText);
        return;
    }
    const parts = enabled.map(g => `${g.type} (${g.action || 'log'})`);
    updateSummaryElement(summaryEl, parts.join(' · '), summaryEl.dataset.emptyText);
}

function syncJsonToGuardrailConfig(prefix) {
    const textarea = document.getElementById(prefix + 'GuardrailConfig');
    if (!textarea) return;
    const parsed = safeJsonParse(textarea.value);
    const guardrails = (parsed && parsed.guardrails && Array.isArray(parsed.guardrails)) ? parsed.guardrails : [];
    const _set = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
    const _setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = (val !== undefined && val !== null) ? val : ''; };
    if (guardrails.length === 0) {
        _set(prefix + 'GrPii', false);
        _set(prefix + 'GrInjection', false);
        _set(prefix + 'GrContentPolicy', false);
        _set(prefix + 'GrOutputLength', false);
        _set(prefix + 'GrHallucination', false);
        _setVal(prefix + 'GrHallucinationModel', '');
        _setVal(prefix + 'GrHallucinationThreshold', 0.7);
    }
    for (const g of guardrails) {
        switch (g.type) {
            case 'pii_detection':
                _set(prefix + 'GrPii', g.enabled);
                _setVal(prefix + 'GrPiiAction', g.action);
                break;
            case 'prompt_injection':
                _set(prefix + 'GrInjection', g.enabled);
                _setVal(prefix + 'GrInjectionAction', g.action);
                _setVal(prefix + 'GrInjectionSensitivity', (g.config || {}).sensitivity || 'medium');
                break;
            case 'content_policy':
                _set(prefix + 'GrContentPolicy', g.enabled);
                _setVal(prefix + 'GrContentPolicyAction', g.action);
                break;
            case 'output_length':
                _set(prefix + 'GrOutputLength', g.enabled);
                _setVal(prefix + 'GrOutputLengthAction', g.action);
                _setVal(prefix + 'GrMaxChars', (g.config || {}).max_characters || 10000);
                _setVal(prefix + 'GrMaxWords', (g.config || {}).max_words || 2000);
                break;
            case 'hallucination_check':
                _set(prefix + 'GrHallucination', g.enabled);
                _setVal(prefix + 'GrHallucinationAction', g.action);
                _setVal(prefix + 'GrHallucinationModel', (g.config || {}).model || '');
                _setVal(prefix + 'GrHallucinationThreshold', (g.config && g.config.threshold !== undefined) ? g.config.threshold : 0.7);
                break;
        }
    }
    updateGuardrailConfigSummary(prefix);
}

function buildGuardrailConfigFromPresets(prefix) {
    const _checked = (id) => { const el = document.getElementById(id); return el ? el.checked : false; };
    const _val = (id) => { const el = document.getElementById(id); return el ? el.value : ''; };
    const guardrails = [];
    guardrails.push({
        type: 'pii_detection', enabled: _checked(prefix + 'GrPii'),
        action: _val(prefix + 'GrPiiAction') || 'redact',
        config: { detect_email: true, detect_phone: true, detect_ssn: true, detect_credit_card: true, detect_ip_address: true }
    });
    guardrails.push({
        type: 'prompt_injection', enabled: _checked(prefix + 'GrInjection'),
        action: _val(prefix + 'GrInjectionAction') || 'block',
        config: { sensitivity: _val(prefix + 'GrInjectionSensitivity') || 'medium' }
    });
    guardrails.push({
        type: 'content_policy', enabled: _checked(prefix + 'GrContentPolicy'),
        action: _val(prefix + 'GrContentPolicyAction') || 'block',
        config: { blocklist: [], regex_patterns: [], case_sensitive: false }
    });
    const maxChars = parseInt(_val(prefix + 'GrMaxChars')) || 10000;
    const maxWords = parseInt(_val(prefix + 'GrMaxWords')) || 2000;
    guardrails.push({
        type: 'output_length', enabled: _checked(prefix + 'GrOutputLength'),
        action: _val(prefix + 'GrOutputLengthAction') || 'warn',
        config: { max_characters: maxChars, max_words: maxWords }
    });
    const hallucinationModel = _val(prefix + 'GrHallucinationModel').trim();
    const parsedThreshold = parseFloat(_val(prefix + 'GrHallucinationThreshold'));
    const hallucinationThreshold = isNaN(parsedThreshold) ? 0.7 : parsedThreshold;
    const hallucinationConfig = {};
    if (hallucinationModel) {
        hallucinationConfig.model = hallucinationModel;
    }
    hallucinationConfig.threshold = hallucinationThreshold;
    guardrails.push({
        type: 'hallucination_check', enabled: _checked(prefix + 'GrHallucination'),
        action: _val(prefix + 'GrHallucinationAction') || 'warn',
        config: hallucinationConfig
    });
    return { guardrails: guardrails };
}

function applyGuardrailConfig(prefix) {
    const config = buildGuardrailConfigFromPresets(prefix);
    const textarea = document.getElementById(prefix + 'GuardrailConfig');
    if (textarea) textarea.value = JSON.stringify(config, null, 2);
    if (monacoEditors && monacoEditors[prefix + 'GuardrailConfigEditor']) {
        setJsonInEditor(monacoEditors[prefix + 'GuardrailConfigEditor'], JSON.stringify(config, null, 2));
    }
    updateGuardrailConfigSummary(prefix);
    closeConfigModal(prefix, 'GuardrailConfig');
}

window.syncJsonToGuardrailConfig = syncJsonToGuardrailConfig;
window.updateGuardrailConfigSummary = updateGuardrailConfigSummary;
window.applyGuardrailConfig = applyGuardrailConfig;
window.buildGuardrailConfigFromPresets = buildGuardrailConfigFromPresets;

function updateConfigSummaries(prefix) {
    updateToolConfigSummary(prefix);
    updateMcpServersConfigSummary(prefix);
    updatePlannerConfigSummary(prefix);
    updateGenerateContentSummary(prefix);
    updateSchemaSummary(prefix, 'InputSchema');
    updateSchemaSummary(prefix, 'OutputSchema');
    updateGuardrailConfigSummary(prefix);
    
    // Update memory blocks section visibility based on current tool config
    const textarea = document.getElementById(prefix + 'ToolConfig');
    if (textarea) {
        try {
            const toolConfig = JSON.parse(textarea.value || '{}');
            if (typeof updateMemoryBlocksSectionVisibility === 'function') {
                updateMemoryBlocksSectionVisibility(prefix, toolConfig);
            }
        } catch (e) {
            // Invalid JSON, hide memory blocks section
            if (typeof updateMemoryBlocksSectionVisibility === 'function') {
                updateMemoryBlocksSectionVisibility(prefix, {});
            }
        }
    }
}

function attachSummaryListeners(prefix) {
    const mappings = [
        { field: 'ToolConfig', handler: () => updateToolConfigSummary(prefix) },
        { field: 'McpServersConfig', handler: () => updateMcpServersConfigSummary(prefix) },
        { field: 'PlannerConfig', handler: () => updatePlannerConfigSummary(prefix) },
        { field: 'GenerateContentConfig', handler: () => updateGenerateContentSummary(prefix) },
        { field: 'InputSchema', handler: () => updateSchemaSummary(prefix, 'InputSchema') },
        { field: 'OutputSchema', handler: () => updateSchemaSummary(prefix, 'OutputSchema') }
    ];

    mappings.forEach(({ field, handler }) => {
        const element = document.getElementById(prefix + field);
        if (element && !element.dataset.summaryListenerAttached) {
            element.addEventListener('input', handler);
            element.dataset.summaryListenerAttached = 'true';
        }
    });
}

function openConfigModal(prefix, fieldSuffix) {
    const modal = document.getElementById(`${prefix}${fieldSuffix}Modal`);
    if (!modal) return;
    if (fieldSuffix === 'GuardrailConfig' && typeof syncJsonToGuardrailConfig === 'function') {
        syncJsonToGuardrailConfig(prefix);
    }
    modal.classList.remove('hidden');
    document.body.classList.add('config-modal-open');
}

function closeConfigModal(prefix, fieldSuffix) {
    const modal = document.getElementById(`${prefix}${fieldSuffix}Modal`);
    if (!modal) return;
    modal.classList.add('hidden');
    // Remove the body class if no visible config modals remain
    const anyVisible = Array.from(document.querySelectorAll('[id$="ConfigModal"], [id$="SchemaModal"]'))
        .some(element => !element.classList.contains('hidden'));
    if (!anyVisible) {
        document.body.classList.remove('config-modal-open');
    }
}

function clearJsonField(prefix, fieldSuffix) {
    const field = document.getElementById(prefix + fieldSuffix);
    if (!field) return;
    field.value = '';
    switch (fieldSuffix) {
        case 'McpServersConfig':
            updateMcpServersConfigSummary(prefix);
            break;
        case 'InputSchema':
        case 'OutputSchema':
            updateSchemaSummary(prefix, fieldSuffix);
            break;
        default:
            const summaryEl = document.getElementById(prefix + fieldSuffix + 'Summary');
            if (summaryEl) {
                updateSummaryElement(summaryEl, '', summaryEl.dataset.emptyText);
            }
    }
}

function resetToolConfig(prefix) {
    const ids = ['GoogleDrive', 'CvTools', 'ImageTools', 'MemoryBlocks', 'CreateAgent', 'CodeExecutor', 'ImageDataExtraction', 'Shop', 'SubagentDelegation'];
    ids.forEach(id => {
        const checkbox = document.getElementById(prefix + id);
        if (checkbox) {
            checkbox.checked = false;
        }
    });
    const shopHint = document.getElementById(prefix + 'ShopHint');
    if (shopHint) shopHint.style.display = 'none';
    const saHint = document.getElementById(prefix + 'SubagentDelegationHint');
    if (saHint) saHint.style.display = 'none';
    const imageModelContainer = document.getElementById(prefix + 'ImageModelContainer');
    if (imageModelContainer) {
        imageModelContainer.style.display = 'none';
    }
    const imageModel = document.getElementById(prefix + 'ImageModel');
    if (imageModel) {
        imageModel.value = '';
    }

    const textarea = document.getElementById(prefix + 'ToolConfig');
    if (textarea) {
        textarea.value = '';
    }
    updateToolConfigSummary(prefix);
}

function resetPlannerConfig(prefix) {
    const plannerType = document.getElementById(prefix + 'PlannerType');
    const thinkingMode = document.getElementById(prefix + 'ThinkingMode');
    const plannerMaxIterations = document.getElementById(prefix + 'PlannerMaxIterations');
    const thinkingContainer = document.getElementById(prefix + 'ThinkingModeContainer');

    if (plannerType) plannerType.value = '';
    if (thinkingMode) thinkingMode.value = 'default';
    if (plannerMaxIterations) plannerMaxIterations.value = '';
    if (thinkingContainer) thinkingContainer.style.display = 'none';

    const textarea = document.getElementById(prefix + 'PlannerConfig');
    if (textarea) {
        textarea.value = '';
    }
    updatePlannerConfigSummary(prefix);
}

function resetContentConfig(prefix) {
    const fields = ['Temperature', 'TemperatureValue', 'MaxOutputTokens', 'TopP', 'TopPValue', 'TopK'];
    fields.forEach(id => {
        const field = document.getElementById(prefix + id);
        if (field) field.value = '';
    });

    const textarea = document.getElementById(prefix + 'GenerateContentConfig');
    if (textarea) {
        textarea.value = '';
    }
    updateGenerateContentSummary(prefix);
}

function applyConfigModal(prefix, fieldSuffix) {
    switch (fieldSuffix) {
        case 'ToolConfig':
            if (typeof syncToolConfigToJson === 'function') {
                syncToolConfigToJson(prefix);
            }
            updateToolConfigSummary(prefix);
            break;
        case 'PlannerConfig':
            if (typeof syncPlannerConfigToJson === 'function') {
                syncPlannerConfigToJson(prefix);
            }
            updatePlannerConfigSummary(prefix);
            break;
        case 'GenerateContentConfig':
            if (typeof syncContentConfigToJson === 'function') {
                syncContentConfigToJson(prefix);
            }
            updateGenerateContentSummary(prefix);
            break;
        case 'McpServersConfig':
            updateMcpServersConfigSummary(prefix);
            break;
        case 'InputSchema':
            updateSchemaSummary(prefix, 'InputSchema');
            break;
        case 'OutputSchema':
            updateSchemaSummary(prefix, 'OutputSchema');
            break;
        case 'GuardrailConfig':
            if (typeof buildGuardrailConfigFromPresets === 'function') {
                const config = buildGuardrailConfigFromPresets(prefix);
                const textarea = document.getElementById(prefix + 'GuardrailConfig');
                if (textarea) textarea.value = JSON.stringify(config, null, 2);
                if (typeof monacoEditors !== 'undefined' && monacoEditors[prefix + 'GuardrailConfigEditor']) {
                    setJsonInEditor(monacoEditors[prefix + 'GuardrailConfigEditor'], JSON.stringify(config, null, 2));
                }
                updateGuardrailConfigSummary(prefix);
            }
            break;
        default:
            break;
    }
    closeConfigModal(prefix, fieldSuffix);
}

function initializeConfigSummaryHandling(prefixes) {
    prefixes.forEach(prefix => {
        attachSummaryListeners(prefix);
        updateConfigSummaries(prefix);
    });
}


/**
 * Delete agent with confirmation; then onDeleted(), or a reload of the page
 */
async function deleteAgent(id, name, isHardcoded, onDeleted) {
    let deleteFolder = false;
    const basePrompt = `Are you sure you want to delete agent "${name}"?`;
    if (!isHardcoded) {
        // Ask if we should also delete the folder for non-hardcoded agents
        deleteFolder = await showConfirmDialog(
            basePrompt + `\n\nAlso delete its folder under agents/${name}?`,
            'Delete Agent',
            'Delete',
            'Cancel',
            'danger'
        );
        if (!deleteFolder) {
            return;
        }
    } else {
        const confirmed = await showConfirmDialog(
            basePrompt,
            'Delete Agent',
            'Delete',
            'Cancel',
            'danger'
        );
        if (!confirmed) {
            return;
        }
    }

    const url = `/dashboard/api/agents/${id}?delete_folder=${deleteFolder}`;
    fetch(url, { method: 'DELETE', credentials: 'same-origin' })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                if (data.folder_deleted) {
                    showNotification(`Agent and folder deleted (${data.folder_path})`, 'success');
                } else {
                    showNotification('Agent deleted successfully', 'success');
                }
                setTimeout(() => (onDeleted ? onDeleted() : location.reload()), 600);
            } else {
                showNotification(data.message || 'Failed to delete agent', 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showNotification('Error deleting agent', 'error');
        });
}

// ============================================================================
// Import/Export Functions
// ============================================================================

/**
 * Export agents to JSON file
 */
function exportAgents() {
    fetch('/dashboard/api/agents/export')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const dataStr = JSON.stringify(data.agents, null, 2);
                const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);
                
                const exportFileDefaultName = 'agents_export_' + new Date().toISOString().slice(0, 10) + '.json';
                
                const linkElement = document.createElement('a');
                linkElement.setAttribute('href', dataUri);
                linkElement.setAttribute('download', exportFileDefaultName);
                linkElement.click();
            } else {
                showAlert('Error exporting agents: ' + data.error, 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showAlert('Error exporting agents', 'error');
        });
}

/**
 * Import agents from JSON file
 * Note: The actual form submission is handled in the main HTML file
 */
function handleImportFile() {
    const fileInput = document.getElementById('importAgentsFile');
    const file = fileInput.files[0];
    
    if (!file) {
        showAlert('Please select a file to import', 'warning');
        return;
    }
    
    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const agents = JSON.parse(e.target.result);
            // The actual import will be handled by form submission
        } catch (error) {
            showAlert('Invalid JSON file', 'error');
            console.error('Error parsing JSON:', error);
        }
    };
    reader.readAsText(file);
}

function openInstructionConfigModal(prefix) {
    const modal = document.getElementById(`${prefix}InstructionModal`);
    if (!modal) return;
    
    const textarea = document.getElementById(`${prefix}Instruction`);
    const currentVal = textarea ? textarea.value : '';
    
    modal.classList.remove('hidden');
    document.body.classList.add('config-modal-open');
    
    const editorId = `${prefix}InstructionEditor`;
    
    if (!monacoEditors[editorId]) {
        initMonacoEditor(editorId, currentVal, 'markdown').then(editor => {
            monacoEditors[editorId] = editor;
        });
    } else {
        setJsonInEditor(monacoEditors[editorId], currentVal);
    }
}

function saveInstructionFromEditor(prefix) {
    const editorId = `${prefix}InstructionEditor`;
    const textarea = document.getElementById(`${prefix}Instruction`);
    if (monacoEditors[editorId] && textarea) {
        const val = monacoEditors[editorId].getValue();
        textarea.value = val;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }
    closeConfigModal(prefix, 'Instruction');
}

// Make functions available globally for graph editor
window.openConfigModal = openConfigModal;
window.closeConfigModal = closeConfigModal;
window.applyConfigModal = applyConfigModal;
window.applyGuardrailConfig = applyGuardrailConfig;
window.syncJsonToGuardrailConfig = syncJsonToGuardrailConfig;
window.buildGuardrailConfigFromPresets = buildGuardrailConfigFromPresets;
window.resetToolConfig = resetToolConfig;
window.resetPlannerConfig = resetPlannerConfig;
window.resetContentConfig = resetContentConfig;
window.clearJsonField = clearJsonField;
window.initializeConfigSummaryHandling = initializeConfigSummaryHandling;
window.updateConfigSummaries = updateConfigSummaries;
window.updateMemoryBlocksSectionVisibility = updateMemoryBlocksSectionVisibility;
window.openMemoryBlocksFromEditModal = openMemoryBlocksFromEditModal;
window.openInstructionConfigModal = openInstructionConfigModal;
window.saveInstructionFromEditor = saveInstructionFromEditor;

