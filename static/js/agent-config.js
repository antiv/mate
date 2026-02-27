/**
 * Agent Configuration Module
 * Handles planner configuration and content generation settings
 */

// ============================================================================
// Content Generation Configuration
// ============================================================================

/**
 * Sync content generation form controls to JSON
 */
function syncContentConfigToJson(prefix = '') {
    const temperature = document.getElementById(prefix + 'Temperature');
    const maxOutputTokens = document.getElementById(prefix + 'MaxOutputTokens');
    const topP = document.getElementById(prefix + 'TopP');
    const topK = document.getElementById(prefix + 'TopK');
    
    const config = {};
    
    if (temperature && temperature.value) {
        config.temperature = parseFloat(temperature.value);
    }
    if (maxOutputTokens && maxOutputTokens.value) {
        config.max_output_tokens = parseInt(maxOutputTokens.value);
    }
    if (topP && topP.value) {
        config.top_p = parseFloat(topP.value);
    }
    if (topK && topK.value) {
        config.top_k = parseInt(topK.value);
    }
    
    const textarea = document.getElementById(prefix + 'GenerateContentConfig');
    textarea.value = JSON.stringify(config, null, 2);
    if (typeof updateGenerateContentSummary === 'function') {
        updateGenerateContentSummary(prefix);
    }
}

/**
 * Sync JSON textarea to content generation form controls
 */
function syncJsonToContentConfig(prefix = '') {
    const textarea = document.getElementById(prefix + 'GenerateContentConfig');
    try {
        const config = JSON.parse(textarea.value || '{}');
        
        if (config.temperature !== undefined) {
            document.getElementById(prefix + 'Temperature').value = config.temperature;
            document.getElementById(prefix + 'TemperatureValue').value = config.temperature;
        }
        if (config.max_output_tokens !== undefined) {
            document.getElementById(prefix + 'MaxOutputTokens').value = config.max_output_tokens;
        }
        if (config.top_p !== undefined) {
            document.getElementById(prefix + 'TopP').value = config.top_p;
            document.getElementById(prefix + 'TopPValue').value = config.top_p;
        }
        if (config.top_k !== undefined) {
            document.getElementById(prefix + 'TopK').value = config.top_k;
        }
    } catch (e) {
        // Invalid JSON, ignore
    }
    if (typeof updateGenerateContentSummary === 'function') {
        updateGenerateContentSummary(prefix);
    }
}

/**
 * Setup event listeners for content generation parameters
 */
function setupContentGenerationListeners(prefix) {
    // Temperature slider and input sync
    const temperatureSlider = document.getElementById(prefix + 'Temperature');
    const temperatureInput = document.getElementById(prefix + 'TemperatureValue');
    
    if (temperatureSlider && temperatureInput) {
        temperatureSlider.addEventListener('input', function() {
            temperatureInput.value = this.value;
            syncContentConfigToJson(prefix);
        });
        
        temperatureInput.addEventListener('input', function() {
            temperatureSlider.value = this.value;
            syncContentConfigToJson(prefix);
        });
    }
    
    // Top P slider and input sync
    const topPSlider = document.getElementById(prefix + 'TopP');
    const topPInput = document.getElementById(prefix + 'TopPValue');
    
    if (topPSlider && topPInput) {
        topPSlider.addEventListener('input', function() {
            topPInput.value = this.value;
            syncContentConfigToJson(prefix);
        });
        
        topPInput.addEventListener('input', function() {
            topPSlider.value = this.value;
            syncContentConfigToJson(prefix);
        });
    }
    
    // Max output tokens input
    const maxOutputTokensInput = document.getElementById(prefix + 'MaxOutputTokens');
    if (maxOutputTokensInput) {
        maxOutputTokensInput.addEventListener('input', function() {
            syncContentConfigToJson(prefix);
        });
    }
    
    // Top K input
    const topKInput = document.getElementById(prefix + 'TopK');
    if (topKInput) {
        topKInput.addEventListener('input', function() {
            syncContentConfigToJson(prefix);
        });
    }

    // JSON textarea sync
    const temperatureTextarea = document.getElementById(prefix + 'GenerateContentConfig');
    if (temperatureTextarea) {
        temperatureTextarea.addEventListener('input', function() {
            syncJsonToContentConfig(prefix);
        });
    }
}

