// ============================================================================
// File Search Modal Functions
// ============================================================================

function openFileSearchModal(prefix) {
    const modal = document.getElementById(`${prefix}FileSearchModal`);
    if (!modal) {
        console.error(`File Search modal not found: ${prefix}FileSearchModal`);
        return;
    }
    modal.classList.remove('hidden');
    document.body.classList.add('config-modal-open');
    
    // Load file search data for this agent
    loadFileSearchData(prefix);
}

function closeFileSearchModal(prefix) {
    const modal = document.getElementById(`${prefix}FileSearchModal`);
    if (!modal) return;
    modal.classList.add('hidden');
    document.body.classList.remove('config-modal-open');
}

async function loadFileSearchData(prefix) {
    // Get agent name and project ID from the edit form
    const agentNameInput = document.getElementById('editAgentName');
    const projectIdInput = document.getElementById('editAgentProject');
    
    if (!agentNameInput) {
        console.error('Could not find editAgentName input');
        return;
    }
    
    const agentName = agentNameInput.value;
    if (!agentName) {
        console.error('Agent name not found in form');
        return;
    }
    
    const projectId = projectIdInput ? projectIdInput.value : null;
    
    const summaryEl = document.getElementById(`${prefix}FileSearchSummary`);
    if (summaryEl) {
        summaryEl.innerHTML = '<span class="text-gray-400">Loading...</span>';
    }
    
    try {
        // Fetch stores and files for this agent, and all stores in project
        const promises = [
            fetch(`/dashboard/api/agents/${encodeURIComponent(agentName)}/file-search/stores`),
            fetch(`/dashboard/api/agents/${encodeURIComponent(agentName)}/file-search/files`)
        ];
        
        if (projectId) {
            promises.push(fetch(`/dashboard/api/projects/${projectId}/file-search/stores`));
        }
        
        const [storesResponse, filesResponse, allStoresResponse] = await Promise.all(promises);
        
        const storesData = await storesResponse.json();
        const filesData = await filesResponse.json();
        
        const stores = storesData.stores || [];
        const files = filesData.files || [];
        
        // Load all stores in project for assignment dropdown
        let allStores = [];
        if (allStoresResponse && allStoresResponse.ok) {
            const allStoresData = await allStoresResponse.json();
            allStores = allStoresData.stores || [];
        }
        
        // Update summary
        if (summaryEl) {
            if (stores.length === 0) {
                summaryEl.innerHTML = '<span class="text-gray-400">No file search stores assigned</span>';
            } else {
                const storeNames = stores.map(s => s.display_name || s.store_name).join(', ');
                const fileCount = files.length;
                summaryEl.innerHTML = `<span class="text-gray-600 dark:text-gray-300">${stores.length} store(s): ${storeNames} | ${fileCount} file(s)</span>`;
            }
        }
        
        // Update modal content
        updateFileSearchModalContent(prefix, stores, files, agentName, allStores);
        
    } catch (error) {
        console.error('Error loading file search data:', error);
        if (summaryEl) {
            summaryEl.innerHTML = '<span class="text-red-500">Error loading data</span>';
        }
    }
}

// Store files data globally for "Show all" functionality
window.fileSearchFilesData = window.fileSearchFilesData || {};

