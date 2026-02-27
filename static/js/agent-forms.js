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
// Helper Functions for ID-based Operations
// ============================================================================

/**
 * Find config by ID from the global configs array
 */
function findConfigById(id) {
    if (typeof configs === 'undefined') {
        console.error('configs array not available');
        return null;
    }
    // Use loose comparison to handle string/number type mismatches
    const config = configs.find(config => config.id == id);
    if (!config) {
        console.warn('Config not found for ID:', id, 'Type:', typeof id, 'Available configs:', configs.map(c => ({ id: c.id, name: c.name, idType: typeof c.id })));
    }
    return config;
}

/**
 * Normalize various truthy/falsy representations to a strict boolean
 */
function normalizeBoolean(value) {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value === 1;
    if (typeof value === 'string') {
        const v = value.trim().toLowerCase();
        return v === '1' || v === 'true' || v === 'yes' || v === 'on';
    }
    return false;
}

// ============================================================================
// Config Summary & Modal Helpers
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
 * Open memory blocks modal from edit agent modal
 */
function openMemoryBlocksFromEditModal(prefix) {
    // Get agent name from the edit form
    const agentNameEl = document.getElementById(prefix + 'Name');
    if (!agentNameEl || !agentNameEl.value) {
        showNotification('Please enter an agent name first', 'error');
        return;
    }
    
    const agentName = agentNameEl.value;
    
    // Close edit modal if open (optional, can keep it open)
    // hideEditAgentModal();
    
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

function updateConfigSummaries(prefix) {
    updateToolConfigSummary(prefix);
    updateMcpServersConfigSummary(prefix);
    updatePlannerConfigSummary(prefix);
    updateGenerateContentSummary(prefix);
    updateSchemaSummary(prefix, 'InputSchema');
    updateSchemaSummary(prefix, 'OutputSchema');
    
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
    const ids = ['GoogleDrive', 'CvTools', 'ImageTools', 'MemoryBlocks', 'CreateAgent'];
    ids.forEach(id => {
        const checkbox = document.getElementById(prefix + id);
        if (checkbox) {
            checkbox.checked = false;
        }
    });

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
 * Edit agent by ID
 */
function editAgentById(id) {
    console.log('editAgentById called with ID:', id, 'Type:', typeof id);
    const config = findConfigById(id);
    if (config) {
        console.log('Found config:', { id: config.id, name: config.name, idType: typeof config.id });
        editAgent(config);
    } else {
        const availableIds = typeof configs !== 'undefined' 
            ? configs.map(c => `${c.id}(${typeof c.id})`).join(', ')
            : 'configs array not available';
        const errorMsg = `Agent config not found for ID: ${id} (type: ${typeof id}). Available IDs: ${availableIds}`;
        console.error(errorMsg);
        showAlert(errorMsg, 'error');
    }
}

/**
 * Copy agent by ID
 */
function copyAgentById(id) {
    const config = findConfigById(id);
    if (config) {
        copyAgent(config);
    } else {
        console.error('Agent config not found for ID:', id);
    }
}

// ============================================================================
// Form Population Functions
// ============================================================================

/**
 * Populate edit agent form with config data
 */
function editAgent(config) {
    // Helper to safely stringify objects/arrays for form fields
    const safeStringify = (value) => {
        if (value === null || value === undefined || value === '') {
            return '';
        }
        if (typeof value === 'object') {
            // Check if it's an empty object {} or empty array []
            if (Array.isArray(value) && value.length === 0) {
                return '';
            }
            if (!Array.isArray(value) && Object.keys(value).length === 0) {
                return '';
            }
            return JSON.stringify(value, null, 2);
        }
        if (typeof value === 'string') {
            // If it's already a string, check if it's valid JSON
            try {
                const parsed = JSON.parse(value);
                // Check if parsed result is empty object or array
                if (Array.isArray(parsed) && parsed.length === 0) {
                    return '';
                }
                if (typeof parsed === 'object' && !Array.isArray(parsed) && Object.keys(parsed).length === 0) {
                    return '';
                }
                // If it parses and not empty, re-stringify it with formatting
                return JSON.stringify(parsed, null, 2);
            } catch (e) {
                // If it doesn't parse, return as-is
                return value;
            }
        }
        return String(value);
    };
    
    // Store original hardcoded state for comparison during submit
    window.editAgentOriginalHardcoded = normalizeBoolean(config.hardcoded);
    
    // Validate that config.id exists and is a valid number
    // Check if id is undefined/null or if it's not a valid number when parsed
    const rawId = config.id;
    const parsedId = parseInt(rawId, 10);
    const isInvalid = rawId === undefined || rawId === null || rawId === '' || isNaN(parsedId);
    
    if (isInvalid) {
        console.error('Invalid agent ID in config:', {
            rawId: rawId,
            rawIdType: typeof rawId,
            parsedId: parsedId,
            isNaN: isNaN(parsedId),
            fullConfig: config
        });
        const errorMsg = `Error: Agent configuration has invalid ID. ID: "${rawId}" (type: ${typeof rawId}). Agent: "${config.name || 'unknown'}". Please refresh the page and try again.`;
        showAlert(errorMsg, 'error');
        return;
    }
    
    // Populate basic fields - use parsed ID to ensure it's normalized
    document.getElementById('editAgentId').value = parsedId;
    console.log('Setting editAgentId to:', parsedId, 'from raw:', rawId, 'type:', typeof rawId);
    document.getElementById('editAgentName').value = config.name;
    document.getElementById('editAgentType').value = config.type;
    document.getElementById('editAgentModel').value = config.model_name || '';
    document.getElementById('editAgentParents').value = safeStringify(config.parent_agents);
    document.getElementById('editAgentDescription').value = config.description || '';
    document.getElementById('editAgentInstruction').value = config.instruction || '';
    document.getElementById('editAgentRoles').value = safeStringify(config.allowed_for_roles);
    document.getElementById('editAgentToolConfig').value = safeStringify(config.tool_config);
    
    // Sync tool config to form controls
    syncJsonToToolConfig('editAgent');
    
    // Populate MCP servers configuration
    document.getElementById('editAgentMcpServersConfig').value = safeStringify(config.mcp_servers_config);
    
    // Populate planner configuration
    document.getElementById('editAgentPlannerConfig').value = safeStringify(config.planner_config);
    
    // Sync planner config to form controls
    syncJsonToPlannerConfig('editAgent');
    
    // Populate ADK configuration fields
    document.getElementById('editAgentGenerateContentConfig').value = safeStringify(config.generate_content_config);
    document.getElementById('editAgentInputSchema').value = safeStringify(config.input_schema);
    document.getElementById('editAgentOutputSchema').value = safeStringify(config.output_schema);
    document.getElementById('editAgentIncludeContents').value = config.include_contents || '';
    document.getElementById('editAgentMaxIterations').value = config.max_iterations || '';
    document.getElementById('editAgentDisabled').checked = normalizeBoolean(config.disabled);
    document.getElementById('editAgentHardcoded').checked = normalizeBoolean(config.hardcoded);
    const editProjectSelect = document.getElementById('editAgentProject');
    if (editProjectSelect) {
        const projectIdValue = (config.project_id !== undefined && config.project_id !== null)
            ? config.project_id
            : (config.project && config.project.id !== undefined ? config.project.id : '');
        editProjectSelect.value = projectIdValue !== '' ? String(projectIdValue) : '';
    }
    
    // Sync content generation config to form controls
    syncJsonToContentConfig('editAgent');
    updateConfigSummaries('editAgent');
    
    // Update memory blocks section visibility when editing
    const toolConfig = config.tool_config;
    let toolConfigObj = {};
    if (toolConfig) {
        try {
            toolConfigObj = typeof toolConfig === 'string' ? JSON.parse(toolConfig) : toolConfig;
        } catch (e) {
            toolConfigObj = {};
        }
    }
    if (typeof updateMemoryBlocksSectionVisibility === 'function') {
        updateMemoryBlocksSectionVisibility('editAgent', toolConfigObj);
    }
    
    // Show modal
    showEditAgentModal();
    
    // Re-setup tool listeners after modal is shown
    setTimeout(() => {
        setupToolListeners('editAgent');
    }, 50);
    
    // Populate Monaco editors if they exist
    setTimeout(() => {
        if (monacoEditors['editAgentParentsEditor']) {
            setJsonInEditor(monacoEditors['editAgentParentsEditor'], document.getElementById('editAgentParents').value);
        }
        if (monacoEditors['editAgentRolesEditor']) {
            setJsonInEditor(monacoEditors['editAgentRolesEditor'], document.getElementById('editAgentRoles').value);
        }
        if (monacoEditors['editAgentToolConfigEditor']) {
            setJsonInEditor(monacoEditors['editAgentToolConfigEditor'], document.getElementById('editAgentToolConfig').value);
        }
        if (monacoEditors['editAgentMcpServersConfigEditor']) {
            setJsonInEditor(monacoEditors['editAgentMcpServersConfigEditor'], document.getElementById('editAgentMcpServersConfig').value);
        }
        if (monacoEditors['editAgentPlannerConfigEditor']) {
            setJsonInEditor(monacoEditors['editAgentPlannerConfigEditor'], document.getElementById('editAgentPlannerConfig').value);
        }
        if (monacoEditors['editAgentGenerateContentConfigEditor']) {
            setJsonInEditor(monacoEditors['editAgentGenerateContentConfigEditor'], document.getElementById('editAgentGenerateContentConfig').value);
        }
        if (monacoEditors['editAgentInputSchemaEditor']) {
            setJsonInEditor(monacoEditors['editAgentInputSchemaEditor'], document.getElementById('editAgentInputSchema').value);
        }
        if (monacoEditors['editAgentOutputSchemaEditor']) {
            setJsonInEditor(monacoEditors['editAgentOutputSchemaEditor'], document.getElementById('editAgentOutputSchema').value);
        }
        if (monacoEditors['editAgentIncludeContentsEditor']) {
            setJsonInEditor(monacoEditors['editAgentIncludeContentsEditor'], document.getElementById('editAgentIncludeContents').value);
        }
        // Re-assert boolean checkboxes in case any other scripts toggled them
        const disabledEl = document.getElementById('editAgentDisabled');
        const hardcodedEl = document.getElementById('editAgentHardcoded');
        if (disabledEl) disabledEl.checked = normalizeBoolean(config.disabled);
        if (hardcodedEl) hardcodedEl.checked = normalizeBoolean(config.hardcoded);
    }, 100);
}

/**
 * Populate copy agent form with config data
 */
function copyAgent(config) {
    // Populate basic fields (excluding ID, name will be copied with suffix)
    document.getElementById('copyAgentName').value = config.name + '_copy';
    document.getElementById('copyAgentType').value = config.type;
    document.getElementById('copyAgentModel').value = config.model_name || '';
    document.getElementById('copyAgentParents').value = config.parent_agents ? JSON.stringify(config.parent_agents) : '';
    document.getElementById('copyAgentDescription').value = config.description || '';
    document.getElementById('copyAgentInstruction').value = config.instruction || '';
    document.getElementById('copyAgentRoles').value = config.allowed_for_roles || '';
    document.getElementById('copyAgentToolConfig').value = config.tool_config || '';
    
    // Sync tool config to form controls
    syncJsonToToolConfig('copyAgent');
    
    document.getElementById('copyAgentMcpServersConfig').value = config.mcp_servers_config || '';
    document.getElementById('copyAgentPlannerConfig').value = config.planner_config || '';
    
    // Sync planner config to form controls
    syncJsonToPlannerConfig('copyAgent');
    
    // Populate ADK configuration fields
    document.getElementById('copyAgentGenerateContentConfig').value = config.generate_content_config || '';
    document.getElementById('copyAgentInputSchema').value = config.input_schema || '';
    document.getElementById('copyAgentOutputSchema').value = config.output_schema || '';
    document.getElementById('copyAgentIncludeContents').value = config.include_contents || '';
    document.getElementById('copyAgentMaxIterations').value = config.max_iterations || '';
    document.getElementById('copyAgentDisabled').checked = normalizeBoolean(config.disabled);
    document.getElementById('copyAgentHardcoded').checked = normalizeBoolean(config.hardcoded);
    const copyProjectSelect = document.getElementById('copyAgentProject');
    if (copyProjectSelect) {
        const projectIdValue = (config.project_id !== undefined && config.project_id !== null)
            ? config.project_id
            : (config.project && config.project.id !== undefined ? config.project.id : '');
        copyProjectSelect.value = projectIdValue !== '' ? String(projectIdValue) : '';
    }
    
    // Sync content generation config to form controls
    syncJsonToContentConfig('copyAgent');
    updateConfigSummaries('copyAgent');
    
    // Show modal
    showCopyAgentModal();
    
    // Re-setup tool listeners after modal is shown
    setTimeout(() => {
        setupToolListeners('copyAgent');
    }, 50);
    
    // Populate Monaco editors if they exist
    setTimeout(() => {
        if (monacoEditors['copyAgentParentsEditor']) {
            setJsonInEditor(monacoEditors['copyAgentParentsEditor'], document.getElementById('copyAgentParents').value);
        }
        if (monacoEditors['copyAgentRolesEditor']) {
            setJsonInEditor(monacoEditors['copyAgentRolesEditor'], document.getElementById('copyAgentRoles').value);
        }
        if (monacoEditors['copyAgentToolConfigEditor']) {
            setJsonInEditor(monacoEditors['copyAgentToolConfigEditor'], document.getElementById('copyAgentToolConfig').value);
        }
        if (monacoEditors['copyAgentMcpServersConfigEditor']) {
            setJsonInEditor(monacoEditors['copyAgentMcpServersConfigEditor'], document.getElementById('copyAgentMcpServersConfig').value);
        }
        if (monacoEditors['copyAgentPlannerConfigEditor']) {
            setJsonInEditor(monacoEditors['copyAgentPlannerConfigEditor'], document.getElementById('copyAgentPlannerConfig').value);
        }
        if (monacoEditors['copyAgentGenerateContentConfigEditor']) {
            setJsonInEditor(monacoEditors['copyAgentGenerateContentConfigEditor'], document.getElementById('copyAgentGenerateContentConfig').value);
        }
        if (monacoEditors['copyAgentInputSchemaEditor']) {
            setJsonInEditor(monacoEditors['copyAgentInputSchemaEditor'], document.getElementById('copyAgentInputSchema').value);
        }
        if (monacoEditors['copyAgentOutputSchemaEditor']) {
            setJsonInEditor(monacoEditors['copyAgentOutputSchemaEditor'], document.getElementById('copyAgentOutputSchema').value);
        }
        if (monacoEditors['copyAgentIncludeContentsEditor']) {
            setJsonInEditor(monacoEditors['copyAgentIncludeContentsEditor'], document.getElementById('copyAgentIncludeContents').value);
        }
    }, 100);
}


/**
 * Delete agent with confirmation
 */
async function deleteAgent(id, name, isHardcoded) {
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
                setTimeout(() => location.reload(), 600);
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
            console.log('Parsed agents:', agents);
            // The actual import will be handled by form submission
        } catch (error) {
            showAlert('Invalid JSON file', 'error');
            console.error('Error parsing JSON:', error);
        }
    };
    reader.readAsText(file);
}

// Make functions available globally for graph editor
window.editAgent = editAgent;
window.editAgentById = editAgentById;
window.copyAgent = copyAgent;
window.openConfigModal = openConfigModal;
window.closeConfigModal = closeConfigModal;
window.applyConfigModal = applyConfigModal;
window.resetToolConfig = resetToolConfig;
window.resetPlannerConfig = resetPlannerConfig;
window.resetContentConfig = resetContentConfig;
window.clearJsonField = clearJsonField;
window.initializeConfigSummaryHandling = initializeConfigSummaryHandling;
window.updateConfigSummaries = updateConfigSummaries;
window.updateMemoryBlocksSectionVisibility = updateMemoryBlocksSectionVisibility;
window.openMemoryBlocksFromEditModal = openMemoryBlocksFromEditModal;

