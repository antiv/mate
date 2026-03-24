/**
 * Agent Tools Module
 * Handles tool configuration including image tools, Google Drive, CV tools
 */

/**
 * Sync tool configuration form controls to JSON
 */
function syncToolConfigToJson(prefix = '') {
    const config = {};
    
    // Check each tool checkbox
    const googleDrive = document.getElementById(prefix + 'GoogleDrive');
    const cvTools = document.getElementById(prefix + 'CvTools');
    const imageTools = document.getElementById(prefix + 'ImageTools');
    const imageModel = document.getElementById(prefix + 'ImageModel');
    const memoryBlocks = document.getElementById(prefix + 'MemoryBlocks');
    const createAgent = document.getElementById(prefix + 'CreateAgent');
    const codeExecutor = document.getElementById(prefix + 'CodeExecutor');
    const imageDataExtraction = document.getElementById(prefix + 'ImageDataExtraction');
    const imageDataExtractionModel = document.getElementById(prefix + 'ImageDataExtractionModel');
    
    if (googleDrive && googleDrive.checked) {
        config.google_drive = true;
    }
    if (cvTools && cvTools.checked) {
        config.cv_tools = true;
    }
    if (imageTools && imageTools.checked) {
        if (imageModel && imageModel.value) {
            config.image_tools = { model: imageModel.value };
        } else {
            config.image_tools = true;
        }
    }
    if (memoryBlocks && memoryBlocks.checked) {
        config.memory_blocks = true;
    }
    
    if (createAgent && createAgent.checked) {
        config.create_agent = true;
    }
    if (codeExecutor && codeExecutor.checked) {
        config.code_executor = true;
    }
    if (imageDataExtraction && imageDataExtraction.checked) {
        if (imageDataExtractionModel && imageDataExtractionModel.value) {
            config.image_data_extraction = { model: imageDataExtractionModel.value };
        } else {
            config.image_data_extraction = true;
        }
    }
    
    const textarea = document.getElementById(prefix + 'ToolConfig');
    if (textarea) {
        textarea.value = JSON.stringify(config, null, 2);
        if (typeof updateToolConfigSummary === 'function') {
            updateToolConfigSummary(prefix);
        }
    } else {
        console.warn('ToolConfig textarea not found for prefix:', prefix, 'Full ID:', prefix + 'ToolConfig');
    }
}

/**
 * Handle image tools checkbox change
 */
function handleImageToolsChange(prefix) {
    const imageTools = document.getElementById(prefix + 'ImageTools');
    const imageModelContainer = document.getElementById(prefix + 'ImageModelContainer');
    
    if (imageTools && imageModelContainer) {
        if (imageTools.checked) {
            imageModelContainer.style.display = 'block';
        } else {
            imageModelContainer.style.display = 'none';
        }
    }
    
    syncToolConfigToJson(prefix);
}

/**
 * Handle memory blocks checkbox change (sync + update section visibility)
 */
function handleMemoryBlocksChange(prefix) {
    syncToolConfigToJson(prefix);
    if (typeof updateMemoryBlocksSectionVisibility === 'function') {
        const textarea = document.getElementById(prefix + 'ToolConfig');
        const toolConfig = textarea ? (function() { try { return JSON.parse(textarea.value || '{}'); } catch (e) { return {}; } })() : {};
        updateMemoryBlocksSectionVisibility(prefix, toolConfig);
    }
}

/**
 * Sync JSON textarea to tool form controls
 */
function syncJsonToToolConfig(prefix = '') {
    const textarea = document.getElementById(prefix + 'ToolConfig');
    try {
        const config = JSON.parse(textarea.value || '{}');
        
        // Update checkboxes based on config
        const googleDrive = document.getElementById(prefix + 'GoogleDrive');
        const cvTools = document.getElementById(prefix + 'CvTools');
        const imageTools = document.getElementById(prefix + 'ImageTools');
        const imageModel = document.getElementById(prefix + 'ImageModel');
        const imageModelContainer = document.getElementById(prefix + 'ImageModelContainer');
        const memoryBlocks = document.getElementById(prefix + 'MemoryBlocks');
        const createAgent = document.getElementById(prefix + 'CreateAgent');
        const codeExecutor = document.getElementById(prefix + 'CodeExecutor');
        const imageDataExtraction = document.getElementById(prefix + 'ImageDataExtraction');
        const imageDataExtractionModel = document.getElementById(prefix + 'ImageDataExtractionModel');
        const imageDataExtractionModelContainer = document.getElementById(prefix + 'ImageDataExtractionModelContainer');
        
        if (googleDrive) googleDrive.checked = !!config.google_drive;
        if (cvTools) cvTools.checked = !!config.cv_tools;
        if (createAgent) createAgent.checked = !!config.create_agent;
        if (codeExecutor) codeExecutor.checked = !!config.code_executor;
        if (memoryBlocks) {
            memoryBlocks.checked = config.memory_blocks === true ||
                (config.memory_blocks && typeof config.memory_blocks === 'object' && config.memory_blocks.enabled !== false);
        }
        
        // Handle image tools
        if (imageTools) {
            imageTools.checked = !!config.image_tools;
            
            // Show/hide image model container
            if (imageModelContainer) {
                if (config.image_tools) {
                    imageModelContainer.style.display = 'block';
                    
                    // Set the model if it's an object with model property
                    if (imageModel && typeof config.image_tools === 'object' && config.image_tools.model) {
                        imageModel.value = config.image_tools.model;
                    }
                } else {
                    imageModelContainer.style.display = 'none';
                }
            }
        }
        
        // Handle image data extraction
        if (imageDataExtraction) {
            imageDataExtraction.checked = !!config.image_data_extraction;
            
            if (imageDataExtractionModelContainer) {
                if (config.image_data_extraction) {
                    imageDataExtractionModelContainer.style.display = 'block';
                    
                    if (imageDataExtractionModel && typeof config.image_data_extraction === 'object' && config.image_data_extraction.model) {
                        imageDataExtractionModel.value = config.image_data_extraction.model;
                    }
                } else {
                    imageDataExtractionModelContainer.style.display = 'none';
                }
            }
        }
        
    } catch (e) {
        // Invalid JSON, ignore
    }
    if (typeof updateToolConfigSummary === 'function') {
        updateToolConfigSummary(prefix);
    }
}