function updateFileSearchModalContent(prefix, stores, files, agentName, allStores = []) {
    const storesList = document.getElementById(`${prefix}FileSearchStoresList`);
    const uploadStoreSelect = document.getElementById(`${prefix}UploadStoreSelect`);
    const assignStoreSelect = document.getElementById(`${prefix}AssignStoreSelect`);
    
    // Store files data globally for this prefix
    window.fileSearchFilesData[prefix] = files;
    
    // Track which stores are expanded (preserve state after reload)
    if (!window.fileSearchExpandedStores) {
        window.fileSearchExpandedStores = {};
    }
    if (!window.fileSearchExpandedStores[prefix]) {
        window.fileSearchExpandedStores[prefix] = new Set();
    }
    
    // Group files by store
    const filesByStore = {};
    files.forEach(file => {
        const storeName = file.store_name;
        if (!filesByStore[storeName]) {
            filesByStore[storeName] = [];
        }
        filesByStore[storeName].push(file);
    });
    
    // Update stores list with accordion structure
    if (storesList) {
        if (stores.length === 0) {
            storesList.innerHTML = '<p class="text-sm text-gray-500 dark:text-gray-400">No stores assigned</p>';
        } else {
            storesList.innerHTML = stores.map((store, index) => {
                const storeFiles = filesByStore[store.store_name] || [];
                const fileCount = storeFiles.length;
                const storeId = `store-${index}-${prefix}`;
                // Check if this store was previously expanded
                const isExpanded = window.fileSearchExpandedStores[prefix]?.has(store.store_name) || false;
                
                // Limit initial display to 5 files
                const initialDisplayCount = 5;
                const showAll = storeFiles.length <= initialDisplayCount;
                const displayFiles = showAll ? storeFiles : storeFiles.slice(0, initialDisplayCount);
                const remainingCount = storeFiles.length - initialDisplayCount;
                
                return `
                    <div class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
                        <!-- Store Header (Always Visible) -->
                        <div class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-900/50 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800/50" 
                             onclick="toggleStoreFiles('${storeId}', '${prefix}', '${store.store_name}')"
                             data-store-name="${store.store_name}">
                            <div class="flex items-center space-x-2 flex-1">
                                <i class="fas fa-chevron-${isExpanded ? 'down' : 'right'} text-xs text-gray-500 dark:text-gray-400 transition-transform" id="${storeId}-icon"></i>
                                <div class="flex-1">
                                    <p class="text-sm font-medium text-gray-900 dark:text-white">${store.display_name || store.store_name}</p>
                                    <p class="text-xs text-gray-500 dark:text-gray-400">${fileCount} file${fileCount !== 1 ? 's' : ''} • ${store.store_name}</p>
                                </div>
                            </div>
                            <div class="flex items-center space-x-1" onclick="event.stopPropagation()">
                                <button 
                                    onclick="event.stopPropagation(); event.preventDefault(); unassignFileSearchStore('${prefix}', '${agentName}', '${store.store_name}')"
                                    class="px-2 py-1 text-xs text-orange-600 hover:text-orange-700 border border-orange-300 rounded"
                                    title="Remove from this agent"
                                    type="button"
                                >
                                    Remove
                                </button>
                                <button 
                                    onclick="event.stopPropagation(); event.preventDefault(); deleteFileSearchStore('${prefix}', '${store.store_name}', '${store.display_name || store.store_name}')"
                                    class="px-2 py-1 text-xs text-red-600 hover:text-red-700 border border-red-300 rounded"
                                    title="Delete store completely"
                                    type="button"
                                >
                                    <i class="fas fa-trash text-[10px]"></i>
                                </button>
                            </div>
                        </div>
                        
                        <!-- Store Files (Collapsible) -->
                        <div id="${storeId}-files" class="${isExpanded ? '' : 'hidden'} border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
                            <!-- Upload File Section -->
                            <div class="p-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50">
                                <div class="flex items-center space-x-2">
                                    <input 
                                        type="file" 
                                        id="${storeId}-file-upload" 
                                        class="flex-1 px-2 py-1 text-xs border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                                    >
                                    <button 
                                        type="button" 
                                        id="${storeId}-upload-button"
                                        onclick="uploadFileToStoreById('${storeId}', '${prefix}', '${store.store_name}')"
                                        class="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50 disabled:cursor-not-allowed flex items-center"
                                    >
                                        <i class="fas fa-upload text-[10px] mr-1"></i>
                                        Upload
                                    </button>
                                </div>
                                <div id="${storeId}-upload-loader" class="hidden flex items-center space-x-2 mt-2 p-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded">
                                    <div class="animate-spin rounded-full h-3 w-3 border-b-2 border-blue-600 dark:border-blue-400"></div>
                                    <span class="text-xs text-blue-800 dark:text-blue-200">Uploading file... This may take a while.</span>
                                </div>
                            </div>
                            
                            ${fileCount === 0 ? `
                                <div class="p-3 text-center">
                                    <p class="text-xs text-gray-500 dark:text-gray-400">No files in this store</p>
                                </div>
                            ` : `
                                ${fileCount > initialDisplayCount ? `
                                    <div class="p-2 border-b border-gray-200 dark:border-gray-700">
                                        <input 
                                            type="text" 
                                            id="${storeId}-search" 
                                            placeholder="Search files..." 
                                            class="w-full px-2 py-1 text-xs border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white rounded focus:outline-none focus:ring-1 focus:ring-blue-500"
                                            oninput="filterStoreFiles('${storeId}', '${store.store_name}')"
                                        >
                                    </div>
                                ` : ''}
                                <div id="${storeId}-files-list" class="max-h-96 overflow-y-auto" data-prefix="${prefix}" data-store-name="${store.store_name}" data-initial-display-count="${initialDisplayCount}" data-show-all="${showAll}">
                                    ${displayFiles.map(file => `
                                        <div class="flex items-center justify-between p-2 border-b border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/30" data-file-name="${escapeHtml(file.display_name || file.document_name)}">
                                            <div class="flex-1 min-w-0">
                                                <p class="text-sm font-medium text-gray-900 dark:text-white truncate">${escapeHtml(file.display_name || file.document_name)}</p>
                                                <p class="text-xs text-gray-500 dark:text-gray-400">${file.status || 'unknown'}${file.file_size ? ` • ${formatFileSize(file.file_size)}` : ''}</p>
                                            </div>
                                            <button 
                                                onclick="event.stopPropagation(); event.preventDefault(); deleteFileFromStore('${prefix}', '${store.store_name}', '${file.document_name}')"
                                                class="ml-2 px-2 py-1 text-xs text-red-600 hover:text-red-700 border border-red-300 rounded flex-shrink-0"
                                                title="Delete file"
                                                type="button"
                                            >
                                                <i class="fas fa-trash text-[10px]"></i>
                                            </button>
                                        </div>
                                    `).join('')}
                                    ${!showAll ? `
                                        <div class="p-2 text-center border-t border-gray-200 dark:border-gray-700" id="${storeId}-show-all-container" data-show-all="true">
                                            <button 
                                                onclick="showAllStoreFiles('${storeId}', '${store.store_name}', ${storeFiles.length})"
                                                class="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
                                                type="button"
                                            >
                                                Show all ${storeFiles.length} files (${remainingCount} more)
                                            </button>
                                        </div>
                                    ` : ''}
                                </div>
                            `}
                        </div>
                    </div>
                `;
            }).join('');
        }
    }
    
    // Update upload store dropdown
    if (uploadStoreSelect) {
        if (stores.length === 0) {
            uploadStoreSelect.innerHTML = '<option value="">No stores available</option>';
            uploadStoreSelect.disabled = true;
        } else {
            uploadStoreSelect.innerHTML = '<option value="">Select a store...</option>' + 
                stores.map(store => 
                    `<option value="${store.store_name}">${store.display_name || store.store_name}</option>`
                ).join('');
            uploadStoreSelect.disabled = false;
        }
    }
    
    // Update assign store dropdown (all stores in project, excluding already assigned, plus "New store" option)
    if (assignStoreSelect) {
        const assignedStoreNames = new Set(stores.map(s => s.store_name));
        const availableStores = allStores.filter(s => !assignedStoreNames.has(s.store_name));
        
        let options = '<option value="">Select a store to assign...</option>';
        options += '<option value="__new_store__">➕ Create New Store</option>';
        
        if (availableStores.length > 0) {
            options += availableStores.map(store => 
                `<option value="${store.store_name}">${store.display_name || store.store_name}</option>`
            ).join('');
        }
        
        assignStoreSelect.innerHTML = options;
        assignStoreSelect.disabled = false;
        
        // Reset new store input visibility
        const newStoreContainer = document.getElementById(`${prefix}NewStoreNameContainer`);
        if (newStoreContainer) {
            newStoreContainer.classList.add('hidden');
        }
        const assignButtonText = document.getElementById(`${prefix}AssignButtonText`);
        if (assignButtonText) {
            assignButtonText.textContent = 'Assign Store';
        }
    }
}