// ============================================================================
// Planner Configuration
// ============================================================================

/**
 * Sync planner form controls to JSON
 */
function syncPlannerConfigToJson(prefix = '') {
    const plannerType = document.getElementById(prefix + 'PlannerType').value;
    const thinkingMode = document.getElementById(prefix + 'ThinkingMode').value;
    const maxIterations = document.getElementById(prefix + 'PlannerMaxIterations').value;
    
    let config = {};
    
    if (plannerType) {
        config.type = plannerType;
        
        if (plannerType === 'BuiltInPlanner' && thinkingMode && thinkingMode !== 'default') {
            config.thinking_mode = thinkingMode;
        }
        
        if (maxIterations && maxIterations !== '10') {
            config.max_iterations = parseInt(maxIterations);
        }
    }
    
    const textarea = document.getElementById(prefix + 'PlannerConfig');
    textarea.value = JSON.stringify(config, null, 2);
    if (typeof updatePlannerConfigSummary === 'function') {
        updatePlannerConfigSummary(prefix);
    }
}

/**
 * Sync JSON textarea to planner form controls
 */
function syncJsonToPlannerConfig(prefix = '') {
    const textarea = document.getElementById(prefix + 'PlannerConfig');
    try {
        const config = JSON.parse(textarea.value || '{}');
        
        if (config.type) {
            document.getElementById(prefix + 'PlannerType').value = config.type;
            
            // Show/hide thinking mode based on planner type
            const thinkingContainer = document.getElementById(prefix + 'ThinkingModeContainer');
            if (config.type === 'BuiltInPlanner') {
                thinkingContainer.style.display = 'block';
                if (config.thinking_mode) {
                    document.getElementById(prefix + 'ThinkingMode').value = config.thinking_mode;
                }
            } else {
                thinkingContainer.style.display = 'none';
            }
        }
        
        if (config.max_iterations !== undefined) {
            document.getElementById(prefix + 'PlannerMaxIterations').value = config.max_iterations;
        }
    } catch (e) {
        // Invalid JSON, ignore
    }
    if (typeof updatePlannerConfigSummary === 'function') {
        updatePlannerConfigSummary(prefix);
    }
}

/**
 * Handle planner type change
 */
function handlePlannerTypeChange(prefix) {
    const plannerType = document.getElementById(prefix + 'PlannerType').value;
    const thinkingContainer = document.getElementById(prefix + 'ThinkingModeContainer');
    
    if (plannerType === 'BuiltInPlanner') {
        thinkingContainer.style.display = 'block';
    } else {
        thinkingContainer.style.display = 'none';
    }
    
    syncPlannerConfigToJson(prefix);
}

/**
 * Setup event listeners for planner configuration
 */
function setupPlannerListeners(prefix) {
    // Planner type dropdown
    const plannerTypeSelect = document.getElementById(prefix + 'PlannerType');
    if (plannerTypeSelect) {
        plannerTypeSelect.addEventListener('change', function() {
            handlePlannerTypeChange(prefix);
        });
    }

    // Thinking mode dropdown
    const thinkingModeSelect = document.getElementById(prefix + 'ThinkingMode');
    if (thinkingModeSelect) {
        thinkingModeSelect.addEventListener('change', function() {
            syncPlannerConfigToJson(prefix);
        });
    }

    // Max iterations input
    const maxIterationsInput = document.getElementById(prefix + 'PlannerMaxIterations');
    if (maxIterationsInput) {
        maxIterationsInput.addEventListener('input', function() {
            syncPlannerConfigToJson(prefix);
        });
    }

    // JSON textarea sync
    const plannerTextarea = document.getElementById(prefix + 'PlannerConfig');
    if (plannerTextarea) {
        plannerTextarea.addEventListener('input', function() {
            syncJsonToPlannerConfig(prefix);
        });
    }
}

