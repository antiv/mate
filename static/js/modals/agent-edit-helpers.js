// ============================================================================
// Agent Edit Modal Helper Functions
// ============================================================================

// Modal Functions
function openEditAgentModal(row) {
    // Prevent event bubbling from action buttons
    if (event && event.target && event.target.closest('button')) {
        return;
    }
    
    const agentName = row.dataset.name;
    
    // Find the config by name to get the numeric ID
    const config = configs.find(c => c.name === agentName);
    if (!config || !config.id) {
        console.error('Config not found for agent:', agentName);
        showNotification(`Error: Config not found for agent "${agentName}"`, 'error');
        return;
    }
    
    // Use editAgent function which properly handles ID
    // editAgent is exported from agent-forms.js as window.editAgent
    if (typeof window.editAgent === 'function') {
        window.editAgent(config);
    } else {
        console.error('editAgent function not available. Make sure agent-forms.js is loaded.');
        showNotification('Error: editAgent function not loaded. Please refresh the page.', 'error');
        return;
    }
    
    // Add active row styling
    document.querySelectorAll('.agent-row').forEach(r => r.classList.remove('bg-blue-50', 'dark:bg-blue-900'));
    row.classList.add('bg-blue-50', 'dark:bg-blue-900');
}

function populateEditModalToolConfiguration(row) {
    // Parse tool config from row data or use defaults
    const toolConfig = row.dataset.toolConfig ? JSON.parse(row.dataset.toolConfig) : {};
    
    // Set checkboxes based on tool config
    document.getElementById('editAgentGoogleDrive').checked = toolConfig.google_drive || false;
    document.getElementById('editAgentCvTools').checked = toolConfig.cv_tools || false;
    document.getElementById('editAgentImageTools').checked = toolConfig.image_tools || false;
    
    // Show/hide image model container
    const imageModelContainer = document.getElementById('editAgentImageModelContainer');
    if (toolConfig.image_tools) {
        imageModelContainer.classList.remove('hidden');
        document.getElementById('editAgentImageModel').value = toolConfig.image_tools.model || 'openrouter/google/gemini-2.5-flash-image';
    } else {
        imageModelContainer.classList.add('hidden');
    }
    
    // Set tool config JSON
    document.getElementById('editAgentToolConfig').value = JSON.stringify(toolConfig, null, 2);
    
    // Set MCP servers config
    const mcpConfig = row.dataset.mcpServersConfig ? JSON.parse(row.dataset.mcpServersConfig) : {};
    document.getElementById('editAgentMcpServersConfig').value = JSON.stringify(mcpConfig, null, 2);
}

function populateEditModalPlannerConfiguration(row) {
    // Parse planner config from row data or use defaults
    const plannerConfig = row.dataset.plannerConfig ? JSON.parse(row.dataset.plannerConfig) : {};
    
    // Set planner type
    document.getElementById('editAgentPlannerType').value = plannerConfig.type || '';
    
    // Set planner max iterations
    document.getElementById('editAgentPlannerMaxIterations').value = plannerConfig.max_iterations || 10;
    
    // Show/hide thinking mode container for BuiltInPlanner
    const thinkingModeContainer = document.getElementById('editAgentThinkingModeContainer');
    if (plannerConfig.type === 'BuiltInPlanner') {
        thinkingModeContainer.classList.remove('hidden');
        document.getElementById('editAgentThinkingMode').value = plannerConfig.thinking_mode || 'default';
    } else {
        thinkingModeContainer.classList.add('hidden');
    }
    
    // Set planner config JSON
    document.getElementById('editAgentPlannerConfig').value = JSON.stringify(plannerConfig, null, 2);
}

function populateEditModalContentConfiguration(row) {
    // Parse content config from row data or use defaults
    const contentConfig = row.dataset.generateContentConfig ? JSON.parse(row.dataset.generateContentConfig) : {};
    
    // Set temperature
    const temperature = contentConfig.temperature || 0.7;
    document.getElementById('editAgentTemperature').value = temperature;
    document.getElementById('editAgentTemperatureValue').value = temperature;
    
    // Set max output tokens
    document.getElementById('editAgentMaxOutputTokens').value = contentConfig.max_output_tokens || 8192;
    
    // Set Top P
    const topP = contentConfig.top_p || 0.95;
    document.getElementById('editAgentTopP').value = topP;
    document.getElementById('editAgentTopPValue').value = topP;
    
    // Set Top K
    document.getElementById('editAgentTopK').value = contentConfig.top_k || 40;
    
    // Set content config JSON
    document.getElementById('editAgentGenerateContentConfig').value = JSON.stringify(contentConfig, null, 2);
}