// Helper functions for store accordion
function toggleStoreFiles(storeId, prefix, storeName) {
    const filesDiv = document.getElementById(`${storeId}-files`);
    const icon = document.getElementById(`${storeId}-icon`);
    
    if (!filesDiv || !icon || !prefix || !storeName) return;
    
    // Initialize expanded stores tracking if needed
    if (!window.fileSearchExpandedStores) {
        window.fileSearchExpandedStores = {};
    }
    if (!window.fileSearchExpandedStores[prefix]) {
        window.fileSearchExpandedStores[prefix] = new Set();
    }
    
    const isHidden = filesDiv.classList.contains('hidden');
    if (isHidden) {
        filesDiv.classList.remove('hidden');
        icon.classList.remove('fa-chevron-right');
        icon.classList.add('fa-chevron-down');
        // Track expanded state
        window.fileSearchExpandedStores[prefix].add(storeName);
    } else {
        filesDiv.classList.add('hidden');
        icon.classList.remove('fa-chevron-down');
        icon.classList.add('fa-chevron-right');
        // Remove from expanded set
        window.fileSearchExpandedStores[prefix].delete(storeName);
    }
}

function filterStoreFiles(storeId, storeName) {
    const searchInput = document.getElementById(`${storeId}-search`);
    const filesList = document.getElementById(`${storeId}-files-list`);
    
    if (!searchInput || !filesList) return;
    
    const searchTerm = searchInput.value.toLowerCase().trim();
    const prefix = filesList.getAttribute('data-prefix');
    const actualStoreName = filesList.getAttribute('data-store-name') || storeName;
    
    if (!prefix) return;
    
    // Get all files for this store from global data
    const allFiles = window.fileSearchFilesData[prefix] || [];
    const storeFiles = allFiles.filter(f => f.store_name === actualStoreName);
    
    // Hide "Show all" button when searching
    const showAllContainer = filesList.parentElement.querySelector('.p-2.text-center');
    if (showAllContainer) {
        if (searchTerm) {
            showAllContainer.style.display = 'none';
        } else {
            showAllContainer.style.display = '';
        }
    }
    
    // If search is empty, restore original display (first N files or all if already expanded)
    if (!searchTerm) {
        // Get original display settings
        const initialDisplayCount = parseInt(filesList.getAttribute('data-initial-display-count') || '5', 10);
        const showAll = filesList.getAttribute('data-show-all') === 'true';
        
        // Get all files for this store
        const allFiles = window.fileSearchFilesData[prefix] || [];
        const storeFiles = allFiles.filter(f => f.store_name === actualStoreName);
        
        // Determine which files to show
        const displayFiles = showAll ? storeFiles : storeFiles.slice(0, initialDisplayCount);
        const remainingCount = storeFiles.length - initialDisplayCount;
        
        // Re-render with original display
        filesList.innerHTML = displayFiles.map(file => `
            <div class="flex items-center justify-between p-2 border-b border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/30" data-file-name="${escapeHtml(file.display_name || file.document_name)}">
                <div class="flex-1 min-w-0">
                    <p class="text-sm font-medium text-gray-900 dark:text-white truncate">${escapeHtml(file.display_name || file.document_name)}</p>
                    <p class="text-xs text-gray-500 dark:text-gray-400">${file.status || 'unknown'}${file.file_size ? ` • ${formatFileSize(file.file_size)}` : ''}</p>
                </div>
                <button 
                    onclick="event.stopPropagation(); event.preventDefault(); deleteFileFromStore('${prefix}', '${actualStoreName}', '${file.document_name}')"
                    class="ml-2 px-2 py-1 text-xs text-red-600 hover:text-red-700 border border-red-300 rounded flex-shrink-0"
                    title="Delete file"
                    type="button"
                >
                    <i class="fas fa-trash text-[10px]"></i>
                </button>
            </div>
        `).join('');
        
        // Add "Show all" button if needed
        if (!showAll && storeFiles.length > initialDisplayCount) {
            filesList.innerHTML += `
                <div class="p-2 text-center border-t border-gray-200 dark:border-gray-700" id="${storeId}-show-all-container" data-show-all="true">
                    <button 
                        onclick="showAllStoreFiles('${storeId}', '${actualStoreName}', ${storeFiles.length})"
                        class="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
                        type="button"
                    >
                        Show all ${storeFiles.length} files (${remainingCount} more)
                    </button>
                </div>
            `;
        }
        
        return;
    }
    
    // Filter files by search term
    const filteredFiles = storeFiles.filter(file => {
        const fileName = (file.display_name || file.document_name || '').toLowerCase();
        return fileName.includes(searchTerm);
    });
    
    // Re-render with filtered files
    filesList.innerHTML = filteredFiles.map(file => `
        <div class="flex items-center justify-between p-2 border-b border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/30" data-file-name="${escapeHtml(file.display_name || file.document_name)}">
            <div class="flex-1 min-w-0">
                <p class="text-sm font-medium text-gray-900 dark:text-white truncate">${escapeHtml(file.display_name || file.document_name)}</p>
                <p class="text-xs text-gray-500 dark:text-gray-400">${file.status || 'unknown'}${file.file_size ? ` • ${formatFileSize(file.file_size)}` : ''}</p>
            </div>
            <button 
                onclick="event.stopPropagation(); event.preventDefault(); deleteFileFromStore('${prefix}', '${actualStoreName}', '${file.document_name}')"
                class="ml-2 px-2 py-1 text-xs text-red-600 hover:text-red-700 border border-red-300 rounded flex-shrink-0"
                title="Delete file"
                type="button"
            >
                <i class="fas fa-trash text-[10px]"></i>
            </button>
        </div>
    `).join('');
    
    // Show message if no results
    if (filteredFiles.length === 0) {
        filesList.innerHTML = `
            <div class="p-3 text-center">
                <p class="text-xs text-gray-500 dark:text-gray-400">No files match "${escapeHtml(searchTerm)}"</p>
            </div>
        `;
    }
}

