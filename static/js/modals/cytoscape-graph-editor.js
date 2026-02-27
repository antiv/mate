/**
 * Cytoscape.js Graph Editor for Agent Hierarchy
 * Better graph visualization with proper layouts and interactions
 */

function getSelectedProjectId() {
    if (typeof window === 'undefined') {
        return null;
    }
    const value = window.selectedProjectId;
    return value === undefined || value === null ? null : value;
}

function ensureProjectId(alertUser = true) {
    const projectId = getSelectedProjectId();
    if (projectId === null && alertUser) {
        alert('Select a project before performing this action.');
    }
    return projectId;
}

function buildAgentsListUrl() {
    const projectId = getSelectedProjectId();
    if (projectId === null) {
        return '/dashboard/api/agents';
    }
    return `/dashboard/api/agents?project_id=${encodeURIComponent(projectId)}`;
}

function appendProjectToFormData(formData) {
    const projectId = getSelectedProjectId();
    if (projectId === null) {
        return false;
    }
    formData.append('project_id', projectId);
    return true;
}

class CytoscapeGraphEditor {
    constructor() {
        this.cy = null;
        this.selectedNode = null;
        this.selectedEdge = null;
        this.agents = [];
        this.filteredAgents = [];
        this.agentsModified = false; // Track if agents were modified
        this.deletedAgentIds = []; // Track IDs of deleted agents
        this.modifiedAgentIds = new Set(); // Track IDs of modified agents (for connection changes)
        this.changesSaved = false; // Track if any changes were saved (need reload on close)
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
    }
    
