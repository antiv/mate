/**
 * Agent Tools Module
 * Handles tool configuration including image tools, Google Drive, CV tools
 */

/**
 * Sync tool configuration form controls to JSON
 */
// Keys that the checkbox UI owns: these are rebuilt from checkbox state below (and removed
// when unchecked). Every OTHER key in the JSON (e.g. shop, google_search, file_search,
// supabase_storage, user_profile, custom_functions) is preserved as-is.
const CHECKBOX_MANAGED_TOOL_KEYS = [
    'google_drive', 'google_calendar', 'browser', 'cv_tools', 'image_tools',
    'memory_blocks', 'create_agent', 'code_executor', 'image_data_extraction', 'shop',
];

function syncToolConfigToJson(prefix = '') {
    // Start from the existing JSON so keys without a checkbox survive; only the
    // checkbox-managed keys are reset and re-derived from the checkboxes below.
    let config = {};
    const existingTextarea = document.getElementById(prefix + 'ToolConfig');
    if (existingTextarea && existingTextarea.value.trim()) {
        try {
            const parsed = JSON.parse(existingTextarea.value);
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                config = parsed;
            }
        } catch (e) { /* malformed JSON — fall back to rebuilding from checkboxes only */ }
    }
    // Preserve the shop object (catalog etc.) across a checkbox re-sync — the checkbox only
    // toggles presence, the catalog is authored in the JSON editor.
    const existingShop = (config.shop && typeof config.shop === 'object') ? config.shop : null;
    CHECKBOX_MANAGED_TOOL_KEYS.forEach(function (k) { delete config[k]; });

    // Check each tool checkbox
    const googleDrive = document.getElementById(prefix + 'GoogleDrive');
    const googleCalendar = document.getElementById(prefix + 'GoogleCalendar');
    const browser = document.getElementById(prefix + 'Browser');
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
    if (googleCalendar && googleCalendar.checked) {
        const calId = document.getElementById(prefix + 'GoogleCalendarId');
        const calTz = document.getElementById(prefix + 'GoogleCalendarTz');
        const calHours = document.getElementById(prefix + 'GoogleCalendarHours');
        const calSlot = document.getElementById(prefix + 'GoogleCalendarSlot');
        const gc = {};
        if (calId && calId.value.trim()) gc.calendar_id = calId.value.trim();
        if (calTz && calTz.value.trim()) gc.timezone = calTz.value.trim();
        if (calHours && calHours.value.trim()) gc.working_hours = calHours.value.trim();
        if (calSlot && calSlot.value.trim() && !isNaN(parseInt(calSlot.value, 10))) gc.slot_minutes = parseInt(calSlot.value, 10);
        config.google_calendar = Object.keys(gc).length ? gc : true;
    }
    if (browser && browser.checked) {
        config.browser = true;
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
    const shop = document.getElementById(prefix + 'Shop');
    if (shop && shop.checked) {
        // Keep the authored catalog/currency/partner_key; default to an empty catalog.
        config.shop = existingShop || { catalog: [] };
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
 * Handle Google Calendar checkbox change (show/hide the Calendar ID field + sync)
 */
function handleGoogleCalendarChange(prefix) {
    const cb = document.getElementById(prefix + 'GoogleCalendar');
    const container = document.getElementById(prefix + 'GoogleCalendarIdContainer');
    if (cb && container) {
        container.style.display = cb.checked ? 'block' : 'none';
    }
    syncToolConfigToJson(prefix);
}

/**
 * Handle Shop checkbox change (show/hide the catalog hint + sync)
 */
function handleShopChange(prefix) {
    const cb = document.getElementById(prefix + 'Shop');
    const hint = document.getElementById(prefix + 'ShopHint');
    if (cb && hint) {
        hint.style.display = cb.checked ? 'block' : 'none';
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
        const googleCalendar = document.getElementById(prefix + 'GoogleCalendar');
        const browser = document.getElementById(prefix + 'Browser');
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
        if (googleCalendar) {
            googleCalendar.checked = !!config.google_calendar;
            const calContainer = document.getElementById(prefix + 'GoogleCalendarIdContainer');
            if (calContainer) calContainer.style.display = config.google_calendar ? 'block' : 'none';
            const gc = (config.google_calendar && typeof config.google_calendar === 'object') ? config.google_calendar : {};
            const calId = document.getElementById(prefix + 'GoogleCalendarId');
            const calTz = document.getElementById(prefix + 'GoogleCalendarTz');
            const calHours = document.getElementById(prefix + 'GoogleCalendarHours');
            const calSlot = document.getElementById(prefix + 'GoogleCalendarSlot');
            if (calId) calId.value = gc.calendar_id || '';
            if (calTz) calTz.value = gc.timezone || '';
            if (calHours) calHours.value = gc.working_hours || '';
            if (calSlot) calSlot.value = (gc.slot_minutes != null ? gc.slot_minutes : '');
        }
        if (browser) browser.checked = !!config.browser;
        if (cvTools) cvTools.checked = !!config.cv_tools;
        if (createAgent) createAgent.checked = !!config.create_agent;
        if (codeExecutor) codeExecutor.checked = !!config.code_executor;
        const shop = document.getElementById(prefix + 'Shop');
        if (shop) {
            shop.checked = !!config.shop;
            const shopHint = document.getElementById(prefix + 'ShopHint');
            if (shopHint) shopHint.style.display = config.shop ? 'block' : 'none';
        }
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
                    prefix + 'GoogleCalendar',
                    prefix + 'Browser',
                    prefix + 'CvTools',
                    prefix + 'MemoryBlocks',
                    prefix + 'ImageTools',
                    prefix + 'CreateAgent',
                    prefix + 'CodeExecutor',
                    prefix + 'ImageDataExtraction',
                    prefix + 'Shop'
                ];

                if (toolCheckboxIds.includes(checkboxId)) {
                    if (checkboxId === prefix + 'ImageTools') {
                        handleImageToolsChange(prefix);
                    } else if (checkboxId === prefix + 'ImageDataExtraction') {
                        handleImageDataExtractionChange(prefix);
                    } else if (checkboxId === prefix + 'MemoryBlocks') {
                        handleMemoryBlocksChange(prefix);
                    } else if (checkboxId === prefix + 'GoogleCalendar') {
                        handleGoogleCalendarChange(prefix);
                    } else if (checkboxId === prefix + 'Shop') {
                        handleShopChange(prefix);
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
            prefix + 'GoogleCalendar',
            prefix + 'Browser',
            prefix + 'CvTools',
            prefix + 'MemoryBlocks',
            prefix + 'CreateAgent',
            prefix + 'CodeExecutor',
            prefix + 'ImageDataExtraction',
            prefix + 'Shop'
        ];

        toolCheckboxes.forEach(checkboxId => {
            const checkbox = document.getElementById(checkboxId);
            if (checkbox) {
                checkbox.addEventListener('change', function(e) {
                    if (checkboxId === prefix + 'MemoryBlocks') {
                        handleMemoryBlocksChange(prefix);
                    } else if (checkboxId === prefix + 'GoogleCalendar') {
                        handleGoogleCalendarChange(prefix);
                    } else if (checkboxId === prefix + 'Shop') {
                        handleShopChange(prefix);
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

    [prefix + 'GoogleCalendarId', prefix + 'GoogleCalendarTz', prefix + 'GoogleCalendarHours', prefix + 'GoogleCalendarSlot'].forEach(function(id) {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', function() { syncToolConfigToJson(prefix); });
    });

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
