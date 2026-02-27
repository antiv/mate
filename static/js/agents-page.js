/**
 * Agent Page Initialization
 * Page-specific code that needs to run on DOMContentLoaded
 * Note: This file expects 'configs' variable to be defined by the template
 */

// Initialize the page when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    // Initialize search and filter
    initializeSearchAndFilter(configs);
    
    // Setup event listeners for content generation parameters
    setupContentGenerationListeners('agent');
    setupContentGenerationListeners('editAgent');
    setupContentGenerationListeners('copyAgent');
    
    // Setup event listeners for planner configuration
    setupPlannerListeners('agent');
    setupPlannerListeners('editAgent');
    setupPlannerListeners('copyAgent');
    
    // Setup event listeners for tool configuration
    setupToolListeners('agent');
    setupToolListeners('editAgent');
    setupToolListeners('copyAgent');

    if (typeof initializeConfigSummaryHandling === 'function') {
        initializeConfigSummaryHandling(['agent', 'editAgent', 'copyAgent']);
    }
    
    if (typeof window.selectedProjectId !== 'undefined' && window.selectedProjectId !== null) {
        const createProjectSelect = document.getElementById('agentProject');
        if (createProjectSelect) {
            createProjectSelect.value = String(window.selectedProjectId);
        }
    }
    
    // Setup max iterations field toggles
    const agentTypeSelect = document.getElementById('agentType');
    if (agentTypeSelect) {
        agentTypeSelect.addEventListener('change', function() {
            toggleMaxIterationsField(this, 'maxIterationsField');
        });
    }
    
    const editAgentTypeSelect = document.getElementById('editAgentType');
    if (editAgentTypeSelect) {
        editAgentTypeSelect.addEventListener('change', function() {
            toggleMaxIterationsField(this, 'editMaxIterationsField');
        });
    }
    
    const copyAgentTypeSelect = document.getElementById('copyAgentType');
    if (copyAgentTypeSelect) {
        copyAgentTypeSelect.addEventListener('change', function() {
            toggleMaxIterationsField(this, 'copyMaxIterationsField');
        });
    }
    
    // Initialize graph editor button state
    if (typeof updateGraphEditorButtonState === 'function') {
        updateGraphEditorButtonState();
    } else {
        // Fallback if function not yet loaded
        setTimeout(() => {
            if (typeof updateGraphEditorButtonState === 'function') {
                updateGraphEditorButtonState();
            }
        }, 100);
    }
});

