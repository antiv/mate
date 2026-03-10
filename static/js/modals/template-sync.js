/**
 * Template Sync Modal Logic
 */

function showTemplateSyncModal(projectId) {
    const modal = document.getElementById('templateSyncModal');
    if (!modal) return;

    document.getElementById('templateSyncProjectId').value = projectId;

    // Reset UI state
    document.getElementById('templateSyncLoading').classList.remove('hidden');
    document.getElementById('templateSyncUpToDate').classList.add('hidden');
    document.getElementById('templateSyncChanges').classList.add('hidden');
    document.getElementById('templateSyncError').classList.add('hidden');

    document.getElementById('templateSyncActions').style.display = 'none';
    document.getElementById('templateSyncCloseActions').style.display = 'none';

    // Clear lists
    document.getElementById('templateSyncAddedAgents').innerHTML = '';
    document.getElementById('templateSyncUpdatedAgents').innerHTML = '';
    document.getElementById('templateSyncMemoryBlocks').innerHTML = '';

    document.getElementById('templateSyncAddedAgentsContainer').classList.add('hidden');
    document.getElementById('templateSyncUpdatedAgentsContainer').classList.add('hidden');
    document.getElementById('templateSyncMemoryBlocksContainer').classList.add('hidden');

    modal.classList.remove('hidden');

    // Fetch status
    fetch(`/dashboard/api/templates/sync-status/${projectId}`)
        .then(response => {
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            return response.json();
        })
        .then(data => {
            document.getElementById('templateSyncLoading').classList.add('hidden');

            if (data.up_to_date) {
                document.getElementById('templateSyncUpToDate').classList.remove('hidden');
                document.getElementById('templateSyncCloseActions').style.display = 'flex';
                const versionText = data.latest_version ? `You have the latest version (v${data.latest_version}).` : 'You have all the latest agents and configurations.';
                document.getElementById('templateSyncLatestVersion').textContent = versionText;
            } else {
                document.getElementById('templateSyncChanges').classList.remove('hidden');
                document.getElementById('templateSyncActions').style.display = 'flex';

                let hasChanges = false;

                // Added Agents
                if (data.agents_to_add && data.agents_to_add.length > 0) {
                    hasChanges = true;
                    const container = document.getElementById('templateSyncAddedAgentsContainer');
                    const list = document.getElementById('templateSyncAddedAgents');
                    container.classList.remove('hidden');
                    data.agents_to_add.forEach(agent => {
                        const li = document.createElement('li');
                        li.textContent = agent;
                        list.appendChild(li);
                    });
                }

                // Updated Agents
                if (data.agents_to_update && data.agents_to_update.length > 0) {
                    hasChanges = true;
                    const container = document.getElementById('templateSyncUpdatedAgentsContainer');
                    const list = document.getElementById('templateSyncUpdatedAgents');
                    container.classList.remove('hidden');
                    data.agents_to_update.forEach(agent => {
                        const li = document.createElement('li');
                        li.textContent = agent;
                        list.appendChild(li);
                    });
                }

                // Memory Blocks
                const memBlocksCount = (data.memory_blocks_to_add?.length || 0) + (data.memory_blocks_to_update?.length || 0);
                if (memBlocksCount > 0) {
                    hasChanges = true;
                    const container = document.getElementById('templateSyncMemoryBlocksContainer');
                    const list = document.getElementById('templateSyncMemoryBlocks');
                    container.classList.remove('hidden');

                    if (data.memory_blocks_to_add && data.memory_blocks_to_add.length > 0) {
                        data.memory_blocks_to_add.forEach(block => {
                            const li = document.createElement('li');
                            li.innerHTML = `New: <strong>${block}</strong>`;
                            list.appendChild(li);
                        });
                    }
                    if (data.memory_blocks_to_update && data.memory_blocks_to_update.length > 0) {
                        data.memory_blocks_to_update.forEach(block => {
                            const li = document.createElement('li');
                            li.innerHTML = `Update: <strong>${block}</strong>`;
                            list.appendChild(li);
                        });
                    }
                }

                // Clear any existing version update message
                const oldMsg = document.getElementById('templateSyncVersionOnlyMsg');
                if (oldMsg) oldMsg.remove();

                if (!hasChanges) {
                    // It's just a version bump with no structural changes
                    const noChangesMsg = document.createElement('div');
                    noChangesMsg.id = "templateSyncVersionOnlyMsg";
                    noChangesMsg.className = "text-sm text-gray-600 dark:text-gray-400 italic mb-4";
                    noChangesMsg.textContent = "Version update only. No agent or template changes required.";

                    document.getElementById('templateSyncChanges').insertBefore(
                        noChangesMsg,
                        document.getElementById('templateSyncChanges').firstChild
                    );
                }
            }
        })
        .catch(error => {
            console.error('Error fetching template sync status:', error);
            document.getElementById('templateSyncLoading').classList.add('hidden');
            document.getElementById('templateSyncError').classList.remove('hidden');
            document.getElementById('templateSyncError').textContent = `Error checking template status: ${error.message || 'Unknown error'}`;
            document.getElementById('templateSyncCloseActions').style.display = 'flex';
        });
}

function hideTemplateSyncModal() {
    const modal = document.getElementById('templateSyncModal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

function performTemplateSync() {
    const projectId = document.getElementById('templateSyncProjectId').value;
    const btn = document.getElementById('templateSyncConfirmBtn');

    if (!projectId) return;

    // Set loading state
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i> Syncing...';

    fetch('/dashboard/api/templates/sync', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ project_id: parseInt(projectId) })
    })
        .then(response => {
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            return response.json();
        })
        .then(data => {
            hideTemplateSyncModal();
            if (typeof showNotification === 'function') {
                showNotification('Project synchronized with template successfully!', 'success');
            }
            // Reload page to see changes
            setTimeout(() => {
                window.location.reload();
            }, 1500);
        })
        .catch(error => {
            console.error('Error syncing template:', error);
            btn.disabled = false;
            btn.textContent = 'Apply Updates';
            document.getElementById('templateSyncError').classList.remove('hidden');
            document.getElementById('templateSyncError').textContent = `Error syncing template: ${error.message || 'Unknown error'}`;
        });
}
