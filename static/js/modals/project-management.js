/**
 * Project Management JavaScript
 * Handles project creation, editing, deletion, and selection
 */

(function() {
    'use strict';

    const PROJECT_STORAGE_KEY = 'agentDashboardSelectedProjectId';

    /**
     * Handle project selection change
     */
    function handleProjectSelection(projectId) {
        try {
            localStorage.removeItem('agentRootFilter');
            localStorage.removeItem('agentSearchFilter');
            if (projectId) {
                localStorage.setItem(PROJECT_STORAGE_KEY, String(projectId));
            } else {
                localStorage.removeItem(PROJECT_STORAGE_KEY);
            }
        } catch (error) {
            // Ignore storage errors
        }
        if (!projectId) {
            window.location.href = '/dashboard/agents';
            return;
        }
        const targetUrl = `/dashboard/agents?project_id=${encodeURIComponent(projectId)}`;
        window.location.href = targetUrl;
    }

    /**
     * Show the project management modal
     */
    function showProjectModal() {
        const modal = document.getElementById('projectModal');
        if (!modal) return;
        startCreateProject();
        modal.classList.remove('hidden');
    }

    /**
     * Hide the project management modal
     */
    function hideProjectModal() {
        const modal = document.getElementById('projectModal');
        if (!modal) return;
        modal.classList.add('hidden');
    }

    /**
     * Reset the project form to initial state
     */
    function resetProjectForm() {
        const formId = document.getElementById('projectFormId');
        const nameInput = document.getElementById('projectName');
        const descriptionInput = document.getElementById('projectDescription');
        if (formId) formId.value = '';
        if (nameInput) nameInput.value = '';
        if (descriptionInput) descriptionInput.value = '';
        const hint = document.getElementById('projectFormHint');
        if (hint) hint.textContent = 'Fill out project details and click Save.';
    }

    /**
     * Set the form mode (create or edit)
     */
    function setProjectFormMode(mode, projectName = '') {
        const title = document.getElementById('projectModalTitle');
        const buttonText = document.getElementById('projectSubmitText');
        if (mode === 'edit') {
            if (title) title.textContent = `Edit Project: ${projectName}`;
            if (buttonText) buttonText.textContent = 'Update Project';
        } else {
            if (title) title.textContent = 'Create Project';
            if (buttonText) buttonText.textContent = 'Create Project';
        }
    }

    /**
     * Start creating a new project
     */
    function startCreateProject() {
        resetProjectForm();
        setProjectFormMode('create');
        const nameInput = document.getElementById('projectName');
        if (nameInput) {
            nameInput.focus();
        }
    }

    /**
     * Start editing an existing project
     */
    function startEditProject(projectId) {
        const project = window.projects.find(p => p.id === projectId);
        if (!project) {
            if (typeof showNotification === 'function') {
                showNotification('Project not found', 'error');
            }
            return;
        }
        const formId = document.getElementById('projectFormId');
        const nameInput = document.getElementById('projectName');
        const descriptionInput = document.getElementById('projectDescription');
        if (formId) formId.value = project.id;
        if (nameInput) nameInput.value = project.name || '';
        if (descriptionInput) descriptionInput.value = project.description || '';
        setProjectFormMode('edit', project.name || '');
        const hint = document.getElementById('projectFormHint');
        if (hint) hint.textContent = 'Updating a project will affect all associated agents.';
        const modal = document.getElementById('projectModal');
        if (modal) modal.classList.remove('hidden');
        if (nameInput) nameInput.focus();
    }

    /**
     * Delete a project
     */
    async function deleteProject(projectId) {
        const project = window.projects.find(p => p.id === projectId);
        if (!project) {
            if (typeof showNotification === 'function') {
                showNotification('Project not found', 'error');
            }
            return;
        }
        const confirmed = await showConfirmDialog(
            `Delete project "${project.name}"? All agents in this project will also be removed.`,
            'Delete Project',
            'Delete',
            'Cancel',
            'danger'
        );
        if (!confirmed) {
            return;
        }
        try {
            const response = await fetch(`/dashboard/api/projects/${projectId}`, {
                method: 'DELETE',
                credentials: 'same-origin'
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                const message = result.detail || result.message || 'Failed to delete project';
                if (typeof showNotification === 'function') {
                    showNotification(message, 'error');
                }
                return;
            }
            if (typeof showNotification === 'function') {
                showNotification('Project deleted successfully');
            }
            const selectedId = window.selectedProjectId ? String(window.selectedProjectId) : null;
            if (selectedId && selectedId === String(projectId)) {
                try {
                    localStorage.removeItem(PROJECT_STORAGE_KEY);
                } catch (error) {
                    // ignore
                }
                window.location.href = '/dashboard/agents';
            } else {
                window.location.reload();
            }
        } catch (error) {
            if (typeof showNotification === 'function') {
                showNotification(`Failed to delete project: ${error.message}`, 'error');
            }
        }
    }

    /**
     * Initialize project form submission handler
     */
    function initializeProjectForm() {
        const projectForm = document.getElementById('projectForm');
        if (!projectForm) return;

        projectForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const submitBtn = document.getElementById('projectSubmitBtn');
            const loader = document.getElementById('projectSubmitLoader');
            const submitText = document.getElementById('projectSubmitText');
            const formId = document.getElementById('projectFormId');
            const nameInput = document.getElementById('projectName');
            const descriptionInput = document.getElementById('projectDescription');

            const name = nameInput ? nameInput.value.trim() : '';
            if (!name) {
                if (typeof showNotification === 'function') {
                    showNotification('Project name is required', 'error');
                }
                return;
            }

            const description = descriptionInput ? descriptionInput.value.trim() : '';
            const projectId = formId ? formId.value : '';
            const formData = new FormData();
            formData.append('name', name);
            formData.append('description', description);

            const isEdit = Boolean(projectId);
            const url = isEdit ? `/dashboard/api/projects/${projectId}` : '/dashboard/api/projects';
            const method = isEdit ? 'PUT' : 'POST';

            if (submitBtn) submitBtn.disabled = true;
            if (loader) loader.classList.remove('hidden');
            if (submitText) submitText.textContent = isEdit ? 'Updating...' : 'Creating...';

            try {
                const response = await fetch(url, {
                    method,
                    credentials: 'same-origin',
                    body: formData
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    const message = result.detail || result.message || 'Failed to save project';
                    if (typeof showNotification === 'function') {
                        showNotification(message, 'error');
                    }
                    return;
                }
                const savedProject = result.project;
                if (typeof showNotification === 'function') {
                    showNotification(isEdit ? 'Project updated successfully' : 'Project created successfully');
                }
                try {
                    localStorage.setItem(PROJECT_STORAGE_KEY, String(savedProject.id));
                } catch (error) {
                    // ignore
                }
                window.location.href = `/dashboard/agents?project_id=${encodeURIComponent(savedProject.id)}`;
            } catch (error) {
                if (typeof showNotification === 'function') {
                    showNotification(`Failed to save project: ${error.message}`, 'error');
                }
            } finally {
                if (submitBtn) submitBtn.disabled = false;
                if (loader) loader.classList.add('hidden');
                if (submitText) submitText.textContent = isEdit ? 'Update Project' : 'Create Project';
            }
        });
    }

    // Export functions to window object for inline event handlers
    window.handleProjectSelection = handleProjectSelection;
    window.showProjectModal = showProjectModal;
    window.hideProjectModal = hideProjectModal;
    window.startCreateProject = startCreateProject;
    window.startEditProject = startEditProject;
    window.deleteProject = deleteProject;

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeProjectForm);
    } else {
        initializeProjectForm();
    }
})();