function showAllStoreFiles(storeId, storeName, totalCount) {
    const filesList = document.getElementById(`${storeId}-files-list`);
    if (!filesList) return;
    
    // Check if search is active - don't show all if searching
    const searchInput = document.getElementById(`${storeId}-search`);
    if (searchInput && searchInput.value.trim()) {
        // If search is active, just trigger filter to refresh
        filterStoreFiles(storeId, storeName);
        return;
    }
    
    // Get prefix and store name from data attributes
    const prefix = filesList.getAttribute('data-prefix');
    const actualStoreName = filesList.getAttribute('data-store-name') || storeName;
    
    if (!prefix) return;
    
    const allFiles = window.fileSearchFilesData[prefix] || [];
    const storeFiles = allFiles.filter(f => f.store_name === actualStoreName);
    
    // Update data attribute to mark as "show all"
    filesList.setAttribute('data-show-all', 'true');
    
    // Remove "Show all" button
    const showAllContainer = document.getElementById(`${storeId}-show-all-container`);
    if (showAllContainer) {
        showAllContainer.remove();
    }
    
    // Render all files
    filesList.innerHTML = storeFiles.map(file => `
        <div class="flex items-center justify-between p-2 border-b border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/30" data-file-name="${escapeHtml(file.display_name || file.document_name)}">
            <div class="flex-1 min-w-0">
                <p class="text-sm font-medium text-gray-900 dark:text-white truncate">${escapeHtml(file.display_name || file.document_name)}</p>
                <p class="text-xs text-gray-500 dark:text-gray-400">${file.status || 'unknown'}${file.file_size ? ` • ${formatFileSize(file.file_size)}` : ''}</p>
            </div>
            <button 
                onclick="event.stopPropagation(); event.preventDefault(); deleteFileFromStore('${prefix}', '${actualStoreName}', '${file.document_name}')"
                class="ml-2 px-2 py-1 text-xs text-red-600 hover:text-red-700 border border-red-300 rounded flex-shrink-0"
                title="Delete file"
                type="button"
            >
                <i class="fas fa-trash text-[10px]"></i>
            </button>
        </div>
    `).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatFileSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function unassignFileSearchStore(prefix, agentName, storeName) {
    const confirmed = await showConfirmDialog(
        `Remove store "${storeName}" from agent "${agentName}"?`,
        'Remove Store',
        'Remove',
        'Cancel',
        'warning'
    );
    if (!confirmed) {
        return;
    }
    
    try {
        const formData = new FormData();
        formData.append('store_name', storeName);
        
        const response = await fetch(`/dashboard/api/agents/${encodeURIComponent(agentName)}/file-search/stores/unassign`, {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Reload data
            await loadFileSearchData(prefix);
            // Show message about reinitialization if needed
            if (result.needs_reload) {
                const agentNameInput = document.getElementById('editAgentName');
                const agentName = agentNameInput ? agentNameInput.value : agentName;
                showAlert(`Store unassigned successfully!\n\nNote: Agent "${agentName}" should be reinitialized to apply changes. You can use the "Reinitialize" button in the agent list.`, 'success');
            }
        } else {
            showAlert('Failed to unassign store: ' + (result.message || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error unassigning store:', error);
        showAlert('Error unassigning store: ' + error.message, 'error');
    }
}

async function createFileSearchStore(prefix) {
    const agentNameInput = document.getElementById('editAgentName');
    const projectIdInput = document.getElementById('editAgentProject');
    
    if (!agentNameInput) {
        console.error('Could not find editAgentName input');
        return;
    }
    
    const agentName = agentNameInput.value;
    const projectId = projectIdInput ? parseInt(projectIdInput.value, 10) : 1;
    const displayNameInput = document.getElementById(`${prefix}NewStoreDisplayName`);
    
    if (!displayNameInput || !displayNameInput.value.trim()) {
        showAlert('Please enter a store display name', 'warning');
        return;
    }
    
    const displayName = displayNameInput.value.trim();
    
    try {
        const payload = {
            display_name: displayName,
            project_id: projectId,
            agent_name: agentName,
            is_primary: false
        };
        
        const response = await fetch('/dashboard/api/file-search/stores/create', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Clear input
            displayNameInput.value = '';
            // Reset select
            const assignStoreSelect = document.getElementById(`${prefix}AssignStoreSelect`);
            if (assignStoreSelect) {
                assignStoreSelect.value = '';
            }
            // Hide new store input
            const newStoreContainer = document.getElementById(`${prefix}NewStoreNameContainer`);
            if (newStoreContainer) {
                newStoreContainer.classList.add('hidden');
            }
            // Reset button text
            const assignButtonText = document.getElementById(`${prefix}AssignButtonText`);
            if (assignButtonText) {
                assignButtonText.textContent = 'Assign Store';
            }
            // Reload data
            await loadFileSearchData(prefix);
            showAlert(`Store created and assigned successfully!`, 'success');
        } else {
            showAlert('Failed to create store: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error creating store:', error);
        showAlert('Error creating store: ' + error.message, 'error');
    }
}

async function uploadFileToStoreById(storeId, prefix, storeName) {
    const agentNameInput = document.getElementById('editAgentName');
    if (!agentNameInput) {
        console.error('Could not find editAgentName input');
        return;
    }
    
    const agentName = agentNameInput.value;
    const fileInput = document.getElementById(`${storeId}-file-upload`);
    const uploadLoader = document.getElementById(`${storeId}-upload-loader`);
    const uploadButton = document.getElementById(`${storeId}-upload-button`);
    
    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
        showAlert('Please select a file to upload', 'warning');
        return;
    }
    
    const file = fileInput.files[0];
    
    // Show loader and disable controls
    if (uploadLoader) {
        uploadLoader.classList.remove('hidden');
    }
    if (uploadButton) {
        uploadButton.disabled = true;
    }
    if (fileInput) {
        fileInput.disabled = true;
    }
    
    try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('store_name', storeName);
        formData.append('display_name', file.name);
        formData.append('agent_name', agentName);
        
        const response = await fetch('/dashboard/api/file-search/stores/upload', {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Clear file input
            fileInput.value = '';
            // Reload data
            await loadFileSearchData(prefix);
            showAlert('File uploaded successfully!', 'success');
        } else {
            showAlert('Failed to upload file: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error uploading file:', error);
        showAlert('Error uploading file: ' + error.message, 'error');
    } finally {
        // Hide loader and enable controls
        if (uploadLoader) {
            uploadLoader.classList.add('hidden');
        }
        if (uploadButton) {
            uploadButton.disabled = false;
        }
        if (fileInput) {
            fileInput.disabled = false;
        }
    }
}

async function uploadFileToStore(prefix) {
    const agentNameInput = document.getElementById('editAgentName');
    if (!agentNameInput) {
        console.error('Could not find editAgentName input');
        return;
    }
    
    const agentName = agentNameInput.value;
    const storeSelect = document.getElementById(`${prefix}UploadStoreSelect`);
    const fileInput = document.getElementById(`${prefix}FileUpload`);
    const uploadLoader = document.getElementById(`${prefix}UploadLoader`);
    const uploadButton = document.getElementById(`${prefix}UploadButton`);
    
    if (!storeSelect || !storeSelect.value) {
        showAlert('Please select a store', 'warning');
        return;
    }
    
    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
        showAlert('Please select a file to upload', 'warning');
        return;
    }
    
    const storeName = storeSelect.value;
    const file = fileInput.files[0];
    
    // Show loader and disable controls
    if (uploadLoader) {
        uploadLoader.classList.remove('hidden');
    }
    if (uploadButton) {
        uploadButton.disabled = true;
    }
    if (storeSelect) {
        storeSelect.disabled = true;
    }
    if (fileInput) {
        fileInput.disabled = true;
    }
    
    try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('store_name', storeName);
        formData.append('display_name', file.name);
        formData.append('agent_name', agentName);
        
        const response = await fetch('/dashboard/api/file-search/stores/upload', {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Clear file input
            fileInput.value = '';
            // Reload data
            await loadFileSearchData(prefix);
            const agentNameInput = document.getElementById('editAgentName');
            const agentName = agentNameInput ? agentNameInput.value : '';
            showAlert(`File uploaded successfully!\n\nNote: Agent "${agentName}" should be reinitialized to use the new file.`, 'success');
        } else {
            showAlert('Failed to upload file: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error uploading file:', error);
        showAlert('Error uploading file: ' + error.message, 'error');
    } finally {
        // Hide loader and enable controls
        if (uploadLoader) {
            uploadLoader.classList.add('hidden');
        }
        if (uploadButton) {
            uploadButton.disabled = false;
        }
        if (storeSelect) {
            storeSelect.disabled = false;
        }
        if (fileInput) {
            fileInput.disabled = false;
        }
    }
}

async function deleteFileFromStore(prefix, storeName, documentName) {
    // Prevent event propagation
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    
    const confirmed = await showConfirmDialog(
        `Delete file "${documentName}" from store "${storeName}"?`,
        'Delete File',
        'Delete',
        'Cancel',
        'danger'
    );
    if (!confirmed) {
        return;
    }
    
    try {
        const formData = new FormData();
        formData.append('store_name', storeName);
        formData.append('document_name', documentName);
        
        const response = await fetch(`/dashboard/api/file-search/stores/files/delete`, {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Reload data
            await loadFileSearchData(prefix);
            // Show success message without closing modal
            const filesList = document.getElementById(`${prefix}FileSearchFilesList`);
            if (filesList) {
                const successMsg = document.createElement('div');
                successMsg.className = 'p-2 mb-2 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded text-xs text-green-800 dark:text-green-200';
                successMsg.innerHTML = '<i class="fas fa-check-circle mr-1"></i>File deleted successfully';
                filesList.insertBefore(successMsg, filesList.firstChild);
                // Remove message after 3 seconds
                setTimeout(() => {
                    if (successMsg.parentNode) {
                        successMsg.remove();
                    }
                }, 3000);
            }
        } else {
            showAlert('Failed to delete file: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error deleting file:', error);
        showAlert('Error deleting file: ' + error.message, 'error');
    }
}

async function deleteFileSearchStore(prefix, storeName, displayName) {
    // Prevent event propagation
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    
    try {
        // First check which agents are using this store
        const agentsResponse = await fetch(`/dashboard/api/file-search/stores/agents?store_name=${encodeURIComponent(storeName)}`);
        const agentsData = await agentsResponse.json();
        
        if (!agentsData.success) {
            console.error('Failed to get agents for store:', agentsData.error);
            showAlert('Failed to check store usage: ' + (agentsData.error || 'Unknown error'), 'error');
            return;
        }
        
        const agents = agentsData.agents || [];
        
        // Build warning message
        let warningMessage = `Delete store "${displayName}"?\n\n`;
        
        if (agents.length > 0) {
            warningMessage += `⚠️ WARNING: This store is currently used by ${agents.length} agent(s):\n`;
            warningMessage += agents.map(a => `  • ${a}`).join('\n');
            warningMessage += `\n\nThis will remove the store from ALL these agents and delete it completely.`;
        } else {
            warningMessage += `This store is not used by any agents.`;
        }
        
        warningMessage += `\n\nThis action cannot be undone.`;
        
        const confirmed = await showConfirmDialog(
            warningMessage,
            'Delete Store',
            'Delete',
            'Cancel',
            agents.length > 0 ? 'danger' : 'warning'
        );
        if (!confirmed) {
            return;
        }
        
        // Delete the store
        const formData = new FormData();
        formData.append('store_name', storeName);
        
        const response = await fetch('/dashboard/api/file-search/stores/delete', {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Reload data
            await loadFileSearchData(prefix);
            
            // Show success message
            const storesList = document.getElementById(`${prefix}FileSearchStoresList`);
            if (storesList) {
                const successMsg = document.createElement('div');
                successMsg.className = 'p-2 mb-2 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded text-xs text-green-800 dark:text-green-200';
                successMsg.innerHTML = `<i class="fas fa-check-circle mr-1"></i>${result.message || 'Store deleted successfully'}`;
                storesList.insertBefore(successMsg, storesList.firstChild);
                // Remove message after 5 seconds
                setTimeout(() => {
                    if (successMsg.parentNode) {
                        successMsg.remove();
                    }
                }, 5000);
            }
        } else {
            showAlert('Failed to delete store: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error deleting store:', error);
        showAlert('Error deleting store: ' + error.message, 'error');
    }
}

function handleStoreSelectChange(prefix) {
    const assignStoreSelect = document.getElementById(`${prefix}AssignStoreSelect`);
    const newStoreContainer = document.getElementById(`${prefix}NewStoreNameContainer`);
    const assignButtonText = document.getElementById(`${prefix}AssignButtonText`);
    
    if (!assignStoreSelect) return;
    
    const isNewStore = assignStoreSelect.value === '__new_store__';
    
    if (newStoreContainer) {
        if (isNewStore) {
            newStoreContainer.classList.remove('hidden');
            if (assignButtonText) {
                assignButtonText.textContent = 'Create Store';
            }
        } else {
            newStoreContainer.classList.add('hidden');
            if (assignButtonText) {
                assignButtonText.textContent = 'Assign Store';
            }
        }
    }
}

async function handleAssignOrCreateStore(prefix) {
    const assignStoreSelect = document.getElementById(`${prefix}AssignStoreSelect`);
    
    if (!assignStoreSelect || !assignStoreSelect.value) {
        showAlert('Please select a store to assign or create', 'warning');
        return;
    }
    
    if (assignStoreSelect.value === '__new_store__') {
        // Create new store
        await createFileSearchStore(prefix);
    } else {
        // Assign existing store
        await assignExistingStore(prefix);
    }
}

async function assignExistingStore(prefix) {
    const agentNameInput = document.getElementById('editAgentName');
    const assignStoreSelect = document.getElementById(`${prefix}AssignStoreSelect`);
    
    if (!agentNameInput) {
        console.error('Could not find editAgentName input');
        return;
    }
    
    if (!assignStoreSelect || !assignStoreSelect.value || assignStoreSelect.value === '__new_store__') {
        showAlert('Please select a store to assign', 'warning');
        return;
    }
    
    const agentName = agentNameInput.value;
    const storeName = assignStoreSelect.value;
    
    try {
        const formData = new FormData();
        formData.append('store_name', storeName);
        formData.append('is_primary', 'false');
        
        const response = await fetch(`/dashboard/api/agents/${encodeURIComponent(agentName)}/file-search/stores/assign`, {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Reload data
            await loadFileSearchData(prefix);
            // Reset select
            assignStoreSelect.value = '';
            // Hide new store input if visible
            const newStoreContainer = document.getElementById(`${prefix}NewStoreNameContainer`);
            if (newStoreContainer) {
                newStoreContainer.classList.add('hidden');
            }
            showAlert(`Store assigned successfully!`, 'success');
        } else {
            showAlert('Failed to assign store: ' + (result.message || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Error assigning store:', error);
        showAlert('Error assigning store: ' + error.message, 'error');
    }
}

// Export functions to window
window.openFileSearchModal = openFileSearchModal;
window.closeFileSearchModal = closeFileSearchModal;
window.unassignFileSearchStore = unassignFileSearchStore;
window.createFileSearchStore = createFileSearchStore;
window.uploadFileToStore = uploadFileToStore;
window.deleteFileFromStore = deleteFileFromStore;
window.deleteFileSearchStore = deleteFileSearchStore;
window.handleStoreSelectChange = handleStoreSelectChange;
window.handleAssignOrCreateStore = handleAssignOrCreateStore;
window.assignExistingStore = assignExistingStore;
window.toggleStoreFiles = toggleStoreFiles;
window.filterStoreFiles = filterStoreFiles;
window.showAllStoreFiles = showAllStoreFiles;
window.uploadFileToStoreById = uploadFileToStoreById;

