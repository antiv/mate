/**
 * The Agents list: search, filter and the graph editor button.
 * An agent is edited on its own page (agent-page.js).
 * Note: This file expects 'configs' variable to be defined by the template
 */

document.addEventListener('DOMContentLoaded', function() {
    initializeSearchAndFilter(configs);

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