    setupEventListeners() {
        // Modal controls
        document.getElementById('addNodeBtn')?.addEventListener('click', () => this.showAddNodeModal());
        document.getElementById('saveGraphBtn')?.addEventListener('click', () => this.saveGraph());
        document.getElementById('refreshGraphBtn')?.addEventListener('click', () => this.refreshGraph());
        
        // Zoom controls
        document.getElementById('zoomInBtn')?.addEventListener('click', () => this.zoomIn());
        document.getElementById('zoomOutBtn')?.addEventListener('click', () => this.zoomOut());
        document.getElementById('resetZoomBtn')?.addEventListener('click', () => this.resetZoom());
        
        // Search
        document.getElementById('graphSearchInput')?.addEventListener('input', (e) => this.filterNodes(e.target.value));
        
        // Add node modal
        document.getElementById('createNewAgentBtn')?.addEventListener('click', () => this.createNewAgent());
        
        // Connection modal
        document.getElementById('createConnectionBtn')?.addEventListener('click', () => this.createConnection());
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => this.handleKeyboard(e));
    }
    
    loadAgents(agents) {
        // Validate agents array
        const validAgents = agents.filter(agent => {
            if (!agent) {
                console.error('Found null/undefined agent');
                return false;
            }
            if (!agent.id) {
                console.error('Found agent without ID:', agent);
                return false;
            }
            if (!agent.name) {
                console.error('Found agent without name:', agent);
                return false;
            }
            return true;
        });
        
        this.agents = validAgents;
        this.filteredAgents = [...validAgents];
        this.initializeCytoscape();
        this.updateStats();
    }
    
    initializeCytoscape() {
        const container = document.getElementById('graphContainer');
        if (!container) return;
        
        // Clear existing cytoscape instance
        if (this.cy) {
            this.cy.destroy();
        }
        
        // Prepare data for Cytoscape
        const elements = this.prepareCytoscapeData();
        
        // Check if Dagre layout is available
        let layoutConfig = {
            name: 'breadthfirst',
            directed: true,
            padding: 50,
            spacingFactor: 1.5,
            avoidOverlap: true
        };
        
        // Try to use Dagre layout if available
        if (typeof cytoscape !== 'undefined' && cytoscape('core', 'layout', 'dagre')) {
            layoutConfig = {
                name: 'dagre',
                rankDir: 'TB',
                spacingFactor: 1.5,
                nodeSep: 50,
                rankSep: 100,
                padding: 50
            };
        }
        
        // Initialize Cytoscape
        this.cy = cytoscape({
            container: container,
            elements: elements,
            style: [
                {
                    selector: 'node',
                    style: {
                        'background-color': '#374151',
                        'border-color': '#6b7280',
                        'border-width': 2,
                        'label': function(ele) { return '🤖 ' + ele.data('label'); },
                        'text-valign': 'center',
                        'text-halign': 'center',
                        'color': 'white',
                        'font-size': '12px',
                        'font-weight': '500',
                        'text-outline-width': 2,
                        'text-outline-color': '#000000',
                        'width': '120px',
                        'height': '40px',
                        'shape': 'round-rectangle'
                    }
                },
                {
                    selector: 'node.root',
                    style: {
                        'background-color': '#1f2937',
                        'border-color': '#10b981',
                        'border-width': 3,
                        'width': '140px',
                        'height': '50px'
                    }
                },
                {
                    selector: 'node.llm',
                    style: {
                        'background-color': '#1e40af'
                    }
                },
                {
                    selector: 'node.sequential',
                    style: {
                        'background-color': '#059669'
                    }
                },
                {
                    selector: 'node.parallel',
                    style: {
                        'background-color': '#d97706'
                    }
                },
                {
                    selector: 'node.loop',
                    style: {
                        'background-color': '#dc2626'
                    }
                },
                {
                    selector: 'node.disabled',
                    style: {
                        'background-color': '#4b5563',
                        'opacity': 0.6
                    }
                },
                {
                    selector: 'node:selected',
                    style: {
                        'border-color': '#f59e0b',
                        'border-width': 5,
                        'box-shadow': '0 0 20px #f59e0b'
                    }
                },
                {
                    selector: 'edge',
                    style: {
                        'width': 3,
                        'line-color': '#ffffff',
                        'target-arrow-color': '#ffffff',
                        'target-arrow-shape': 'triangle',
                        'curve-style': 'bezier',
                        'opacity': 0.8
                    }
                },
                {
                    selector: 'edge:selected',
                    style: {
                        'line-color': '#3b82f6',
                        'target-arrow-color': '#3b82f6',
                        'width': 4
                    }
                }
            ],
            layout: layoutConfig,
            userZoomingEnabled: true,
            userPanningEnabled: true,
            boxSelectionEnabled: true,
            selectionType: 'single'
        });
        
        // Add event listeners
        this.setupCytoscapeEvents();
    }
    
    prepareCytoscapeData() {
        const nodes = this.filteredAgents.map(agent => {
            // Validate agent data
            if (!agent) {
                console.error('Undefined agent found in filteredAgents');
                return null;
            }
            
            if (!agent.id) {
                console.error('Agent missing ID:', agent);
                return null;
            }
            
            if (!agent.name) {
                console.error('Agent missing name:', agent);
                return null;
            }
            
            const nodeData = {
                data: {
                    id: agent.id,
                    label: agent.name,
                    type: agent.type || 'llm',
                    disabled: agent.disabled || false,
                    isRoot: !agent.parent_agents || agent.parent_agents.length === 0,
                    ...agent
                },
                classes: this.getNodeClasses(agent)
            };
            return nodeData;
        }).filter(node => node !== null); // Remove null entries
        
        const edges = [];
        this.filteredAgents.forEach(agent => {
            if (agent.parent_agents && agent.parent_agents.length > 0) {
                agent.parent_agents.forEach(parentName => {
                    const parentAgent = this.filteredAgents.find(a => a.name === parentName);
                    if (parentAgent) {
                        const edgeData = {
                            data: {
                                id: `${parentAgent.id}-${agent.id}`,
                                source: parentAgent.id,
                                target: agent.id,
                                sourceName: parentAgent.name,
                                targetName: agent.name
                            }
                        };
                        edges.push(edgeData);
                    } else {
                        console.warn('Parent agent not found:', parentName, 'for agent:', agent.name);
                    }
                });
            }
        });
        
        return [...nodes, ...edges];
    }
    
    getNodeClasses(agent) {
        const classes = [];
        
        // Add type class (default to 'llm' if not specified)
        const type = agent.type || 'llm';
        classes.push(type);
        
        if (!agent.parent_agents || agent.parent_agents.length === 0) {
            classes.push('root');
        }
        if (agent.disabled) {
            classes.push('disabled');
        }
        
        return classes.join(' ');
    }
    
    setupCytoscapeEvents() {
        // Node events
        this.cy.on('tap', 'node', (event) => {
            const node = event.target;
            this.selectNode(node);
        });
        
        this.cy.on('dbltap', 'node', (event) => {
            const node = event.target;
            this.editNode(node);
        });
        
        this.cy.on('cxttap', 'node', (event) => {
            const node = event.target;
            this.showNodeContextMenu(event, node);
        });
        
        // Edge events
        this.cy.on('tap', 'edge', (event) => {
            const edge = event.target;
            this.selectEdge(edge);
        });
        
        this.cy.on('cxttap', 'edge', (event) => {
            const edge = event.target;
            this.showEdgeContextMenu(event, edge);
        });
        
        // Background events
        this.cy.on('tap', (event) => {
            if (event.target === this.cy) {
                this.clearSelection();
            }
        });
        
        // Hover events
        this.cy.on('mouseover', 'node', (event) => {
            const node = event.target;
            this.showTooltip(event, node);
        });
        
        this.cy.on('mouseout', 'node', () => {
            this.hideTooltip();
        });
    }
    
    selectNode(node) {
        this.clearSelection();
        this.selectedNode = node;
        node.select();
        this.updateSelectedNodeInfo(node);
    }
    
    selectEdge(edge) {
        this.clearSelection();
        this.selectedEdge = edge;
        edge.select();
        this.updateSelectedNodeInfo(null, edge);
    }
    
    clearSelection() {
        this.selectedNode = null;
        this.selectedEdge = null;
        this.cy.elements().unselect();
        this.updateSelectedNodeInfo();
    }
    
    editNode(node) {
        
        const nodeId = node.data('id');
        // Try to find agent by ID (handle both string and numeric comparisons)
        let agent = this.agents.find(a => a.id == nodeId); // Use == for loose comparison
        
        // If not found, try exact string comparison
        if (!agent) {
            agent = this.agents.find(a => String(a.id) === String(nodeId));
        }
        
        
        if (agent) {
            // Set flag to indicate we're editing from graph editor
            window.editingFromGraphEditor = true;
            window.graphEditorInstance = this;
            
            // Try multiple ways to call the edit function
            if (typeof editAgent === 'function') {
                editAgent(agent);
            } else if (typeof window.editAgent === 'function') {
                window.editAgent(agent);
            } else {
                console.error('editAgent function not found in global scope');
                // Fallback: try to find and call the function
                const editFunction = window.editAgent || editAgent;
                if (typeof editFunction === 'function') {
                    editFunction(agent);
                } else {
                    alert('Edit function not available. Please refresh the page and try again.');
                }
            }
        } else {
            console.error('Agent not found for node ID:', nodeId);
            
            // Try to find by name as fallback
            const agentByName = this.agents.find(a => a.name === node.data('label'));
            if (agentByName) {
                // Set flag to indicate we're editing from graph editor
                window.editingFromGraphEditor = true;
                window.graphEditorInstance = this;
                
                if (typeof editAgent === 'function') {
                    editAgent(agentByName);
                } else if (typeof window.editAgent === 'function') {
                    window.editAgent(agentByName);
                }
            } else {
                console.error('Agent not found by name either:', node.data('label'));
            }
        }
    }
    
    showNodeContextMenu(event, node) {
        event.preventDefault();
        event.stopPropagation();
        
        const menuItems = [
            { text: 'Edit Agent', action: () => this.editNode(node) },
            { text: 'Connect to...', action: () => this.startConnection(node) },
            { text: 'Delete Agent', action: () => this.deleteNode(node), danger: true }
        ];
        
        this.showContextMenu(event, menuItems);
    }
    
    showEdgeContextMenu(event, edge) {
        event.preventDefault();
        event.stopPropagation();
        
        const menuItems = [
            { text: 'Delete Connection', action: () => this.deleteEdge(edge), danger: true }
        ];
        
        this.showContextMenu(event, menuItems);
    }
    
    showContextMenu(event, items) {
        // Implementation similar to D3 version
        const contextMenu = document.querySelector('.graph-context-menu');
        if (!contextMenu) return;
        
        contextMenu.innerHTML = '';
        contextMenu.className = 'graph-context-menu';
        
        items.forEach(item => {
            const button = document.createElement('button');
            button.className = `graph-context-menu-item ${item.danger ? 'danger' : ''}`;
            button.textContent = item.text;
            button.addEventListener('click', () => {
                item.action();
                this.hideContextMenu();
            });
            contextMenu.appendChild(button);
        });
        
        // Position context menu
        const pos = event.renderedPosition || event.position;
        contextMenu.style.left = (pos.x + 10) + 'px';
        contextMenu.style.top = (pos.y + 10) + 'px';
    }
    
    hideContextMenu() {
        const contextMenu = document.querySelector('.graph-context-menu');
        if (contextMenu) {
            contextMenu.className = 'graph-context-menu hidden';
        }
    }
    
    showTooltip(event, node) {
        // Implementation for tooltip
    }
    
    hideTooltip() {
        // Implementation for hiding tooltip
    }
    
    // Modal functions (same as D3 version)
    showAddNodeModal() {
        this.populateParentSelect();
        document.getElementById('addNodeModal').classList.remove('hidden');
    }
    
    hideAddNodeModal() {
        document.getElementById('addNodeModal').classList.add('hidden');
        document.getElementById('newAgentName').value = '';
        document.getElementById('newAgentType').value = 'llm';
        document.getElementById('newAgentParent').value = '';
    }
    
    populateParentSelect() {
        const select = document.getElementById('newAgentParent');
        select.innerHTML = '<option value="">No parent (root agent)</option>';
        
        this.agents.forEach(agent => {
            const option = document.createElement('option');
            option.value = agent.name;
            option.textContent = agent.name;
            select.appendChild(option);
        });
    }
    
    async createNewAgent() {
        const name = document.getElementById('newAgentName').value.trim();
        const type = document.getElementById('newAgentType').value;
        const parent = document.getElementById('newAgentParent').value;
        
        if (!name) {
            alert('Please enter an agent name');
            return;
        }
        
        if (this.agents.find(a => a.name === name)) {
            alert('An agent with this name already exists');
            return;
        }
        
        // Show loading state
        const submitBtn = document.getElementById('createNewAgentBtn');
        const originalText = submitBtn.textContent;
        submitBtn.disabled = true;
        submitBtn.textContent = 'Creating...';
        
        try {
            // Create agent via API
            if (!ensureProjectId()) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
                return;
            }
            
            const formData = new FormData();
            if (!appendProjectToFormData(formData)) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
                return;
            }
            formData.append('name', name);
            formData.append('type', type);
            formData.append('description', '');
            formData.append('instruction', '');
            formData.append('allowed_for_roles', '["user", "admin"]');
            formData.append('tool_config', '{}');
            formData.append('mcp_servers_config', '{}');
            formData.append('planner_config', '{}');
            formData.append('generate_content_config', '{}');
            formData.append('input_schema', '{}');
            formData.append('output_schema', '{}');
            formData.append('include_contents', '');
            formData.append('max_iterations', '');
            formData.append('disabled', 'false');
            
            if (parent) {
                formData.append('parent_agents', JSON.stringify([parent]));
            }
            
            const response = await fetch('/dashboard/api/agents', {
                method: 'POST',
                credentials: 'same-origin',
                body: formData
            });
            
            const result = await response.json();
            
            if (result.success) {
                // Try different response structures
                let newAgent = result.agent || result.data || result.config;
                
                // If no agent data returned, we need to fetch all agents from server
                // to get the newly created agent with its ID
                if (!newAgent || !newAgent.id) {
                    
                    // Wait a bit for the database transaction to complete
                    await new Promise(resolve => setTimeout(resolve, 500));
                    
                    try {
                        const allAgentsResponse = await fetch(buildAgentsListUrl(), {
                            method: 'GET',
                            credentials: 'same-origin'
                        });
                        
                        
                        if (!allAgentsResponse.ok) {
                            console.error('Failed to fetch agents, status:', allAgentsResponse.status);
                            alert('Agent was created but could not retrieve data. Please refresh the page.');
                            return;
                        }
                        
                        const allAgentsResult = await allAgentsResponse.json();
                        
                        // The response can have either 'agents' or 'configs' property
                        const agentsList = allAgentsResult.agents || allAgentsResult.configs;
                        
                        if (agentsList && Array.isArray(agentsList)) {
                            
                            // Update global configs array with fresh data
                            if (typeof configs !== 'undefined') {
                                configs.length = 0;
                                configs.push(...agentsList);
                            }
                            
                            // Find the newly created agent by name
                            newAgent = agentsList.find(a => a.name === name);
                            
                            if (!newAgent) {
                                console.error('Could not find newly created agent:', name);
                                console.error('Available agent names:', agentsList.map(a => a.name));
                                alert('Agent was created but could not be found. Please refresh the page.');
                                return;
                            }
                            
                        } else {
                            console.error('Failed to fetch agents from server');
                            console.error('Result success:', allAgentsResult.success);
                            console.error('Result structure:', Object.keys(allAgentsResult));
                            alert('Agent was created but could not retrieve data. Please refresh the page.');
                            return;
                        }
                    } catch (fetchError) {
                        console.error('Error fetching agents:', fetchError);
                        console.error('Error details:', fetchError.message, fetchError.stack);
                        alert('Agent was created but could not retrieve data. Please refresh the page.');
                        return;
                    }
                }
                
                // Update local agents array if not already present
                const existingIndex = this.agents.findIndex(a => a.id === newAgent.id);
                if (existingIndex === -1) {
                    this.agents.push(newAgent);
                } else {
                    this.agents[existingIndex] = newAgent;
                }
                
                // Update global configs array if not already present
                if (typeof configs !== 'undefined') {
                    const configIndex = configs.findIndex(c => c.id === newAgent.id);
                    if (configIndex === -1) {
                        configs.push(newAgent);
                    } else {
                        configs[configIndex] = newAgent;
                    }
                }
                
                // Mark that agents were modified
                this.agentsModified = true;
                
                // Refresh graph
                this.loadAgents(this.agents);
                this.hideAddNodeModal();
                
                if (typeof showNotification === 'function') {
                    showNotification('Agent created successfully');
                }
            } else {
                console.error('Failed to create agent:', result);
                alert('Failed to create agent: ' + (result.message || 'Unknown error'));
            }
        } catch (error) {
            console.error('Error creating agent:', error);
            alert('Error creating agent: ' + error.message);
        } finally {
            // Restore button state
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
        }
    }
    
    startConnection(node) {
        this.connectionStart = node;
        this.populateConnectionSelect(node);
        document.getElementById('connectionFrom').value = node.data('label');
        document.getElementById('connectionModal').classList.remove('hidden');
    }
    
    hideConnectionModal() {
        document.getElementById('connectionModal').classList.add('hidden');
        this.connectionStart = null;
    }
    
    populateConnectionSelect(excludeNode) {
        const select = document.getElementById('connectionTo');
        select.innerHTML = '<option value="">Select target agent...</option>';
        
        this.agents.forEach(agent => {
            if (agent.id !== excludeNode.data('id')) {
                const option = document.createElement('option');
                option.value = agent.name;
                option.textContent = agent.name;
                select.appendChild(option);
            }
        });
    }
    
    createConnection() {
        const targetName = document.getElementById('connectionTo').value;
        if (!targetName || !this.connectionStart) return;
        
        const targetAgent = this.agents.find(a => a.name === targetName);
        if (!targetAgent) return;
        
        
        if (!targetAgent.parent_agents) {
            targetAgent.parent_agents = [];
        }
        if (!targetAgent.parent_agents.includes(this.connectionStart.data('label'))) {
            targetAgent.parent_agents.push(this.connectionStart.data('label'));
            
            // Track that this agent was modified
            if (targetAgent.id && !targetAgent.id.toString().startsWith('agent_')) {
                this.modifiedAgentIds.add(targetAgent.id);
            }
            
            // Update global configs array
            if (typeof configs !== 'undefined') {
                const configAgent = configs.find(c => c.id == targetAgent.id);
                if (configAgent) {
                    if (!configAgent.parent_agents) {
                        configAgent.parent_agents = [];
                    }
                    if (!configAgent.parent_agents.includes(this.connectionStart.data('label'))) {
                        configAgent.parent_agents.push(this.connectionStart.data('label'));
                    }
                }
            }
            
            this.agentsModified = true;
        }
        
        this.loadAgents(this.agents);
        this.hideConnectionModal();
    }
    
    deleteNode(node) {
        if (confirm(`Are you sure you want to delete agent "${node.data('label')}"?`)) {
            const agentId = node.data('id');
            const agentName = node.data('label');
            
            
            // Track deleted agent ID (only if it's not a temporary new agent)
            if (agentId && !agentId.toString().startsWith('agent_')) {
                this.deletedAgentIds.push(agentId);
            }
            
            // Remove from local agents array (use == for loose comparison to handle string/number mismatch)
            const beforeLength = this.agents.length;
            this.agents = this.agents.filter(a => a.id != agentId); // Note: using != instead of !==
            
            // Update parent references in remaining agents
            this.agents.forEach(agent => {
                if (agent.parent_agents) {
                    agent.parent_agents = agent.parent_agents.filter(p => p !== agentName);
                }
            });
            
            // Also update global configs array immediately for UI consistency
            if (typeof configs !== 'undefined') {
                const configIndex = configs.findIndex(c => c.id == agentId); // Note: using == instead of ===
                if (configIndex !== -1) {
                    configs.splice(configIndex, 1);
                } else {
                    console.warn('Agent not found in global configs array');
                }
                
                // Update parent references in global configs
                configs.forEach(agent => {
                    if (agent.parent_agents) {
                        agent.parent_agents = agent.parent_agents.filter(p => p !== agentName);
                    }
                });
            }
            
            this.agentsModified = true;
            this.loadAgents(this.agents);
            
        }
    }
    
    deleteEdge(edge) {
        if (confirm(`Are you sure you want to delete the connection between "${edge.data('sourceName')}" and "${edge.data('targetName')}"?`)) {
            const targetAgent = this.agents.find(a => a.id == edge.data('target')); // Use == for type safety
            if (targetAgent && targetAgent.parent_agents) {
                
                targetAgent.parent_agents = targetAgent.parent_agents.filter(p => p !== edge.data('sourceName'));
                
                // Track that this agent was modified
                if (targetAgent.id && !targetAgent.id.toString().startsWith('agent_')) {
                    this.modifiedAgentIds.add(targetAgent.id);
                }
                
                // Update global configs array
                if (typeof configs !== 'undefined') {
                    const configAgent = configs.find(c => c.id == targetAgent.id);
                    if (configAgent && configAgent.parent_agents) {
                        configAgent.parent_agents = configAgent.parent_agents.filter(p => p !== edge.data('sourceName'));
                    }
                }
                
                this.agentsModified = true;
            }
            this.loadAgents(this.agents);
        }
    }
    
    // Zoom functions
    zoomIn() {
        this.cy.zoom(this.cy.zoom() * 1.2);
    }
    
    zoomOut() {
        this.cy.zoom(this.cy.zoom() / 1.2);
    }
    
    resetZoom() {
        this.cy.fit();
    }
    
    // Filter functions
    filterNodes(searchTerm) {
        if (!searchTerm.trim()) {
            this.filteredAgents = [...this.agents];
        } else {
            const term = searchTerm.toLowerCase();
            this.filteredAgents = this.agents.filter(agent => 
                agent.name.toLowerCase().includes(term) ||
                agent.type.toLowerCase().includes(term) ||
                (agent.description && agent.description.toLowerCase().includes(term))
            );
        }
        
        this.initializeCytoscape();
        this.updateStats();
    }
    
    // Utility functions
    updateStats() {
        const nodeCount = this.cy ? this.cy.nodes().length : 0;
        const edgeCount = this.cy ? this.cy.edges().length : 0;
        
        document.getElementById('nodeCount').textContent = nodeCount;
        document.getElementById('edgeCount').textContent = edgeCount;
    }
    
    updateSelectedNodeInfo(node = null, edge = null) {
        const selectedElement = document.getElementById('selectedNode');
        if (node) {
            selectedElement.textContent = `Selected: ${node.data('label')} (${node.data('type')})`;
        } else if (edge) {
            selectedElement.textContent = `Selected: ${edge.data('sourceName')} → ${edge.data('targetName')}`;
        } else {
            selectedElement.textContent = 'No node selected';
        }
    }
    
    handleKeyboard(event) {
        if (event.key === 'Delete' && this.selectedNode) {
            this.deleteNode(this.selectedNode);
        } else if (event.key === 'Delete' && this.selectedEdge) {
            this.deleteEdge(this.selectedEdge);
        } else if (event.key === 'Escape') {
            this.clearSelection();
            this.hideContextMenu();
        }
    }
    
    async saveGraph() {
        
        // Show loading state
        const saveBtn = document.getElementById('saveGraphBtn');
        const originalText = saveBtn ? saveBtn.innerHTML : '';
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Saving...';
        }
        
        try {
            // First, handle deleted agents
            if (this.deletedAgentIds.length > 0) {
                
                for (const agentId of this.deletedAgentIds) {
                    try {
                        const response = await fetch(`/dashboard/api/agents/${agentId}`, {
                            method: 'DELETE',
                            credentials: 'same-origin'
                        });
                        
                        const result = await response.json();
                        
                        if (result.success) {
                            
                            // Remove from global configs array (already done in deleteNode, but double-check)
                            if (typeof configs !== 'undefined') {
                                const configIndex = configs.findIndex(c => c.id == agentId); // Use == for type safety
                                if (configIndex !== -1) {
                                    configs.splice(configIndex, 1);
                                }
                            }
                        } else {
                            console.error('Failed to delete agent:', agentId, result.message);
                            alert(`Failed to delete agent: ${result.message || 'Unknown error'}`);
                        }
                    } catch (error) {
                        console.error('Error deleting agent:', agentId, error);
                        alert(`Error deleting agent: ${error.message}`);
                    }
                }
                
                // Clear the deleted agents list
                this.deletedAgentIds = [];
            } else {
            }
            
            // Check if there are any new agents that need to be created
            const newAgents = this.agents.filter(agent => 
                agent.id && agent.id.toString().startsWith('agent_')
            );
            
            if (newAgents.length > 0) {
                
                for (const agent of newAgents) {
                    try {
                        const formData = new FormData();
                        if (!appendProjectToFormData(formData)) {
                            console.warn('Project ID missing; skipping agent creation for graph editor import.');
                            continue;
                        }
                        if (!appendProjectToFormData(formData)) {
                            console.warn('Project ID missing; skipping agent update for graph editor.');
                            continue;
                        }
                        formData.append('name', agent.name);
                        formData.append('type', agent.type);
                        formData.append('model_name', agent.model_name || '');
                        formData.append('description', agent.description || '');
                        formData.append('instruction', agent.instruction || '');
                        
                        // Handle JSON fields - only send if they have actual values
                        const allowedRoles = agent.allowed_for_roles;
                        if (allowedRoles && typeof allowedRoles === 'string') {
                            formData.append('allowed_for_roles', allowedRoles);
                        } else if (allowedRoles && typeof allowedRoles === 'object') {
                            formData.append('allowed_for_roles', JSON.stringify(allowedRoles));
                        } else {
                            formData.append('allowed_for_roles', '["user", "admin"]');
                        }
                        
                        // Only append JSON fields if they have actual content
                        if (agent.tool_config && agent.tool_config !== '{}' && agent.tool_config !== '') {
                            formData.append('tool_config', typeof agent.tool_config === 'string' ? agent.tool_config : JSON.stringify(agent.tool_config));
                        }
                        if (agent.mcp_servers_config && agent.mcp_servers_config !== '{}' && agent.mcp_servers_config !== '') {
                            formData.append('mcp_servers_config', typeof agent.mcp_servers_config === 'string' ? agent.mcp_servers_config : JSON.stringify(agent.mcp_servers_config));
                        }
                        if (agent.planner_config && agent.planner_config !== '{}' && agent.planner_config !== '') {
                            formData.append('planner_config', typeof agent.planner_config === 'string' ? agent.planner_config : JSON.stringify(agent.planner_config));
                        }
                        if (agent.generate_content_config && agent.generate_content_config !== '{}' && agent.generate_content_config !== '') {
                            formData.append('generate_content_config', typeof agent.generate_content_config === 'string' ? agent.generate_content_config : JSON.stringify(agent.generate_content_config));
                        }
                        if (agent.input_schema && agent.input_schema !== '{}' && agent.input_schema !== '') {
                            formData.append('input_schema', typeof agent.input_schema === 'string' ? agent.input_schema : JSON.stringify(agent.input_schema));
                        }
                        if (agent.output_schema && agent.output_schema !== '{}' && agent.output_schema !== '') {
                            formData.append('output_schema', typeof agent.output_schema === 'string' ? agent.output_schema : JSON.stringify(agent.output_schema));
                        }
                        if (agent.include_contents && agent.include_contents !== '') {
                            formData.append('include_contents', typeof agent.include_contents === 'string' ? agent.include_contents : JSON.stringify(agent.include_contents));
                        }
                        if (agent.max_iterations) {
                            formData.append('max_iterations', agent.max_iterations);
                        }
                        
                        formData.append('disabled', agent.disabled || false);
                        
                        if (agent.parent_agents && agent.parent_agents.length > 0) {
                            formData.append('parent_agents', JSON.stringify(agent.parent_agents));
                        }
                        
                        const response = await fetch('/dashboard/api/agents', {
                            method: 'POST',
                            credentials: 'same-origin',
                            body: formData
                        });
                        
                        const result = await response.json();
                        
                        if (result.success) {
                            const createdAgent = result.agent || result.data;
                            
                            // Update the agent in our local array with the real ID
                            const agentIndex = this.agents.findIndex(a => a.id == agent.id); // Use == for type safety
                            if (agentIndex !== -1) {
                                this.agents[agentIndex] = createdAgent;
                            }
                            
                            // Update global configs array
                            if (typeof configs !== 'undefined') {
                                const configIndex = configs.findIndex(c => c.id == agent.id); // Use == for type safety
                                if (configIndex !== -1) {
                                    configs[configIndex] = createdAgent;
                                } else {
                                    configs.push(createdAgent);
                                }
                            }
                        } else {
                            console.error('Failed to create agent:', agent.name, result.message);
                        }
                    } catch (error) {
                        console.error('Error creating agent:', agent.name, error);
                    }
                }
                
                // Refresh the graph with updated data
                this.loadAgents(this.agents);
            }
            
            // Handle modified agents (connection changes)
            if (this.modifiedAgentIds.size > 0) {
                
                for (const agentId of this.modifiedAgentIds) {
                    try {
                        const agent = this.agents.find(a => a.id == agentId);
                        if (!agent) {
                            console.warn('Modified agent not found:', agentId);
                            continue;
                        }
                        
                        
                        const formData = new FormData();
                        formData.append('name', agent.name);
                        formData.append('type', agent.type || 'llm');
                        formData.append('model_name', agent.model_name || '');
                        formData.append('description', agent.description || '');
                        formData.append('instruction', agent.instruction || '');
                        
                        // Handle JSON fields - only send if they have actual values
                        const allowedRoles = agent.allowed_for_roles;
                        if (allowedRoles && typeof allowedRoles === 'string') {
                            formData.append('allowed_for_roles', allowedRoles);
                        } else if (allowedRoles && typeof allowedRoles === 'object') {
                            formData.append('allowed_for_roles', JSON.stringify(allowedRoles));
                        } else {
                            formData.append('allowed_for_roles', '["user", "admin"]');
                        }
                        
                        // Only append JSON fields if they have actual content
                        if (agent.tool_config && agent.tool_config !== '{}' && agent.tool_config !== '') {
                            formData.append('tool_config', typeof agent.tool_config === 'string' ? agent.tool_config : JSON.stringify(agent.tool_config));
                        }
                        if (agent.mcp_servers_config && agent.mcp_servers_config !== '{}' && agent.mcp_servers_config !== '') {
                            formData.append('mcp_servers_config', typeof agent.mcp_servers_config === 'string' ? agent.mcp_servers_config : JSON.stringify(agent.mcp_servers_config));
                        }
                        if (agent.planner_config && agent.planner_config !== '{}' && agent.planner_config !== '') {
                            formData.append('planner_config', typeof agent.planner_config === 'string' ? agent.planner_config : JSON.stringify(agent.planner_config));
                        }
                        if (agent.generate_content_config && agent.generate_content_config !== '{}' && agent.generate_content_config !== '') {
                            formData.append('generate_content_config', typeof agent.generate_content_config === 'string' ? agent.generate_content_config : JSON.stringify(agent.generate_content_config));
                        }
                        if (agent.input_schema && agent.input_schema !== '{}' && agent.input_schema !== '') {
                            formData.append('input_schema', typeof agent.input_schema === 'string' ? agent.input_schema : JSON.stringify(agent.input_schema));
                        }
                        if (agent.output_schema && agent.output_schema !== '{}' && agent.output_schema !== '') {
                            formData.append('output_schema', typeof agent.output_schema === 'string' ? agent.output_schema : JSON.stringify(agent.output_schema));
                        }
                        if (agent.include_contents && agent.include_contents !== '') {
                            formData.append('include_contents', typeof agent.include_contents === 'string' ? agent.include_contents : JSON.stringify(agent.include_contents));
                        }
                        if (agent.max_iterations) {
                            formData.append('max_iterations', agent.max_iterations);
                        }
                        
                        formData.append('disabled', agent.disabled || false);
                        
                        if (agent.parent_agents && agent.parent_agents.length > 0) {
                            formData.append('parent_agents', JSON.stringify(agent.parent_agents));
                        } else {
                            formData.append('parent_agents', JSON.stringify([]));
                        }
                        
                        const response = await fetch(`/dashboard/api/agents/${agentId}`, {
                            method: 'PUT',
                            credentials: 'same-origin',
                            body: formData
                        });
                        
                        const result = await response.json();
                        
                        if (result.success) {
                        } else {
                            console.error('Failed to update agent:', agent.name, result.message);
                            alert(`Failed to update agent ${agent.name}: ${result.message || 'Unknown error'}`);
                        }
                    } catch (error) {
                        console.error('Error updating agent:', agentId, error);
                        alert(`Error updating agent: ${error.message}`);
                    }
                }
                
                // Clear the modified agents set
                this.modifiedAgentIds.clear();
            } else {
            }
            
            // Reset modification flag after successful save
            this.agentsModified = false;
            
            // Mark that changes were saved (need reload on close)
            this.changesSaved = true;
            
            if (typeof showNotification === 'function') {
                showNotification('Graph changes saved successfully');
            }
        } catch (error) {
            console.error('Error saving graph:', error);
            if (typeof showNotification === 'function') {
                showNotification('Error saving graph changes', 'error');
            }
            alert(`Error saving graph: ${error.message}`);
        } finally {
            // Restore button state
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.innerHTML = originalText;
            }
        }
    }
    
    // Method to refresh graph data after agent update
    // Pass updatedAgentId to update only that specific node, or omit to update all
    refreshGraphData(updatedAgentId = null) {
        
        if (!this.cy) {
            console.warn('Cytoscape instance not available');
            return;
        }
        
        // Clear any pending changes when refreshing from server
        if (!updatedAgentId) {
            this.deletedAgentIds = [];
            this.modifiedAgentIds.clear();
        }
        
        // Reload agents from the main configs array
        if (typeof configs !== 'undefined' && configs.length > 0) {
            const rootAgentFilter = document.getElementById('rootAgentFilter');
            const selectedRootAgent = rootAgentFilter ? rootAgentFilter.value : '';
            
            if (selectedRootAgent) {
                const filteredAgents = getAgentsForRootAgent(selectedRootAgent, configs);
                
                // Update the local agents array
                this.agents = filteredAgents;
                
                // If we have a specific agent ID, only update that one node
                if (updatedAgentId) {
                    const updatedAgent = filteredAgents.find(a => a.id == updatedAgentId);
                    if (updatedAgent) {
                        this.updateSingleNode(updatedAgent);
                    } else {
                        console.warn('Agent not found in filtered agents, doing full refresh');
                        this.updateExistingNodes(filteredAgents);
                    }
                } else {
                    // Update all nodes
                    this.updateExistingNodes(filteredAgents);
                }
                
            }
        }
    }
    
    // Method to update a single node without re-rendering the graph
    updateSingleNode(updatedAgent) {
        if (!this.cy) return;
        
        
        // Find the node in the graph
        const node = this.cy.getElementById(updatedAgent.id);
        
        if (node && node.length > 0) {
            // Update node data
            node.data({
                'id': updatedAgent.id,
                'label': updatedAgent.name,
                'type': updatedAgent.type || 'llm',
                'disabled': updatedAgent.disabled || false,
                'root': updatedAgent.root || false
            });
            
            // Update node style classes
            const newClasses = this.getNodeClasses(updatedAgent);
            // Remove all existing classes first
            const currentClasses = node.classes();
            if (currentClasses && currentClasses.length > 0) {
                currentClasses.forEach(cls => {
                    if (cls) node.removeClass(cls);
                });
            }
            // Add new classes
            if (newClasses) {
                node.addClass(newClasses);
            }
            
            // Check if parent relationships changed
            const currentEdges = node.connectedEdges().filter(edge => edge.target().id() === updatedAgent.id);
            const currentParents = currentEdges.map(edge => edge.source().data('label'));
            const newParents = updatedAgent.parent_agents || [];
            
            // Only update edges if parent relationships changed
            const parentsChanged = 
                currentParents.length !== newParents.length ||
                !currentParents.every(p => newParents.includes(p)) ||
                !newParents.every(p => currentParents.includes(p));
            
            if (parentsChanged) {
                
                // Remove old edges to this node
                currentEdges.remove();
                
                // Add new edges
                newParents.forEach(parentName => {
                    const parentAgent = this.agents.find(a => a.name === parentName);
                    if (parentAgent) {
                        this.cy.add({
                            data: {
                                id: `${parentAgent.id}-${updatedAgent.id}`,
                                source: parentAgent.id,
                                target: updatedAgent.id,
                                sourceName: parentAgent.name,
                                targetName: updatedAgent.name
                            }
                        });
                    }
                });
                
                // Don't run layout - let the graph maintain its current positions
            } else {
            }
            
            
            // Update stats
            this.updateStats();
        } else {
            console.warn('Node not found in graph:', updatedAgent.id);
            // Node doesn't exist, might be a new node - do a full refresh
            this.updateExistingNodes(this.agents);
        }
    }
    
    // Method to update existing nodes with new data
    updateExistingNodes(updatedAgents) {
        if (!this.cy) return;
        
        
        // Create a map of updated agents for quick lookup
        const agentMap = new Map();
        updatedAgents.forEach(agent => {
            agentMap.set(agent.id, agent);
        });
        
        // Update existing nodes
        this.cy.nodes().forEach(node => {
            const nodeId = node.data('id');
            const updatedAgent = agentMap.get(nodeId);
            
            if (updatedAgent) {
                // Update node data
                node.data({
                    'id': updatedAgent.id,
                    'label': updatedAgent.name,
                    'type': updatedAgent.type || 'llm',
                    'disabled': updatedAgent.disabled || false,
                    'root': updatedAgent.root || false
                });
                
                // Update node style classes
                const classes = this.getNodeClasses(updatedAgent);
                node.removeClass();
                node.addClass(classes);
                
            }
        });
        
        // Update edges based on new parent relationships (preserve layout on refresh)
        this.updateEdges(updatedAgents, true); // true = preserve layout
        
        // Update stats
        this.updateStats();
    }
    
    // Method to update edges based on agent relationships
    updateEdges(updatedAgents, preserveLayout = true) {
        if (!this.cy) return;
        
        
        // Clear existing edges
        this.cy.edges().remove();
        
        // Create new edges based on parent relationships
        const edges = [];
        updatedAgents.forEach(agent => {
            if (agent.parent_agents && Array.isArray(agent.parent_agents)) {
                agent.parent_agents.forEach(parentName => {
                    const parentAgent = updatedAgents.find(a => a.name === parentName);
                    if (parentAgent) {
                        edges.push({
                            data: {
                                id: `${parentAgent.id}-${agent.id}`,
                                source: parentAgent.id,
                                target: agent.id,
                                sourceName: parentAgent.name,
                                targetName: agent.name
                            }
                        });
                    }
                });
            }
        });
        
        // Add new edges to the graph
        if (edges.length > 0) {
            this.cy.add(edges);
        }
        
        // Only run layout if explicitly requested (not when refreshing)
        if (!preserveLayout) {
            this.cy.layout({
                name: 'dagre',
                rankDir: 'TB',
                rankSep: 100,
                nodeSep: 50,
                edgeSep: 10,
                ranker: 'tight-tree'
            }).run();
        } else {
        }
    }
    
    // Method to refresh the entire graph (reload from server)
    async refreshGraph() {
        
        try {
            // Show loading state
            const refreshBtn = document.getElementById('refreshGraphBtn');
            const originalText = refreshBtn.textContent;
            refreshBtn.disabled = true;
            refreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-1"></i>Refreshing...';
            
            if (!ensureProjectId()) {
                refreshBtn.disabled = false;
                refreshBtn.textContent = originalText;
                return;
            }
            
            // Fetch fresh data from server instead of reloading page
            const response = await fetch(buildAgentsListUrl(), {
                method: 'GET',
                credentials: 'same-origin'
            });
            
            if (response.ok) {
                const result = await response.json();
                
                // API returns {"configs": [...]} not {"success": true, "agents": [...]}
                const agentsList = result.configs || result.agents || [];
                
                if (agentsList && agentsList.length >= 0) {
                    // Update global configs array
                    if (typeof configs !== 'undefined') {
                        configs.length = 0; // Clear existing configs
                        configs.push(...agentsList); // Add new configs
                    }
                    
                    // Refresh graph data (without agent ID to refresh all)
                    this.refreshGraphData();
                    
                    if (typeof showNotification === 'function') {
                        showNotification('Graph refreshed successfully');
                    }
                } else {
                    console.error('Invalid response structure from server:', result);
                    if (typeof showNotification === 'function') {
                        showNotification('Failed to refresh graph data - invalid response', 'error');
                    }
                }
            } else {
                console.error('Failed to fetch agents from server, status:', response.status);
                const errorText = await response.text();
                console.error('Error response:', errorText);
                if (typeof showNotification === 'function') {
                    showNotification(`Failed to refresh graph data from server (${response.status})`, 'error');
                }
            }
        } catch (error) {
            console.error('Error refreshing graph:', error);
            if (typeof showNotification === 'function') {
                showNotification('Error refreshing graph', 'error');
            }
        } finally {
            // Restore button state
            const refreshBtn = document.getElementById('refreshGraphBtn');
            if (refreshBtn) {
                refreshBtn.disabled = false;
                refreshBtn.innerHTML = '<i class="fas fa-sync-alt mr-1"></i>Refresh';
            }
        }
    }
}