/**
 * Setup event listeners for tool configuration
 */
function setupToolListeners(prefix) {
    console.log('setupToolListeners called with prefix:', prefix);
    
    // Find the parent container that holds all tool checkboxes
    // Look for the first tool checkbox to find its parent container
    const firstCheckbox = document.getElementById(prefix + 'GoogleDrive');
    if (!firstCheckbox) {
        console.warn('Parent container not found - checkboxes may not exist yet');
        return;
    }
    
    // Find the parent container (should be the div with class "space-y-2")
    let parentContainer = firstCheckbox.closest('.space-y-2');
    if (!parentContainer) {
        // Fallback: use the parent of the first checkbox
        parentContainer = firstCheckbox.parentElement;
        while (parentContainer && parentContainer.tagName !== 'DIV') {
            parentContainer = parentContainer.parentElement;
        }
    }
    
    if (parentContainer) {
        console.log('Using event delegation on parent container');
        // Use event delegation - listen for change events on the parent
        parentContainer.addEventListener('change', function(e) {
            const target = e.target;
            if (target.type === 'checkbox') {
                const checkboxId = target.id;
                // Check if it's one of our tool checkboxes
                const toolCheckboxIds = [
                    prefix + 'GoogleDrive',
                    prefix + 'CvTools',
                    prefix + 'MemoryBlocks',
                    prefix + 'ImageTools',
                    prefix + 'CreateAgent',
                    prefix + 'CodeExecutor',
                    prefix + 'ImageDataExtraction'
                ];
                
                if (toolCheckboxIds.includes(checkboxId)) {
                    if (checkboxId === prefix + 'ImageTools') {
                        handleImageToolsChange(prefix);
                    } else if (checkboxId === prefix + 'ImageDataExtraction') {
                        handleImageDataExtractionChange(prefix);
                    } else if (checkboxId === prefix + 'MemoryBlocks') {
                        handleMemoryBlocksChange(prefix);
                    } else {
                        syncToolConfigToJson(prefix);
                    }
                }
            }
        });
    } else {
        console.warn('Parent container not found, falling back to individual listeners');
        // Fallback to individual listeners
        const toolCheckboxes = [
            prefix + 'GoogleDrive', 
            prefix + 'CvTools',
            prefix + 'MemoryBlocks',
            prefix + 'CreateAgent',
            prefix + 'CodeExecutor',
            prefix + 'ImageDataExtraction'
        ];
        
        toolCheckboxes.forEach(checkboxId => {
            const checkbox = document.getElementById(checkboxId);
            if (checkbox) {
                checkbox.addEventListener('change', function(e) {
                    if (checkboxId === prefix + 'MemoryBlocks') {
                        handleMemoryBlocksChange(prefix);
                    } else {
                        syncToolConfigToJson(prefix);
                    }
                });
            }
        });
    }
    
    const imageModelSelect = document.getElementById(prefix + 'ImageModel');
    if (imageModelSelect) {
        imageModelSelect.addEventListener('change', function() {
            syncToolConfigToJson(prefix);
        });
    }
    
    const ideModelInput = document.getElementById(prefix + 'ImageDataExtractionModel');
    if (ideModelInput) {
        ideModelInput.addEventListener('input', function() {
            syncToolConfigToJson(prefix);
        });
    }

    // JSON textarea sync
    const toolTextarea = document.getElementById(prefix + 'ToolConfig');
    if (toolTextarea) {
        toolTextarea.addEventListener('input', function() {
            syncJsonToToolConfig(prefix);
        });
    }
}

/**
 * Handle image data extraction checkbox change
 */
function handleImageDataExtractionChange(prefix) {
    const checkbox = document.getElementById(prefix + 'ImageDataExtraction');
    const modelContainer = document.getElementById(prefix + 'ImageDataExtractionModelContainer');
    
    if (checkbox && modelContainer) {
        if (checkbox.checked) {
            modelContainer.style.display = 'block';
        } else {
            modelContainer.style.display = 'none';
        }
    }
    
    syncToolConfigToJson(prefix);
}