function populateEditModalSchemaConfiguration(row) {
    // Set input schema
    const inputSchema = row.dataset.inputSchema ? JSON.parse(row.dataset.inputSchema) : {};
    document.getElementById('editAgentInputSchema').value = JSON.stringify(inputSchema, null, 2);
    
    // Set output schema
    const outputSchema = row.dataset.outputSchema ? JSON.parse(row.dataset.outputSchema) : {};
    document.getElementById('editAgentOutputSchema').value = JSON.stringify(outputSchema, null, 2);
}

function populateEditModalOtherConfiguration(row) {
    // Set include contents
    document.getElementById('editAgentIncludeContents').value = row.dataset.includeContents || '';
    
    // Set disabled checkbox
    document.getElementById('editAgentDisabled').checked = row.dataset.disabled === 'true';
    
    // Set hardcoded checkbox
    document.getElementById('editAgentHardcoded').checked = row.dataset.hardcoded === 'true';
}

function setupPanelEventListeners() {
    // Temperature slider sync
    const temperatureSlider = document.getElementById('panelEditAgentTemperature');
    const temperatureValue = document.getElementById('panelEditAgentTemperatureValue');
    
    if (temperatureSlider && temperatureValue) {
        temperatureSlider.addEventListener('input', function() {
            temperatureValue.value = this.value;
        });
        
        temperatureValue.addEventListener('input', function() {
            temperatureSlider.value = this.value;
        });
    }
    
    // Top P slider sync
    const topPSlider = document.getElementById('panelEditAgentTopP');
    const topPValue = document.getElementById('panelEditAgentTopPValue');
    
    if (topPSlider && topPValue) {
        topPSlider.addEventListener('input', function() {
            topPValue.value = this.value;
        });
        
        topPValue.addEventListener('input', function() {
            topPSlider.value = this.value;
        });
    }
    
    // Image tools checkbox
    const imageToolsCheckbox = document.getElementById('panelEditAgentImageTools');
    const imageModelContainer = document.getElementById('panelEditAgentImageModelContainer');
    
    if (imageToolsCheckbox && imageModelContainer) {
        imageToolsCheckbox.addEventListener('change', function() {
            if (this.checked) {
                imageModelContainer.classList.remove('hidden');
            } else {
                imageModelContainer.classList.add('hidden');
            }
        });
    }
    
    // Planner type change
    const plannerTypeSelect = document.getElementById('panelEditAgentPlannerType');
    const thinkingModeContainer = document.getElementById('panelEditAgentThinkingModeContainer');
    
    if (plannerTypeSelect && thinkingModeContainer) {
        plannerTypeSelect.addEventListener('change', function() {
            if (this.value === 'BuiltInPlanner') {
                thinkingModeContainer.classList.remove('hidden');
            } else {
                thinkingModeContainer.classList.add('hidden');
            }
        });
    }
    
    // Agent type change for max iterations
    const agentTypeSelect = document.getElementById('panelEditAgentType');
    const maxIterationsField = document.getElementById('panelEditAgentMaxIterationsField');
    
    if (agentTypeSelect && maxIterationsField) {
        agentTypeSelect.addEventListener('change', function() {
            if (this.value === 'loop') {
                maxIterationsField.classList.remove('hidden');
            } else {
                maxIterationsField.classList.add('hidden');
            }
        });
    }
}

// Export functions to window for global access
window.openEditAgentModal = openEditAgentModal;
window.populateEditModalToolConfiguration = populateEditModalToolConfiguration;
window.populateEditModalPlannerConfiguration = populateEditModalPlannerConfiguration;
window.populateEditModalContentConfiguration = populateEditModalContentConfiguration;
window.populateEditModalSchemaConfiguration = populateEditModalSchemaConfiguration;
window.populateEditModalOtherConfiguration = populateEditModalOtherConfiguration;
window.setupPanelEventListeners = setupPanelEventListeners;