// Global functions for modal control
function showCytoscapeGraphEditor() {
    // Check if Cytoscape.js is loaded
    if (typeof cytoscape === 'undefined') {
        alert('Cytoscape.js library is not loaded. Please refresh the page and try again.');
        return;
    }
    
    try {
        document.getElementById('graphEditorModal').classList.remove('hidden');
        
        // Initialize graph editor if not already done
        if (!window.cytoscapeGraphEditor) {
            window.cytoscapeGraphEditor = new CytoscapeGraphEditor();
        }
        
        // Load agents data
        if (typeof configs !== 'undefined' && configs.length > 0) {
            const rootAgentFilter = document.getElementById('rootAgentFilter');
            const selectedRootAgent = rootAgentFilter ? rootAgentFilter.value : '';
            
            if (!selectedRootAgent) {
                alert('Please select a specific root agent to view its hierarchy in the graph editor.');
                return;
            }
            
            const filteredAgents = getAgentsForRootAgent(selectedRootAgent, configs);
            window.cytoscapeGraphEditor.loadAgents(filteredAgents);
        }
    } catch (error) {
        console.error('Error opening graph editor:', error);
        alert('Error opening graph editor. Please check the console for details.');
    }
}

async function hideCytoscapeGraphEditor() {
    const graphEditor = window.cytoscapeGraphEditor;
    let shouldReload = false;
    
    // Check if there are unsaved changes (deletions, new agents, or modified agents)
    if (graphEditor) {
        const hasDeletedAgents = graphEditor.deletedAgentIds && graphEditor.deletedAgentIds.length > 0;
        const hasNewAgents = graphEditor.agents && graphEditor.agents.some(a => a.id && a.id.toString().startsWith('agent_'));
        const hasModifiedAgents = graphEditor.modifiedAgentIds && graphEditor.modifiedAgentIds.size > 0;
        
        if (hasDeletedAgents || hasNewAgents || hasModifiedAgents) {
            
            // Auto-save before closing
            await graphEditor.saveGraph();
            
            // Mark that we need to reload since we auto-saved
            shouldReload = true;
        } else if (graphEditor.changesSaved) {
            // Changes were already saved via Save button
            shouldReload = true;
        }
        
        // Reset the changesSaved flag for next time
        graphEditor.changesSaved = false;
    }
    
    document.getElementById('graphEditorModal').classList.add('hidden');
    
    // Reload page if changes were made
    if (shouldReload) {
        location.reload();
    } else {
    }
}

function hideAddNodeModal() {
    if (window.cytoscapeGraphEditor) {
        window.cytoscapeGraphEditor.hideAddNodeModal();
    }
}

function hideConnectionModal() {
    if (window.cytoscapeGraphEditor) {
        window.cytoscapeGraphEditor.hideConnectionModal();
    }
}

// Helper function to get agents for a specific root agent
function getAgentsForRootAgent(rootAgentName, allAgents) {
    if (!rootAgentName || !allAgents) return [];
    
    const rootAgent = allAgents.find(agent => agent.name === rootAgentName);
    if (!rootAgent) return [];
    
    const result = [rootAgent];
    const visited = new Set([rootAgent.id]);
    
    function addChildren(agent) {
        const children = allAgents.filter(a => 
            a.parent_agents && 
            a.parent_agents.includes(agent.name) && 
            !visited.has(a.id)
        );
        
        children.forEach(child => {
            if (!visited.has(child.id)) {
                result.push(child);
                visited.add(child.id);
                addChildren(child);
            }
        });
    }
    
    addChildren(rootAgent);
    return result;
}

// Function to update graph editor button state
function updateGraphEditorButtonState() {
    const graphEditorBtn = document.getElementById('graphEditorBtn');
    const rootAgentFilter = document.getElementById('rootAgentFilter');
    
    
    if (!graphEditorBtn || !rootAgentFilter) {
        return;
    }
    
    const selectedRootAgent = rootAgentFilter.value;
    
    if (selectedRootAgent && selectedRootAgent !== '') {
        // Enable button when a specific root agent is selected
        graphEditorBtn.disabled = false;
        graphEditorBtn.classList.remove('bg-gray-400', 'cursor-not-allowed');
        graphEditorBtn.classList.add('bg-indigo-600', 'hover:bg-indigo-700');
    } else {
        // Disable button when "All agents" is selected or no selection
        graphEditorBtn.disabled = true;
        graphEditorBtn.classList.add('bg-gray-400', 'cursor-not-allowed');
        graphEditorBtn.classList.remove('bg-indigo-600', 'hover:bg-indigo-700');
    }
}
