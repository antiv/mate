/**
 * Agent Visual Builder - React Flow implementation
 * Vanilla JS + React/ReactDOM/ReactFlow UMD bundles.
 */

/* global window, document, fetch */

(function () {
    const React = window.React;
    const ReactDOM = window.ReactDOM;
    const RF = window.ReactFlow;

    if (!React || !ReactDOM || !RF) {
        console.error('[AgentVisualBuilder] React, ReactDOM, or ReactFlow not available on window.');
        return;
    }

    const {
        ReactFlow: ReactFlowComponent,
        MiniMap,
        Background,
        Controls,
        Handle,
        Position,
        MarkerType,
        addEdge,
        applyNodeChanges,
        applyEdgeChanges,
    } = RF;

    const { useState, useEffect, useCallback, useRef } = React;

    function readInitialState() {
        const state = window.visualBuilderInitialState || {};
        const configs = Array.isArray(state.configs) ? state.configs : [];
        const projects = Array.isArray(state.projects) ? state.projects : [];
        const selectedProjectId = state.selectedProjectId != null ? state.selectedProjectId : null;
        return { configs, projects, selectedProjectId };
    }

    function normalizeAgents(configs) {
        const byName = new Map();
        configs.forEach((cfg) => {
            if (!cfg || !cfg.name) {
                return;
            }
            const copy = Object.assign({}, cfg);
            if (!Array.isArray(copy.parent_agents)) {
                try {
                    if (typeof copy.parent_agents === 'string') {
                        copy.parent_agents = JSON.parse(copy.parent_agents);
                    }
                } catch (e) {
                    copy.parent_agents = [];
                }
            }
            if (!Array.isArray(copy.parent_agents)) {
                copy.parent_agents = [];
            }
            byName.set(copy.name, copy);
        });
        return byName;
    }

    function buildGraph(agentsByName) {
        const nodes = [];
        const edges = [];

        const parentsByName = new Map();

        agentsByName.forEach((agent, name) => {
            const parents = Array.isArray(agent.parent_agents) ? agent.parent_agents : [];
            parentsByName.set(name, parents);
        });

        const depthByName = new Map();

        function computeDepth(name, stack) {
            if (depthByName.has(name)) {
                return depthByName.get(name);
            }
            const inStack = stack && stack.indexOf(name) !== -1;
            if (inStack) {
                depthByName.set(name, 0);
                return 0;
            }
            const parents = parentsByName.get(name) || [];
            if (!parents.length) {
                depthByName.set(name, 0);
                return 0;
            }
            const nextStack = (stack || []).concat(name);
            let maxParentDepth = 0;
            parents.forEach((parentName) => {
                if (!agentsByName.has(parentName)) {
                    return;
                }
                const d = computeDepth(parentName, nextStack);
                if (d > maxParentDepth) {
                    maxParentDepth = d;
                }
            });
            const depth = maxParentDepth + 1;
            depthByName.set(name, depth);
            return depth;
        }

        agentsByName.forEach((_, name) => {
            computeDepth(name, []);
        });

        const levels = new Map();
        depthByName.forEach((depth, name) => {
            if (!levels.has(depth)) {
                levels.set(depth, []);
            }
            levels.get(depth).push(name);
        });

        const NODE_X_SPACING = 280;
        const NODE_Y_SPACING = 280;

        const positionByName = new Map();

        Array.from(levels.keys())
            .sort(function (a, b) {
                return a - b;
            })
            .forEach((depth) => {
                const names = levels.get(depth) || [];
                names.sort();
                const count = names.length;
                names.forEach((name, index) => {
                    const x = (index - (count - 1) / 2) * NODE_X_SPACING;
                    const y = depth * NODE_Y_SPACING;
                    positionByName.set(name, { x: x, y: y });
                });
            });

        agentsByName.forEach((agent, name) => {
            const isRoot = !agent.parent_agents || agent.parent_agents.length === 0;
            const type = (agent.type || 'llm').toLowerCase();
            const classes = [];
            classes.push(type);
            if (isRoot) {
                classes.push('root');
            }
            if (agent.disabled) {
                classes.push('disabled');
            }

            const position = positionByName.get(name) || { x: 0, y: 0 };

            nodes.push({
                id: name,
                data: {
                    label: name,
                    type: type,
                    isRoot: isRoot,
                    disabled: !!agent.disabled,
                    hardcoded: !!agent.hardcoded,
                },
                position: position,
                className: classes.join(' '),
            });
        });

        agentsByName.forEach((agent) => {
            if (agent.parent_agents && agent.parent_agents.length > 0) {
                agent.parent_agents.forEach((parentName) => {
                    if (!agentsByName.has(parentName)) {
                        return;
                    }
                    const edgeId = parentName + '__' + agent.name;
                    /* Use parent's color for the edge */
                    var parentAgent = agentsByName.get(parentName);
                    var parentType = (parentAgent && parentAgent.type || 'llm').toLowerCase();
                    var edgeColors = NODE_COLORS[parentType] || NODE_COLORS._default;
                    var edgeColor = edgeColors.border;
                    edges.push({
                        id: edgeId,
                        source: parentName,
                        target: agent.name,
                        type: 'smoothstep',
                        animated: true,
                        style: {
                            stroke: edgeColor,
                            strokeWidth: 2.5,
                        },
                        markerEnd: {
                            type: MarkerType.ArrowClosed,
                            color: edgeColor,
                            width: 22,
                            height: 22,
                        },
                    });
                });
            }
        });

        /* ── Tool & MCP child nodes (below agents, centered) ── */
        agentsByName.forEach(function (agent, agentName) {
            var agentPos = positionByName.get(agentName) || { x: 0, y: 0 };
            var ITEM_SPACING = 115;
            var TOOL_ROW_Y = agentPos.y + 130;

            var toolCfg;
            try { toolCfg = JSON.parse(agent.tool_config || '{}'); } catch (e) { toolCfg = {}; }
            var activeTools = TOOL_DEFS ? TOOL_DEFS.filter(function (t) { return !!toolCfg[t.key]; }) : [];

            activeTools.forEach(function (t, i) {
                var nodeId = '__tool__' + agentName + '__' + t.key;
                var xOffset = activeTools.length > 1 ? (i - (activeTools.length - 1) / 2) * ITEM_SPACING : 0;
                nodes.push({
                    id: nodeId,
                    type: 'tool',
                    position: { x: agentPos.x + xOffset, y: TOOL_ROW_Y },
                    data: { label: t.label, toolKey: t.key, agentName: agentName },
                    draggable: true,
                });
                edges.push({
                    id: '__tedge__' + agentName + '__' + t.key,
                    source: agentName,
                    target: nodeId,
                    type: 'straight',
                    style: { stroke: '#f59e0b', strokeWidth: 1.5, strokeDasharray: '5 3' },
                });
            });

            var mcpCfg;
            try { mcpCfg = JSON.parse(agent.mcp_servers_config || '{}'); } catch (e) { mcpCfg = {}; }
            var serverNames = Object.keys((mcpCfg && mcpCfg.mcpServers) || {});

            var mcpRowY = activeTools.length > 0 ? TOOL_ROW_Y + 46 : TOOL_ROW_Y;
            serverNames.forEach(function (serverName, i) {
                var nodeId = '__mcp__' + agentName + '__' + serverName;
                var xOffset = serverNames.length > 1 ? (i - (serverNames.length - 1) / 2) * ITEM_SPACING : 0;
                nodes.push({
                    id: nodeId,
                    type: 'mcp',
                    position: { x: agentPos.x + xOffset, y: mcpRowY },
                    data: { label: serverName, agentName: agentName },
                    draggable: true,
                });
                edges.push({
                    id: '__medge__' + agentName + '__' + serverName,
                    source: agentName,
                    target: nodeId,
                    type: 'straight',
                    style: { stroke: '#818cf8', strokeWidth: 1.5, strokeDasharray: '5 3' },
                });
            });
        });

        return { nodes, edges };
    }

    async function apiGetAgents(projectId) {
        const url = projectId
            ? '/dashboard/api/agents?project_id=' + encodeURIComponent(projectId)
            : '/dashboard/api/agents';
        const resp = await fetch(url, { method: 'GET', credentials: 'same-origin' });
        const data = await resp.json();
        const configs = Array.isArray(data.configs) ? data.configs : [];
        return configs;
    }

    function buildAgentFormData(agent) {
        const formData = new FormData();
        const projectId = agent.project_id || agent.projectId || agent.project || null;

        formData.append('name', agent.name || '');
        formData.append('type', agent.type || 'llm');
        formData.append('project_id', projectId != null ? String(projectId) : '');
        formData.append('model_name', agent.model_name || '');
        formData.append('description', agent.description || '');
        formData.append('instruction', agent.instruction || '');

        const parentAgents = Array.isArray(agent.parent_agents) ? agent.parent_agents : [];
        formData.append('parent_agents', JSON.stringify(parentAgents));

        const allowedRoles = agent.allowed_for_roles;
        if (allowedRoles && typeof allowedRoles === 'string') {
            formData.append('allowed_for_roles', allowedRoles);
        } else if (allowedRoles && typeof allowedRoles === 'object') {
            formData.append('allowed_for_roles', JSON.stringify(allowedRoles));
        } else {
            formData.append('allowed_for_roles', '["user","admin"]');
        }

        const jsonFields = [
            'tool_config',
            'mcp_servers_config',
            'planner_config',
            'generate_content_config',
            'input_schema',
            'output_schema',
            'include_contents',
            'guardrail_config',
        ];

        jsonFields.forEach((field) => {
            const value = agent[field];
            if (value === undefined || value === null || value === '') {
                formData.append(field, '');
            } else if (typeof value === 'string') {
                formData.append(field, value);
            } else {
                try {
                    formData.append(field, JSON.stringify(value));
                } catch (e) {
                    formData.append(field, '');
                }
            }
        });

        if (agent.max_iterations != null && agent.max_iterations !== '') {
            formData.append('max_iterations', String(agent.max_iterations));
        } else {
            formData.append('max_iterations', '');
        }

        formData.append('disabled', agent.disabled ? 'true' : 'false');
        formData.append('hardcoded', agent.hardcoded ? 'true' : 'false');

        return formData;
    }

    async function apiCreateAgent(agent) {
        const formData = buildAgentFormData(agent);
        const resp = await fetch('/dashboard/api/agents', {
            method: 'POST',
            credentials: 'same-origin',
            body: formData,
        });
        return resp.json();
    }

    async function apiUpdateAgent(agent) {
        if (!agent.id) {
            throw new Error('Cannot update agent without id');
        }
        const formData = buildAgentFormData(agent);
        const resp = await fetch('/dashboard/api/agents/' + encodeURIComponent(agent.id), {
            method: 'PUT',
            credentials: 'same-origin',
            body: formData,
        });
        return resp.json();
    }

    async function apiDeleteAgent(agentId) {
        const resp = await fetch('/dashboard/api/agents/' + encodeURIComponent(agentId), {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        return resp.json();
    }

    async function apiExportAgents(projectId) {
        const url = projectId
            ? '/dashboard/api/agents/export?project_id=' + encodeURIComponent(projectId)
            : '/dashboard/api/agents/export';
        const resp = await fetch(url, { method: 'GET', credentials: 'same-origin' });
        return resp.json();
    }

    async function apiImportAgents(jsonData, overwrite) {
        const body = Object.assign({ overwrite: !!overwrite }, jsonData);
        const resp = await fetch('/dashboard/api/agents/import', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(body),
        });
        return resp.json();
    }

    function downloadJson(filename, data) {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    }

    /* ── Color palette per agent type ── */
    var NODE_COLORS = {
        llm: { bg: '#1e40af', border: '#3b82f6', glow: 'rgba(59,130,246,0.35)' },
        sequential: { bg: '#065f46', border: '#10b981', glow: 'rgba(16,185,129,0.35)' },
        parallel: { bg: '#92400e', border: '#f59e0b', glow: 'rgba(245,158,11,0.35)' },
        loop: { bg: '#991b1b', border: '#ef4444', glow: 'rgba(239,68,68,0.35)' },
        _default: { bg: '#374151', border: '#6b7280', glow: 'rgba(107,114,128,0.3)' },
    };

    function getNodeColors(type) {
        var t = (type || 'llm').toLowerCase();
        return NODE_COLORS[t] || NODE_COLORS._default;
    }

    function DefaultAgentNode(props) {
        var nodeId = props.id;
        var data = props.data || {};
        var selected = props.selected;
        var type = data.type || 'llm';
        var label = data.label || '';
        var colors = getNodeColors(type);

        /* Override colors for special states */
        var bg = colors.bg;
        var border = colors.border;
        if (data.disabled) { bg = '#374151'; border = '#6b7280'; }
        if (data.hardcoded) { border = '#a78bfa'; }
        if (data.isRoot) { border = '#34d399'; }
        if (selected) { border = '#22d3ee'; }

        /* Main wrapper — large rounded rectangle like the mockup */
        return React.createElement(
            'div',
            {
                style: {
                    /* Size — wider & taller like the Excalidraw boxes */
                    minWidth: 180,
                    minHeight: 64,
                    padding: '18px 28px',
                    /* Shape */
                    borderRadius: 16,
                    borderWidth: selected ? 3 : 2,
                    borderStyle: 'solid',
                    borderColor: border,
                    backgroundColor: bg,
                    /* Text */
                    color: '#ffffff',
                    fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
                    textAlign: 'center',
                    /* Depth */
                    boxShadow: selected
                        ? '0 0 0 3px rgba(34,211,238,0.25), 0 8px 24px rgba(0,0,0,0.4)'
                        : '0 4px 16px ' + colors.glow + ', 0 2px 6px rgba(0,0,0,0.3)',
                    transition: 'all 0.25s ease',
                    position: 'relative',
                    cursor: 'pointer',
                },
                onClick: function (e) {
                    const activeTool = window.visualBuilderGetActiveTool ? window.visualBuilderGetActiveTool() : null;
                    if (activeTool === 'delete') {
                        e.stopPropagation();
                        e.preventDefault();
                        if (window.visualBuilderDeleteSpecific) {
                            window.visualBuilderDeleteSpecific(nodeId);
                        }
                    }
                }
            },
            /* ── Target handle (top) ── */
            React.createElement(Handle, {
                type: 'target',
                position: Position.Top,
                style: { background: border, width: 10, height: 10, border: '2px solid ' + bg, top: -6 },
            }),
            /* ── Agent name — big & centered ── */
            React.createElement(
                'div',
                {
                    style: {
                        fontSize: 17,
                        fontWeight: 700,
                        letterSpacing: '0.2px',
                        lineHeight: 1.3,
                        marginBottom: 6,
                        textShadow: '0 1px 3px rgba(0,0,0,0.3)',
                    },
                },
                label,
            ),
            /* ── Subtitle row: type + badges ── */
            React.createElement(
                'div',
                {
                    style: {
                        display: 'flex',
                        justifyContent: 'center',
                        alignItems: 'center',
                        gap: 6,
                        flexWrap: 'wrap',
                    },
                },
                /* Type pill */
                React.createElement('span', {
                    style: {
                        display: 'inline-block',
                        padding: '2px 10px',
                        borderRadius: 20,
                        fontSize: 10,
                        fontWeight: 600,
                        backgroundColor: 'rgba(255,255,255,0.15)',
                        color: 'rgba(255,255,255,0.8)',
                        textTransform: 'uppercase',
                        letterSpacing: '0.8px',
                        backdropFilter: 'blur(4px)',
                    },
                }, type),
                /* Root badge */
                data.isRoot ? React.createElement('span', {
                    style: {
                        display: 'inline-block',
                        padding: '2px 8px',
                        borderRadius: 20,
                        fontSize: 10,
                        fontWeight: 600,
                        backgroundColor: 'rgba(52,211,153,0.25)',
                        color: '#6ee7b7',
                    },
                }, '★ root') : null,
                /* Disabled badge */
                data.disabled ? React.createElement('span', {
                    style: {
                        display: 'inline-block',
                        padding: '2px 8px',
                        borderRadius: 20,
                        fontSize: 10,
                        fontWeight: 600,
                        backgroundColor: 'rgba(239,68,68,0.25)',
                        color: '#fca5a5',
                    },
                }, 'disabled') : null,
                /* Hardcoded badge */
                data.hardcoded ? React.createElement('span', {
                    style: {
                        display: 'inline-block',
                        padding: '2px 8px',
                        borderRadius: 20,
                        fontSize: 10,
                        fontWeight: 600,
                        backgroundColor: 'rgba(167,139,250,0.25)',
                        color: '#c4b5fd',
                    },
                }, 'HC') : null,
            ),
            /* ── Source handle (bottom) ── */
            React.createElement(Handle, {
                type: 'source',
                position: Position.Bottom,
                style: { background: border, width: 10, height: 10, border: '2px solid ' + bg, bottom: -6 },
            }),
        );
    }

    function ToolNode(props) {
        var nodeId = props.id;
        var data = props.data || {};
        var selected = props.selected;
        return React.createElement(
            'div',
            {
                style: {
                    padding: '4px 10px', borderRadius: 20,
                    border: selected ? '2px solid #fcd34d' : '1.5px solid #f59e0b',
                    backgroundColor: selected ? '#78350f' : '#451a03',
                    color: '#fde68a', fontSize: 10, fontWeight: 600,
                    fontFamily: "'Inter', sans-serif", whiteSpace: 'nowrap',
                    cursor: 'pointer',
                    boxShadow: selected ? '0 0 0 3px rgba(252,211,77,0.3)' : 'none',
                    transition: 'all 0.15s ease',
                },
                onClick: function (e) {
                    var activeTool = window.visualBuilderGetActiveTool ? window.visualBuilderGetActiveTool() : null;
                    if (activeTool === 'delete') {
                        e.stopPropagation();
                        if (window.visualBuilderDeleteSpecific) window.visualBuilderDeleteSpecific(nodeId);
                    } else {
                        e.stopPropagation();
                        if (window.visualBuilderSelectAttachment) window.visualBuilderSelectAttachment(nodeId, 'tool', data);
                    }
                },
            },
            React.createElement(Handle, { type: 'target', position: Position.Top, style: { background: '#f59e0b', width: 6, height: 6 } }),
            '⚙ ' + data.label,
        );
    }

    function McpNode(props) {
        var nodeId = props.id;
        var data = props.data || {};
        var selected = props.selected;
        return React.createElement(
            'div',
            {
                style: {
                    padding: '4px 10px', borderRadius: 20,
                    border: selected ? '2px solid #a5b4fc' : '1.5px solid #818cf8',
                    backgroundColor: selected ? '#312e81' : '#1e1b4b',
                    color: '#c4b5fd', fontSize: 10, fontWeight: 600,
                    fontFamily: "'Inter', sans-serif", whiteSpace: 'nowrap',
                    cursor: 'pointer',
                    boxShadow: selected ? '0 0 0 3px rgba(165,180,252,0.3)' : 'none',
                    transition: 'all 0.15s ease',
                },
                onClick: function (e) {
                    var activeTool = window.visualBuilderGetActiveTool ? window.visualBuilderGetActiveTool() : null;
                    if (activeTool === 'delete') {
                        e.stopPropagation();
                        if (window.visualBuilderDeleteSpecific) window.visualBuilderDeleteSpecific(nodeId);
                    } else {
                        e.stopPropagation();
                        if (window.visualBuilderSelectAttachment) window.visualBuilderSelectAttachment(nodeId, 'mcp', data);
                    }
                },
            },
            React.createElement(Handle, { type: 'target', position: Position.Top, style: { background: '#818cf8', width: 6, height: 6 } }),
            '⬡ ' + data.label,
        );
    }

    /* ── ToolConfigPanel ── shown in right panel when a tool node is selected */
    function ToolConfigPanel(props) {
        var attachment = props.attachment; // { nodeId, type:'tool', agentName, toolKey, label }
        var agent = props.agent;
        var onClose = props.onClose;
        var onOpenToolPicker = props.onOpenToolPicker; // fn(agentName)

        if (!attachment || !agent) return null;

        var toolConfig = parseJson(agent.tool_config);
        var toolValue = toolConfig[attachment.toolKey];
        var isEnabled = toolValue !== undefined && toolValue !== false;
        var valuePreview = isEnabled
            ? (typeof toolValue === 'object' ? JSON.stringify(toolValue) : 'true')
            : '—';

        return React.createElement(
            'div',
            { className: 'h-full flex flex-col text-xs text-gray-900 dark:text-gray-100 gap-3' },
            /* Header */
            React.createElement(
                'div',
                { className: 'flex items-center gap-2 pb-2 border-b border-gray-200 dark:border-gray-700' },
                React.createElement('button', { onClick: onClose, className: 'text-[11px] text-gray-400 hover:text-gray-200' }, '← Back'),
                React.createElement('span', { style: { fontSize: 13, fontWeight: 700, color: '#fde68a' } }, '⚙ ' + attachment.label),
            ),
            React.createElement('div', { className: 'text-[11px] text-gray-400' },
                'Agent: ', React.createElement('span', { className: 'text-gray-300 font-semibold' }, attachment.agentName),
            ),
            /* Status */
            React.createElement('div', { className: 'border border-gray-600 rounded p-2 space-y-1' },
                React.createElement('div', { className: 'flex items-center gap-2' },
                    React.createElement('span', { className: isEnabled ? 'text-green-400 font-semibold text-[11px]' : 'text-gray-500 text-[11px]' },
                        isEnabled ? '✓ Enabled' : '✗ Disabled'),
                ),
                isEnabled && typeof toolValue === 'object' && React.createElement('p', {
                    className: 'text-[10px] font-mono text-gray-400 break-all',
                }, valuePreview),
            ),
            /* Open tool picker modal */
            React.createElement('button', {
                type: 'button',
                className: 'w-full px-3 py-2 text-[12px] font-semibold bg-yellow-600 hover:bg-yellow-500 text-white rounded flex items-center justify-center gap-2 disabled:opacity-50',
                onClick: function () { if (onOpenToolPicker) onOpenToolPicker(attachment.agentName); },
                disabled: agent.hardcoded,
                title: agent.hardcoded ? 'Hardcoded agents cannot be modified' : '',
            },
                React.createElement('i', { className: 'fas fa-sliders-h' }),
                'Configure Tools',
            ),
            agent.hardcoded && React.createElement('p', { className: 'text-[10px] text-yellow-500' }, 'Hardcoded agents cannot be modified.'),
        );
    }

    /* ── McpConfigPanel ── shown in right panel when an MCP node is selected */
    function McpConfigPanel(props) {
        var attachment = props.attachment; // { nodeId, type:'mcp', agentName, serverName: label }
        var agent = props.agent;
        var onClose = props.onClose;
        var onSaveAgent = props.onSaveAgent;
        var saving = props.saving;

        var serverName = attachment ? attachment.label : '';
        var agentMcp = agent ? parseJson(agent.mcp_servers_config) : {};
        var serverCfg = (agentMcp.mcpServers || {})[serverName] || {};

        var [rawJson, setRawJson] = useState(JSON.stringify(serverCfg, null, 2));
        var [jsonError, setJsonError] = useState('');

        useEffect(function () {
            if (!attachment || !agent) return;
            var cfg = parseJson(agent.mcp_servers_config);
            var srv = (cfg.mcpServers || {})[attachment.label] || {};
            setRawJson(JSON.stringify(srv, null, 2));
            setJsonError('');
        }, [attachment && attachment.nodeId, agent && agent.mcp_servers_config]);

        if (!attachment || !agent) return null;

        var handleSave = function () {
            var parsed;
            try { parsed = JSON.parse(rawJson); setJsonError(''); } catch (e) { setJsonError('Invalid JSON: ' + e.message); return; }
            var allMcp = parseJson(agent.mcp_servers_config);
            if (!allMcp.mcpServers) allMcp.mcpServers = {};
            allMcp.mcpServers[serverName] = parsed;
            onSaveAgent(Object.assign({}, agent, { mcp_servers_config: JSON.stringify(allMcp) }));
        };

        return React.createElement(
            'div',
            { className: 'h-full flex flex-col text-xs text-gray-900 dark:text-gray-100 gap-2' },
            /* Header */
            React.createElement(
                'div',
                { className: 'flex items-center gap-2 pb-2 border-b border-gray-200 dark:border-gray-700' },
                React.createElement('button', { onClick: onClose, className: 'text-[11px] text-gray-400 hover:text-gray-200' }, '← Back'),
                React.createElement('span', { style: { fontSize: 13, fontWeight: 700, color: '#c4b5fd' } }, '⬡ ' + serverName),
            ),
            React.createElement('div', { className: 'text-[11px] text-gray-400' },
                'Agent: ', React.createElement('span', { className: 'text-gray-300 font-semibold' }, attachment.agentName),
            ),
            /* Quick-edit JSON */
            React.createElement('p', { className: 'text-[10px] text-gray-500 mt-1' }, 'Edit server JSON:'),
            React.createElement('textarea', {
                className: 'flex-1 w-full px-2 py-1 border border-gray-600 rounded bg-gray-900 text-[11px] font-mono text-gray-100 min-h-[100px] resize-none',
                value: rawJson,
                onChange: function (e) { setRawJson(e.target.value); setJsonError(''); },
                spellCheck: false,
                disabled: agent.hardcoded,
            }),
            jsonError && React.createElement('p', { className: 'text-[10px] text-red-400' }, jsonError),
            React.createElement('button', {
                type: 'button',
                className: 'w-full px-3 py-1.5 text-[11px] bg-indigo-800 hover:bg-indigo-700 text-white rounded disabled:opacity-60',
                onClick: handleSave,
                disabled: saving || agent.hardcoded,
            }, saving ? 'Saving…' : 'Save JSON'),
        );
    }

    /* ── ToolPickerModal Removed in favor of HTML ToolConfigModal ── */

    /* ── McpAddModal ── appears when activeTool==='mcp' and agent is clicked */
    function McpAddModal(props) {
        var agentName = props.agentName;
        var onSave = props.onSave;
        var onCancel = props.onCancel;

        var nameRef = React.useRef(null);
        var [serverName, setServerName] = useState('');
        var [transport, setTransport] = useState('stdio');
        var [command, setCommand] = useState('');
        var [args, setArgs] = useState('');
        var [url, setUrl] = useState('');
        var [env, setEnv] = useState('{}');

        React.useEffect(function () {
            if (nameRef.current) nameRef.current.focus();
            var handleKey = function (e) { if (e.key === 'Escape') onCancel(); };
            document.addEventListener('keydown', handleKey);
            return function () { document.removeEventListener('keydown', handleKey); };
        }, [onCancel]);

        var inputStyle = { padding: '6px 10px', borderRadius: 6, fontSize: 12, border: '1px solid #475569', background: '#0f172a', color: '#f1f5f9', outline: 'none', width: '100%', fontFamily: "'Inter',sans-serif" };
        var labelStyle = { fontSize: 11, fontWeight: 600, color: '#94a3b8', fontFamily: "'Inter',sans-serif", display: 'block', marginBottom: 3 };

        var handleSubmit = function () {
            if (!serverName.trim()) return;
            var serverCfg = { transport: transport };
            if (transport === 'stdio') {
                serverCfg.command = command;
                serverCfg.args = args.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
            } else {
                serverCfg.url = url;
            }
            try { serverCfg.env = JSON.parse(env); } catch (e) { serverCfg.env = {}; }
            onSave(serverName.trim(), serverCfg);
        };

        return React.createElement(
            'div',
            {
                style: { position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.5)' },
                onClick: onCancel,
            },
            React.createElement(
                'div',
                {
                    style: { background: '#1e293b', borderRadius: 12, padding: '20px 24px', minWidth: 360, maxWidth: 480, width: '90vw', boxShadow: '0 8px 32px rgba(0,0,0,0.5)', border: '1px solid #334155', display: 'flex', flexDirection: 'column', gap: 12 },
                    onClick: function (e) { e.stopPropagation(); },
                },
                React.createElement('div', { style: { fontSize: 14, fontWeight: 700, color: '#c4b5fd', fontFamily: "'Inter',sans-serif" } }, '⬡ Add MCP Server to ' + agentName),
                /* Server Name */
                React.createElement('div', null,
                    React.createElement('label', { style: labelStyle }, 'Server Name'),
                    React.createElement('input', { ref: nameRef, value: serverName, onChange: function (e) { setServerName(e.target.value); }, placeholder: 'e.g. my-mcp-server', style: inputStyle }),
                ),
                /* Transport */
                React.createElement('div', null,
                    React.createElement('label', { style: labelStyle }, 'Transport'),
                    React.createElement('select', { value: transport, onChange: function (e) { setTransport(e.target.value); }, style: inputStyle },
                        React.createElement('option', { value: 'stdio' }, 'stdio'),
                        React.createElement('option', { value: 'sse' }, 'sse'),
                        React.createElement('option', { value: 'streamable-http' }, 'streamable-http'),
                    ),
                ),
                /* Command (stdio) */
                transport === 'stdio' && React.createElement('div', null,
                    React.createElement('label', { style: labelStyle }, 'Command'),
                    React.createElement('input', { value: command, onChange: function (e) { setCommand(e.target.value); }, placeholder: 'e.g. python or node', style: inputStyle }),
                ),
                /* Args (stdio) */
                transport === 'stdio' && React.createElement('div', null,
                    React.createElement('label', { style: labelStyle }, 'Args (one per line)'),
                    React.createElement('textarea', { value: args, onChange: function (e) { setArgs(e.target.value); }, placeholder: 'server.py\n--port\n8080', style: Object.assign({}, inputStyle, { minHeight: 60, resize: 'vertical' }) }),
                ),
                /* URL (sse/http) */
                (transport === 'sse' || transport === 'streamable-http') && React.createElement('div', null,
                    React.createElement('label', { style: labelStyle }, 'URL'),
                    React.createElement('input', { value: url, onChange: function (e) { setUrl(e.target.value); }, placeholder: 'http://localhost:8080/sse', style: inputStyle }),
                ),
                /* Env */
                React.createElement('div', null,
                    React.createElement('label', { style: labelStyle }, 'Environment (JSON)'),
                    React.createElement('textarea', { value: env, onChange: function (e) { setEnv(e.target.value); }, placeholder: '{}', style: Object.assign({}, inputStyle, { minHeight: 50, fontFamily: 'monospace', resize: 'vertical' }) }),
                ),
                React.createElement(
                    'div',
                    { style: { display: 'flex', gap: 8, justifyContent: 'flex-end', paddingTop: 4 } },
                    React.createElement('button', {
                        onClick: onCancel,
                        style: { padding: '7px 16px', borderRadius: 6, fontSize: 12, background: '#334155', color: '#94a3b8', border: 'none', cursor: 'pointer', fontFamily: "'Inter',sans-serif" },
                    }, 'Cancel'),
                    React.createElement('button', {
                        onClick: handleSubmit,
                        style: { padding: '7px 16px', borderRadius: 6, fontSize: 12, background: '#818cf8', color: '#fff', border: 'none', cursor: 'pointer', fontWeight: 600, fontFamily: "'Inter',sans-serif" },
                    }, 'Add Server'),
                ),
            ),
        );
    }

    /* ── AgentEditOverlay ── iframe overlay showing only the agent edit panel */

    function AgentCreateModal(props) {
        const inputRef = React.useRef(null);

        React.useEffect(() => {
            if (inputRef.current) inputRef.current.focus();
        }, []);

        React.useEffect(() => {
            const handleKey = (e) => {
                if (e.key === 'Escape') props.onCancel();
            };
            document.addEventListener('keydown', handleKey);
            return () => document.removeEventListener('keydown', handleKey);
        }, [props.onCancel]);

        const handleSubmit = () => {
            const val = inputRef.current ? inputRef.current.value.trim() : '';
            if (val) props.onSubmit(val);
        };

        return React.createElement(
            'div',
            {
                style: {
                    position: 'fixed', inset: 0, zIndex: 9999,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: 'rgba(0,0,0,0.45)',
                },
                onClick: props.onCancel,
            },
            React.createElement(
                'div',
                {
                    style: {
                        background: '#1e293b', borderRadius: 12, padding: '24px 28px',
                        minWidth: 320, boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
                        display: 'flex', flexDirection: 'column', gap: 16,
                        border: '1px solid #334155',
                    },
                    onClick: (e) => e.stopPropagation(),
                },
                React.createElement('div', {
                    style: {
                        fontSize: 15, fontWeight: 700, color: '#f1f5f9',
                        fontFamily: "'Inter', sans-serif"
                    },
                }, 'New Agent'),
                React.createElement('input', {
                    ref: inputRef,
                    placeholder: 'Agent name…',
                    autoComplete: 'off',
                    onKeyDown: (e) => { if (e.key === 'Enter') handleSubmit(); },
                    style: {
                        padding: '8px 12px', borderRadius: 6, fontSize: 14,
                        border: '1px solid #475569', background: '#0f172a',
                        color: '#f1f5f9', outline: 'none', width: '100%',
                        fontFamily: "'Inter', sans-serif",
                    },
                }),
                React.createElement(
                    'div',
                    { style: { display: 'flex', gap: 8, justifyContent: 'flex-end' } },
                    React.createElement('button', {
                        onClick: props.onCancel,
                        style: {
                            padding: '7px 16px', borderRadius: 6, fontSize: 13,
                            background: '#334155', color: '#94a3b8', border: 'none',
                            cursor: 'pointer', fontFamily: "'Inter', sans-serif",
                        },
                    }, 'Cancel'),
                    React.createElement('button', {
                        onClick: handleSubmit,
                        style: {
                            padding: '7px 16px', borderRadius: 6, fontSize: 13,
                            background: '#3b82f6', color: '#fff', border: 'none',
                            cursor: 'pointer', fontWeight: 600,
                            fontFamily: "'Inter', sans-serif",
                        },
                    }, 'Create'),
                ),
            ),
        );
    }

    function parseJson(str) {
        try { return JSON.parse(str || '{}'); } catch (e) { return {}; }
    }

    var TOOL_DEFS = [
        { key: 'google_search', label: 'Google Search' },
        { key: 'google_drive', label: 'Google Drive' },
        { key: 'cv_tools', label: 'CV Tools' },
        { key: 'image_tools', label: 'Image Tools' },
        { key: 'memory_blocks', label: 'Memory Blocks' },
        { key: 'create_agent', label: 'Create Agent' },
        { key: 'code_executor', label: 'Code Executor' },
    ];

    function ConfigPanel(props) {
        const agent = props.agent;
        const onChange = props.onChange;
        const onSave = props.onSave;
        const onDelete = props.onDelete;
        const saving = props.saving;

        const [fsStores, setFsStores] = useState([]);
        const [fsLoading, setFsLoading] = useState(false);

        var agentName = agent ? agent.name : null;
        useEffect(function () {
            if (!agentName) { setFsStores([]); return; }
            setFsLoading(true);
            fetch('/dashboard/api/agents/' + encodeURIComponent(agentName) + '/file-search/stores', { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (data) { setFsStores(Array.isArray(data.stores) ? data.stores : []); })
                .catch(function () { setFsStores([]); })
                .finally(function () { setFsLoading(false); });
        }, [agentName]);

        if (!agent) {
            return React.createElement(
                'div',
                { className: 'h-full flex flex-col text-xs text-gray-500 dark:text-gray-400' },
                React.createElement(
                    'p',
                    { className: 'mb-2' },
                    'Select an agent node on the canvas to edit its configuration.',
                ),
                React.createElement(
                    'p',
                    null,
                    'Use right-click or the toolbar in the canvas to create or connect agents.',
                ),
            );
        }

        const handleInput = function (field) {
            return function (event) {
                const value = event.target.value;
                onChange(Object.assign({}, agent, { [field]: value }));
            };
        };

        const handleCheckbox = function (field) {
            return function (event) {
                const checked = event.target.checked;
                onChange(Object.assign({}, agent, { [field]: checked }));
            };
        };

        const parents = Array.isArray(agent.parent_agents) ? agent.parent_agents : [];

        const toolConfig = parseJson(agent.tool_config);
        const activeToolCount = TOOL_DEFS.filter(function (t) { return !!toolConfig[t.key]; }).length;
        const mcpConfig = parseJson(agent.mcp_servers_config);
        const mcpServerNames = Object.keys(mcpConfig.mcpServers || {});

        return React.createElement(
            'div',
            { className: 'h-full flex flex-col text-xs text-gray-900 dark:text-gray-100' },
            React.createElement(
                'div',
                { className: 'space-y-3 overflow-y-auto pr-1 flex-1' },
                React.createElement(
                    'div',
                    null,
                    React.createElement(
                        'label',
                        { className: 'block text-[11px] font-semibold mb-1' },
                        'Name',
                    ),
                    React.createElement('input', {
                        className: 'w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-gray-100 dark:bg-gray-700 text-xs',
                        value: agent.name || '',
                        disabled: true,
                    }),
                ),
                React.createElement(
                    'div',
                    { className: 'grid grid-cols-2 gap-3' },
                    React.createElement(
                        'div',
                        null,
                        React.createElement(
                            'label',
                            { className: 'block text-[11px] font-semibold mb-1' },
                            'Type',
                        ),
                        React.createElement(
                            'select',
                            {
                                className: 'w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-xs',
                                value: agent.type || 'llm',
                                onChange: handleInput('type'),
                            },
                            React.createElement('option', { value: 'llm' }, 'LLM'),
                            React.createElement('option', { value: 'sequential' }, 'Sequential'),
                            React.createElement('option', { value: 'parallel' }, 'Parallel'),
                            React.createElement('option', { value: 'loop' }, 'Loop'),
                        ),
                    ),
                    React.createElement(
                        'div',
                        null,
                        React.createElement(
                            'label',
                            { className: 'block text-[11px] font-semibold mb-1' },
                            'Model',
                        ),
                        React.createElement('input', {
                            className: 'w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-xs',
                            value: agent.model_name || '',
                            onChange: handleInput('model_name'),
                        }),
                    ),
                ),
                React.createElement(
                    'div',
                    null,
                    React.createElement(
                        'label',
                        { className: 'block text-[11px] font-semibold mb-1' },
                        'Description',
                    ),
                    React.createElement('textarea', {
                        className: 'w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-xs min-h-[60px]',
                        value: agent.description || '',
                        onChange: handleInput('description'),
                    }),
                ),
                React.createElement(
                    'div',
                    null,
                    React.createElement(
                        'label',
                        { className: 'block text-[11px] font-semibold mb-1' },
                        'Instruction (preview)',
                    ),
                    React.createElement('textarea', {
                        className: 'w-full px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-[11px] min-h-[60px]',
                        value: agent.instruction || '',
                        onChange: handleInput('instruction'),
                    }),
                    React.createElement(
                        'p',
                        { className: 'mt-1 text-[10px] text-gray-500 dark:text-gray-400' },
                        'Use the main Agents page for full-screen Monaco editing if needed.',
                    ),
                ),

                /* ── Tools ── */
                React.createElement(
                    'div',
                    { className: 'border-t border-gray-200 dark:border-gray-700 pt-2' },
                    React.createElement(
                        'div',
                        { className: 'flex items-center justify-between mb-1' },
                        React.createElement('span', { className: 'text-[11px] font-semibold text-gray-700 dark:text-gray-300' },
                            'Tools' + (activeToolCount > 0 ? ' (' + activeToolCount + ')' : '')),
                        React.createElement('button', {
                            type: 'button',
                            className: 'text-[11px] px-2 py-0.5 bg-yellow-600 hover:bg-yellow-500 text-white rounded flex items-center gap-1 disabled:opacity-50',
                            onClick: function () { if (props.onOpenToolPicker) props.onOpenToolPicker(agent.name); },
                            disabled: !!agent.hardcoded,
                            title: agent.hardcoded ? 'Hardcoded agents cannot be modified' : 'Open Tool Settings',
                        },
                            React.createElement('i', { className: 'fas fa-sliders-h' }),
                            ' Configure',
                        ),
                    ),
                    activeToolCount > 0 && React.createElement(
                        'div',
                        { className: 'flex flex-wrap gap-1 mt-1' },
                        TOOL_DEFS.filter(function (t) { return !!toolConfig[t.key]; }).map(function (t) {
                            return React.createElement('span', {
                                key: t.key,
                                className: 'px-1.5 py-0.5 rounded bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 text-[10px] font-medium',
                            }, t.label);
                        }),
                    ),
                ),

                /* ── MCP Servers ── */
                React.createElement(
                    'div',
                    { className: 'border-t border-gray-200 dark:border-gray-700 pt-2' },
                    React.createElement(
                        'div',
                        { className: 'flex items-center justify-between mb-1' },
                        React.createElement('span', { className: 'text-[11px] font-semibold text-gray-700 dark:text-gray-300' },
                            'MCP Servers' + (mcpServerNames.length > 0 ? ' (' + mcpServerNames.length + ')' : '')),
                        React.createElement('button', {
                            type: 'button',
                            className: 'text-[11px] px-2 py-0.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded flex items-center gap-1 disabled:opacity-50',
                            onClick: function () { if (props.onAddMcpServer) props.onAddMcpServer(agent.name); },
                            disabled: !!agent.hardcoded,
                            title: agent.hardcoded ? 'Hardcoded agents cannot be modified' : 'Add MCP Server',
                        },
                            React.createElement('i', { className: 'fas fa-plus' }),
                            ' Add',
                        ),
                    ),
                    mcpServerNames.length > 0
                        ? React.createElement(
                            'div',
                            { className: 'space-y-0.5 mt-1' },
                            mcpServerNames.map(function (name) {
                                return React.createElement(
                                    'button',
                                    {
                                        key: name,
                                        type: 'button',
                                        className: 'w-full flex items-center justify-between px-2 py-1 rounded bg-indigo-50 dark:bg-indigo-900/20 hover:bg-indigo-100 dark:hover:bg-indigo-900/40 text-[11px] text-indigo-700 dark:text-indigo-300 font-mono text-left',
                                        onClick: function () { if (props.onSelectMcpServer) props.onSelectMcpServer(name); },
                                    },
                                    React.createElement('span', { className: 'truncate' }, '⬡ ' + name),
                                    React.createElement('i', { className: 'fas fa-chevron-right text-[9px] shrink-0 ml-1 opacity-50' }),
                                );
                            }),
                        )
                        : React.createElement('p', { className: 'text-[10px] text-gray-400 mt-1' }, 'No MCP servers configured.'),
                ),

                /* ── File Search ── */
                React.createElement(
                    'div',
                    { className: 'border-t border-gray-200 dark:border-gray-700 pt-2' },
                    React.createElement(
                        'div',
                        { className: 'flex items-center justify-between mb-1' },
                        React.createElement('span', { className: 'text-[11px] font-semibold text-gray-700 dark:text-gray-300' },
                            'File Search' + (fsStores.length > 0 ? ' (' + fsStores.length + ')' : '')),
                        React.createElement('button', {
                            type: 'button',
                            className: 'text-[11px] px-2 py-0.5 bg-blue-600 hover:bg-blue-500 text-white rounded flex items-center gap-1',
                            onClick: function () { if (props.onOpenFileSearch) props.onOpenFileSearch(); },
                            title: 'Manage File Search stores',
                        },
                            React.createElement('i', { className: 'fas fa-file-search' }),
                            ' Manage',
                        ),
                    ),
                    fsLoading
                        ? React.createElement('span', { className: 'text-[10px] text-gray-400' }, 'Loading…')
                        : fsStores.length === 0
                            ? React.createElement('p', { className: 'text-[10px] text-gray-400 mt-1' }, 'No stores assigned.')
                            : React.createElement(
                                'div',
                                { className: 'space-y-0.5 mt-1' },
                                fsStores.map(function (s) {
                                    return React.createElement(
                                        'div',
                                        { key: s.id || s.store_name, className: 'flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded bg-green-50 dark:bg-green-900/20' },
                                        React.createElement('span', { className: 'text-green-500 shrink-0' }, '●'),
                                        React.createElement('span', { className: 'truncate' }, s.display_name || s.store_name),
                                        s.document_count != null && React.createElement(
                                            'span', { className: 'ml-auto text-gray-400 shrink-0 text-[10px]' }, s.document_count + ' files',
                                        ),
                                    );
                                }),
                            ),
                ),

                /* ── Memory Blocks (only when memory_blocks tool is enabled) ── */
                !!toolConfig.memory_blocks && React.createElement(
                    'div',
                    { className: 'border-t border-gray-200 dark:border-gray-700 pt-2' },
                    React.createElement(
                        'div',
                        { className: 'flex items-center justify-between' },
                        React.createElement('span', { className: 'text-[11px] font-semibold text-gray-700 dark:text-gray-300' }, 'Memory Blocks'),
                        React.createElement('button', {
                            type: 'button',
                            className: 'text-[11px] px-2 py-0.5 bg-purple-600 hover:bg-purple-500 text-white rounded flex items-center gap-1',
                            onClick: function () {
                                if (typeof window.showMemoryBlocksModal === 'function') {
                                    window.showMemoryBlocksModal(agent.name);
                                }
                            },
                            title: 'Manage Memory Blocks',
                        },
                            React.createElement('i', { className: 'fas fa-brain' }),
                            ' Manage',
                        ),
                    ),
                ),

                React.createElement(
                    'div',
                    { className: 'flex items-center justify-between' },
                    React.createElement(
                        'label',
                        { className: 'inline-flex items-center space-x-1 text-[11px]' },
                        React.createElement('input', {
                            type: 'checkbox',
                            className: 'rounded border-gray-300 dark:border-gray-600',
                            checked: !!agent.disabled,
                            onChange: handleCheckbox('disabled'),
                        }),
                        React.createElement('span', null, 'Disabled'),
                    ),
                    React.createElement(
                        'span',
                        { className: 'text-[10px] text-gray-500 dark:text-gray-400' },
                        parents.length === 0 ? 'Root agent' : 'Parents: ' + parents.join(', '),
                    ),
                ),
            ),
            React.createElement(
                'div',
                { className: 'mt-3 pt-2 border-t border-gray-200 dark:border-gray-700 space-y-2' },
                React.createElement(
                    'button',
                    {
                        type: 'button',
                        onClick: props.onAdvancedSetup,
                        className: 'flex items-center justify-center gap-1 w-full px-3 py-1.5 text-[11px] bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded border border-gray-300 dark:border-gray-600',
                    },
                    '⚙ Advanced Setup',
                ),
                React.createElement(
                    'div',
                    { className: 'flex items-center justify-between' },
                    React.createElement(
                        'button',
                        {
                            type: 'button',
                            className: 'px-2 py-1 text-[11px] bg-red-600 hover:bg-red-700 text-white rounded disabled:opacity-60',
                            onClick: onDelete,
                            disabled: saving || agent.hardcoded,
                            title: agent.hardcoded ? 'Hardcoded agents cannot be deleted from here' : 'Delete agent',
                        },
                        'Delete',
                    ),
                    React.createElement(
                        'button',
                        {
                            type: 'button',
                            className: 'px-3 py-1 text-[11px] bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-60',
                            onClick: onSave,
                            disabled: saving,
                        },
                        saving ? 'Saving…' : 'Save',
                    ),
                ),
            ),
        );
    }

    const nodeTypes = {
        default: DefaultAgentNode,
        tool: ToolNode,
        mcp: McpNode,
    };

    function AgentsVisualBuilderApp(props) {
        const initialAgentsByName = normalizeAgents(props.configs || []);
        const initialGraph = buildGraph(initialAgentsByName);

        const [agentsByName, setAgentsByName] = useState(initialAgentsByName);
        const [nodes, setNodes] = useState(initialGraph.nodes);
        const [edges, setEdges] = useState(initialGraph.edges);
        const [selectedAgentName, setSelectedAgentName] = useState(null);
        const [saving, setSaving] = useState(false);
        const [loading, setLoading] = useState(false);
        const [activeTool, setActiveTool] = useState('select');
        const activeToolRef = useRef(activeTool);
        useEffect(() => {
            activeToolRef.current = activeTool;
            window.visualBuilderGetActiveTool = () => activeToolRef.current;
        }, [activeTool]);
        const [createModal, setCreateModal] = useState(null);
        // { nodeId, type:'tool'|'mcp', agentName, toolKey?, label } — selected tool/mcp node
        const [selectedAttachment, setSelectedAttachment] = useState(null);
        // { agentId, agentName } — open AgentEditOverlay when set

        // { agentName } — ToolPickerModal target (now opens HTML modal)
        const [toolPickerModal, setToolPickerModal] = useState(null);
        // { agentName } — McpAddModal target
        const [mcpAddModal, setMcpAddModal] = useState(null);



        useEffect(() => {
            const handleKeyDown = (e) => {
                // If any input is focused, let it handle the event
                if (e.target && ['INPUT', 'TEXTAREA', 'SELECT'].indexOf(e.target.tagName) !== -1) return;

                if (e.key === 'v' || e.key === 'V' || e.key === 'Escape') {
                    setActiveTool('select');
                } else if (e.key === 'a' || e.key === 'A') {
                    setActiveTool('agent');
                } else if (e.key === 'Delete' || e.key === 'Backspace') {
                    setActiveTool('delete');
                }
            };
            window.addEventListener('keydown', handleKeyDown);
            return () => window.removeEventListener('keydown', handleKeyDown);
        }, []);

        const reactFlowInstanceRef = useRef(null);

        const onNodesChange = useCallback(
            (changes) => {
                setNodes((nds) => applyNodeChanges(changes, nds));
            },
            [setNodes],
        );

        const onEdgesChange = useCallback(
            (changes) => {
                setEdges((eds) => applyEdgeChanges(changes, eds));
            },
            [setEdges],
        );

        const regenerateGraph = useCallback(
            (map) => {
                const g = buildGraph(map);
                setNodes(g.nodes);
                setEdges(g.edges);
            },
            [setNodes, setEdges],
        );

        const syncFromDb = useCallback(async () => {
            if (!props.projectId) {
                return;
            }
            setLoading(true);
            try {
                const configs = await apiGetAgents(props.projectId);
                const map = normalizeAgents(configs);
                setAgentsByName(map);
                regenerateGraph(map);
                setSelectedAgentName(null);
            } catch (error) {
                console.error('[AgentVisualBuilder] Failed to sync from DB', error);
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Failed to sync agents from database', 'error');
                }
            } finally {
                setLoading(false);
            }
        }, [props.projectId, regenerateGraph]);

        // Used by ToolConfigPanel / McpConfigPanel / tool picker bridge to save a directly-provided agent object
        const handleSaveAgent = useCallback(async (updatedAgent) => {
            if (!updatedAgent || !updatedAgent.id) return;
            setSaving(true);
            try {
                await apiUpdateAgent(updatedAgent);
                await syncFromDb();
                if (typeof window.showNotification === 'function') window.showNotification('Saved');
            } catch (error) {
                if (typeof window.showNotification === 'function') window.showNotification('Failed to save', 'error');
            } finally {
                setSaving(false);
            }
        }, [syncFromDb]);

        // Intercept editAgentForm submission to prevent page reload
        useEffect(() => {
            const form = document.getElementById('editAgentForm');
            if (!form) return;

            const handleEditSubmit = async (e) => {
                e.preventDefault();

                const submitBtn = document.getElementById('editAgentSubmitBtn');
                const loader = document.getElementById('editAgentLoader');
                const btnText = document.getElementById('editAgentBtnText');

                if (submitBtn) submitBtn.disabled = true;
                if (loader) loader.classList.remove('hidden');
                if (btnText) btnText.textContent = 'Updating...';

                const configIdRaw = document.getElementById('editAgentId').value;
                const agentName = document.getElementById('editAgentName').value;
                const projectId = document.getElementById('editAgentProject').value;

                const configId = parseInt(configIdRaw, 10);
                if (isNaN(configId)) {
                    if (typeof window.showNotification === 'function') window.showNotification('Invalid agent ID', 'error');
                    if (submitBtn) submitBtn.disabled = false;
                    if (loader) loader.classList.add('hidden');
                    if (btnText) btnText.textContent = 'Update Agent';
                    return;
                }

                const formData = new FormData();
                formData.append('name', agentName);
                if (projectId) formData.append('project_id', projectId);
                formData.append('type', document.getElementById('editAgentType').value);
                formData.append('model_name', document.getElementById('editAgentModel').value || '');
                formData.append('description', document.getElementById('editAgentDescription').value || '');
                formData.append('instruction', document.getElementById('editAgentInstruction').value || '');

                const getJson = (id, fallbackId) =>
                    (typeof monacoEditors !== 'undefined' && monacoEditors[id]) ? getJsonFromEditor(monacoEditors[id]) : (document.getElementById(fallbackId) ? document.getElementById(fallbackId).value : '') || '';

                formData.append('parent_agents', getJson('editAgentParentsEditor', 'editAgentParents'));
                formData.append('allowed_for_roles', getJson('editAgentRolesEditor', 'editAgentRoles'));
                formData.append('tool_config', getJson('editAgentToolConfigEditor', 'editAgentToolConfig'));
                formData.append('mcp_servers_config', getJson('editAgentMcpServersConfigEditor', 'editAgentMcpServersConfig'));
                formData.append('planner_config', getJson('editAgentPlannerConfigEditor', 'editAgentPlannerConfig'));
                formData.append('generate_content_config', getJson('editAgentGenerateContentConfigEditor', 'editAgentGenerateContentConfig'));
                formData.append('input_schema', getJson('editAgentInputSchemaEditor', 'editAgentInputSchema'));
                formData.append('output_schema', getJson('editAgentOutputSchemaEditor', 'editAgentOutputSchema'));
                formData.append('include_contents', document.getElementById('editAgentIncludeContents') ? document.getElementById('editAgentIncludeContents').value : '');
                formData.append('guardrail_config', getJson('editAgentGuardrailConfigEditor', 'editAgentGuardrailConfig'));
                formData.append('max_iterations', document.getElementById('editAgentMaxIterations') ? document.getElementById('editAgentMaxIterations').value : '');
                formData.append('disabled', document.getElementById('editAgentDisabled') ? document.getElementById('editAgentDisabled').checked : false);
                formData.append('hardcoded', document.getElementById('editAgentHardcoded') ? document.getElementById('editAgentHardcoded').checked : false);

                try {
                    const response = await fetch(`/dashboard/api/agents/${configId}`, {
                        method: 'PUT',
                        body: formData
                    });
                    const result = await response.json();

                    if (result.success) {
                        if (typeof window.showNotification === 'function') window.showNotification('Agent updated successfully');
                        if (typeof hideEditAgentModal === 'function') hideEditAgentModal();
                        else if (typeof window.hideEditAgentModal === 'function') window.hideEditAgentModal();

                        await syncFromDb();
                    } else {
                        if (typeof window.showNotification === 'function') window.showNotification(result.message || 'Failed to update', 'error');
                    }
                } catch (err) {
                    console.error('Update error', err);
                    if (typeof window.showNotification === 'function') window.showNotification('Error updating agent', 'error');
                } finally {
                    if (submitBtn) submitBtn.disabled = false;
                    if (loader) loader.classList.add('hidden');
                    if (btnText) btnText.textContent = 'Update Agent';
                }
            };

            form.addEventListener('submit', handleEditSubmit);
            return () => form.removeEventListener('submit', handleEditSubmit);
        }, [syncFromDb]);

        // Bridge to HTML configuration modal for tools
        useEffect(() => {
            if (toolPickerModal) {
                const agent = agentsByName.get(toolPickerModal.agentName);
                if (!agent) return;

                const modal = document.getElementById('vbToolConfigModal');
                if (modal) {
                    const ta = document.getElementById('vbToolConfig');
                    if (ta) ta.value = agent.tool_config || '{}';
                    if (window.syncJsonToToolConfig) window.syncJsonToToolConfig('vb');
                    if (window.setupToolListeners && !window.__vbToolListenersSet) {
                        window.setupToolListeners('vb');
                        window.__vbToolListenersSet = true;
                    }
                    modal.classList.remove('hidden');

                    const origApply = window.__originalApplyConfigModal || window.applyConfigModal;
                    window.__originalApplyConfigModal = origApply;
                    window.applyConfigModal = function (prefix, type) {
                        if (prefix === 'vb' && type === 'ToolConfig') {
                            const newCfgStr = document.getElementById('vbToolConfig').value;
                            let newCfg = {};
                            try { newCfg = JSON.parse(newCfgStr); } catch (e) { }
                            handleSaveAgent(Object.assign({}, agent, { tool_config: JSON.stringify(newCfg) }));
                            modal.classList.add('hidden');
                            setToolPickerModal(null);
                        } else if (origApply) {
                            origApply(prefix, type);
                        }
                    };

                    const origClose = window.__originalCloseConfigModal || window.closeConfigModal;
                    window.__originalCloseConfigModal = origClose;
                    window.closeConfigModal = function (prefix, type) {
                        if (prefix === 'vb' && type === 'ToolConfig') {
                            modal.classList.add('hidden');
                            setToolPickerModal(null);
                        } else if (origClose) {
                            origClose(prefix, type);
                        }
                    };

                    const origReset = window.__originalResetToolConfig || window.resetToolConfig;
                    window.__originalResetToolConfig = origReset;
                    window.resetToolConfig = function (prefix) {
                        if (prefix === 'vb') {
                            const ta = document.getElementById('vbToolConfig');
                            if (ta) ta.value = '{}';
                            if (window.syncJsonToToolConfig) window.syncJsonToToolConfig('vb');
                        } else if (origReset) {
                            origReset(prefix);
                        }
                    };

                    window.toggleJsonEditor = function (textareaId, editorId) {
                        var ta = document.getElementById(textareaId);
                        var ed = document.getElementById(editorId);
                        if (!ta || !ed) return;
                        if (ta.style.display !== 'none') {
                            ta.style.display = 'none';
                            ed.style.display = 'block';
                            ed.innerText = "Monaco Editor not loaded in Visual Builder";
                        } else {
                            ta.style.display = 'block';
                            ed.style.display = 'none';
                        }
                    };
                }
            }
        }, [toolPickerModal, agentsByName, handleSaveAgent]);


        useEffect(() => {
            const nodeCountEl = document.getElementById('visualNodeCount');
            const edgeCountEl = document.getElementById('visualEdgeCount');
            if (nodeCountEl) {
                nodeCountEl.textContent = String(nodes.length) + ' nodes';
            }
            if (edgeCountEl) {
                edgeCountEl.textContent = String(edges.length) + ' connections';
            }
        }, [nodes, edges]);

        useEffect(() => {
            window.visualBuilder = {
                zoomIn: function () {
                    const inst = reactFlowInstanceRef.current;
                    if (!inst) return;
                    inst.zoomTo(inst.getZoom() * 1.2);
                },
                zoomOut: function () {
                    const inst = reactFlowInstanceRef.current;
                    if (!inst) return;
                    inst.zoomTo(inst.getZoom() / 1.2);
                },
                fitView: function () {
                    const inst = reactFlowInstanceRef.current;
                    if (!inst) return;
                    inst.fitView({ padding: 0.2 });
                },
                syncFromDb: function () {
                    syncFromDb();
                },
                exportJson: async function () {
                    try {
                        const data = await apiExportAgents(props.projectId);
                        downloadJson('agents_export.json', data);
                    } catch (err) {
                        console.error('[AgentVisualBuilder] Export failed', err);
                        if (typeof window.showNotification === 'function') {
                            window.showNotification('Failed to export agents JSON', 'error');
                        }
                    }
                },
                importJson: async function (jsonData, overwrite) {
                    try {
                        await apiImportAgents(jsonData, overwrite);
                        if (typeof window.showNotification === 'function') {
                            window.showNotification('Agents imported successfully');
                        }
                        await syncFromDb();
                    } catch (err) {
                        console.error('[AgentVisualBuilder] Import failed', err);
                        if (typeof window.showNotification === 'function') {
                            window.showNotification('Failed to import agents JSON', 'error');
                        }
                    }
                },
                getSelectedNode: function () {
                    return selectedAgentName || null;
                }
            };
        }, [syncFromDb, props.projectId, selectedAgentName]);



        const onConnect = useCallback(
            async (connection) => {
                const sourceName = connection.source;
                const targetName = connection.target;
                if (!sourceName || !targetName || sourceName === targetName) {
                    return;
                }

                const targetAgent = agentsByName.get(targetName);
                if (!targetAgent) {
                    return;
                }

                const parentAgents = Array.isArray(targetAgent.parent_agents)
                    ? targetAgent.parent_agents.slice()
                    : [];
                if (parentAgents.indexOf(sourceName) === -1) {
                    parentAgents.push(sourceName);
                } else {
                    return;
                }

                const updatedAgent = Object.assign({}, targetAgent, { parent_agents: parentAgents });
                const nextMap = new Map(agentsByName);
                nextMap.set(targetName, updatedAgent);
                setAgentsByName(nextMap);
                regenerateGraph(nextMap);

                try {
                    await apiUpdateAgent(updatedAgent);
                    if (typeof window.showNotification === 'function') {
                        window.showNotification('Connection created');
                    }
                } catch (error) {
                    console.error('[AgentVisualBuilder] Failed to persist connection', error);
                    if (typeof window.showNotification === 'function') {
                        window.showNotification('Failed to save connection', 'error');
                    }
                }
            },
            [agentsByName, regenerateGraph],
        );

        const onEdgesDelete = useCallback(
            async (deletedEdges) => {
                if (!Array.isArray(deletedEdges) || deletedEdges.length === 0) {
                    return;
                }

                const nextMap = new Map(agentsByName);
                const affectedAgents = new Set();

                deletedEdges.forEach((edge) => {
                    const sourceName = edge.source;
                    const targetName = edge.target;
                    if (!sourceName || !targetName) return;
                    const targetAgent = nextMap.get(targetName);
                    if (!targetAgent || !Array.isArray(targetAgent.parent_agents)) return;
                    const filteredParents = targetAgent.parent_agents.filter(function (p) {
                        return p !== sourceName;
                    });
                    if (filteredParents.length !== targetAgent.parent_agents.length) {
                        const updatedAgent = Object.assign({}, targetAgent, {
                            parent_agents: filteredParents,
                        });
                        nextMap.set(targetName, updatedAgent);
                        affectedAgents.add(updatedAgent);
                    }
                });

                if (affectedAgents.size === 0) {
                    return;
                }

                setAgentsByName(nextMap);
                regenerateGraph(nextMap);

                for (const agent of affectedAgents) {
                    try {
                        await apiUpdateAgent(agent);
                    } catch (err) {
                        console.error('[AgentVisualBuilder] Failed to persist edge removal', err);
                    }
                }
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Connection(s) updated');
                }
            },
            [agentsByName, regenerateGraph],
        );

        const handleDeleteSpecific = useCallback(async (nodeId) => {
            // Tool node: __tool__<agentName>__<toolKey>
            if (nodeId.startsWith('__tool__')) {
                const rest = nodeId.slice('__tool__'.length);
                const sep = rest.lastIndexOf('__');
                if (sep === -1) return;
                const agentName = rest.slice(0, sep);
                const toolKey = rest.slice(sep + 2);
                const agent = agentsByName.get(agentName);
                if (!agent || agent.hardcoded) {
                    if (typeof window.showNotification === 'function') window.showNotification('Cannot modify hardcoded agent', 'warning');
                    setActiveTool('select');
                    return;
                }
                try { var tc = JSON.parse(agent.tool_config || '{}'); } catch (e) { var tc = {}; }
                delete tc[toolKey];
                setSaving(true);
                try {
                    await apiUpdateAgent(Object.assign({}, agent, { tool_config: JSON.stringify(tc) }));
                    if (typeof window.showNotification === 'function') window.showNotification('Tool removed');
                    await syncFromDb();
                } catch (err) {
                    if (typeof window.showNotification === 'function') window.showNotification('Failed to remove tool', 'error');
                } finally { setSaving(false); setActiveTool('select'); }
                return;
            }

            // MCP node: __mcp__<agentName>__<serverName>
            if (nodeId.startsWith('__mcp__')) {
                const rest = nodeId.slice('__mcp__'.length);
                const sep = rest.indexOf('__');
                if (sep === -1) return;
                const agentName = rest.slice(0, sep);
                const serverName = rest.slice(sep + 2);
                const agent = agentsByName.get(agentName);
                if (!agent || agent.hardcoded) {
                    if (typeof window.showNotification === 'function') window.showNotification('Cannot modify hardcoded agent', 'warning');
                    setActiveTool('select');
                    return;
                }
                try { var mc = JSON.parse(agent.mcp_servers_config || '{}'); } catch (e) { var mc = {}; }
                if (mc.mcpServers) delete mc.mcpServers[serverName];
                setSaving(true);
                try {
                    await apiUpdateAgent(Object.assign({}, agent, { mcp_servers_config: JSON.stringify(mc) }));
                    if (typeof window.showNotification === 'function') window.showNotification('MCP server removed');
                    await syncFromDb();
                } catch (err) {
                    if (typeof window.showNotification === 'function') window.showNotification('Failed to remove MCP server', 'error');
                } finally { setSaving(false); setActiveTool('select'); }
                return;
            }

            // Agent node
            const agent = agentsByName.get(nodeId);
            if (!agent || agent.hardcoded) {
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Cannot delete hardcoded agent', 'warning');
                }
                setActiveTool('select');
                return;
            }
            setSaving(true);
            try {
                await apiDeleteAgent(agent.id);
                if (typeof window.showNotification === 'function') window.showNotification('Agent deleted');
                if (selectedAgentName === nodeId) setSelectedAgentName(null);
                await syncFromDb();
            } catch (error) {
                console.error('[AgentVisualBuilder] Failed to delete agent', error);
                if (typeof window.showNotification === 'function') window.showNotification('Failed to delete agent', 'error');
            } finally {
                setSaving(false);
                setActiveTool('select');
            }
        }, [agentsByName, selectedAgentName, syncFromDb]);

        useEffect(() => {
            window.visualBuilderDeleteSpecific = handleDeleteSpecific;
            return () => { delete window.visualBuilderDeleteSpecific; };
        }, [handleDeleteSpecific]);

        useEffect(() => {
            window.visualBuilderSelectAgent = setSelectedAgentName;
            return () => { delete window.visualBuilderSelectAgent; };
        }, [setSelectedAgentName]);

        useEffect(() => {
            window.visualBuilderSelectAttachment = function (nodeId, type, data) {
                setSelectedAttachment({ nodeId, type, agentName: data.agentName, toolKey: data.toolKey, label: data.label });
                setSelectedAgentName(null);
            };
            return () => { delete window.visualBuilderSelectAttachment; };
        }, []);

        // Clear attachment/advanced only when a specific agent is chosen (not on deselect)
        useEffect(() => {
            if (selectedAgentName) {
                setSelectedAttachment(null);
            }
        }, [selectedAgentName]);

        const onNodeClick = useCallback((_event, node) => {
            if (!node || !node.id) return;
            const currentTool = activeToolRef.current;
            const isAttachment = node.id.startsWith('__tool__') || node.id.startsWith('__mcp__');

            if (currentTool === 'delete') {
                // delete handled in node's own onClick
                return;
            }

            if (currentTool === 'tool' && !isAttachment) {
                // Open tool picker for this agent
                setActiveTool('select');
                setToolPickerModal({ agentName: node.id });
                return;
            }

            if (currentTool === 'mcp' && !isAttachment) {
                // Open MCP add modal for this agent
                setActiveTool('select');
                setMcpAddModal({ agentName: node.id });
                return;
            }

            // select mode
            if (isAttachment) {
                // handled in node's own onClick via window.visualBuilderSelectAttachment
            } else {
                setSelectedAgentName(node.id);
            }
        }, []);

        const onEdgeClick = useCallback((event, edge) => {
            const currentTool = activeToolRef.current;
            if (currentTool === 'delete') {
                event.stopPropagation();
                const sourceName = edge.source;
                const targetName = edge.target;
                const targetAgent = agentsByName.get(targetName);
                if (!targetAgent || targetAgent.hardcoded) {
                    if (typeof window.showNotification === 'function') window.showNotification('Cannot modify hardcoded agent', 'warning');
                    return;
                }
                const parents = Array.isArray(targetAgent.parent_agents) ? targetAgent.parent_agents : [];
                const newParents = parents.filter(function (p) { return p !== sourceName; });
                if (newParents.length === parents.length) return;

                const updatedAgent = Object.assign({}, targetAgent, { parent_agents: newParents });
                setSaving(true);
                apiUpdateAgent(updatedAgent).then(function () {
                    if (typeof window.showNotification === 'function') window.showNotification('Connection deleted');
                    syncFromDb();
                }).catch(function (err) {
                    console.error('Failed to delete connection', err);
                    if (typeof window.showNotification === 'function') window.showNotification('Failed to delete connection', 'error');
                }).finally(function () {
                    setSaving(false);
                });
                setActiveTool('select');
            }
        }, [agentsByName, syncFromDb]);

        const onPaneClick = useCallback((event) => {
            const currentTool = activeToolRef.current;
            if (currentTool === 'agent') {
                const inst = reactFlowInstanceRef.current;
                if (!inst) return;

                const pos = inst.screenToFlowPosition({ x: event.clientX, y: event.clientY });

                setActiveTool('select');
                setCreateModal({ flowPos: pos });
            } else if (currentTool === 'select') {
                setSelectedAgentName(null);
            }
        }, [props.projectId, agentsByName, syncFromDb]);

        const onSelectionChange = useCallback((params) => {
            if (!params || !Array.isArray(params.nodes)) return;
            // Ignore tool/MCP attachment nodes — their own onClick handles selection
            const agentNodes = params.nodes.filter(function (n) {
                return n.id && !n.id.startsWith('__tool__') && !n.id.startsWith('__mcp__');
            });
            if (agentNodes.length > 0) {
                setSelectedAgentName(agentNodes[0].id);
            } else if (params.nodes.length === 0) {
                // Nothing selected at all
                setSelectedAgentName(null);
            }
            // If only attachment nodes are selected, leave selectedAgentName unchanged
        }, []);

        const selectedAgent = selectedAgentName ? agentsByName.get(selectedAgentName) : null;

        const [draftAgent, setDraftAgent] = useState(selectedAgent || null);

        useEffect(() => {
            setDraftAgent(selectedAgent || null);
        }, [selectedAgentName, agentsByName, selectedAgent]);

        const handleDraftChange = useCallback((updated) => {
            setDraftAgent(updated);
        }, []);

        const handleSave = useCallback(async () => {
            if (!draftAgent || !draftAgent.id) {
                return;
            }
            setSaving(true);
            try {
                await apiUpdateAgent(draftAgent);
                const nextMap = new Map(agentsByName);
                nextMap.set(draftAgent.name, draftAgent);
                setAgentsByName(nextMap);
                regenerateGraph(nextMap);
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Agent updated');
                }
            } catch (error) {
                console.error('[AgentVisualBuilder] Failed to save agent', error);
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Failed to save agent', 'error');
                }
            } finally {
                setSaving(false);
            }
        }, [draftAgent, agentsByName, regenerateGraph]);

        const handleDelete = useCallback(async () => {
            if (!draftAgent || !draftAgent.id) {
                return;
            }
            const confirmDelete = window.confirm(
                'Are you sure you want to delete agent "' + draftAgent.name + '"?',
            );
            if (!confirmDelete) return;
            setSaving(true);
            try {
                await apiDeleteAgent(draftAgent.id);
                const nextMap = new Map(agentsByName);
                nextMap.delete(draftAgent.name);
                setAgentsByName(nextMap);
                regenerateGraph(nextMap);
                setSelectedAgentName(null);
                setDraftAgent(null);
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Agent deleted');
                }
            } catch (error) {
                console.error('[AgentVisualBuilder] Failed to delete agent', error);
                if (typeof window.showNotification === 'function') {
                    window.showNotification('Failed to delete agent', 'error');
                }
            } finally {
                setSaving(false);
            }
        }, [draftAgent, agentsByName, regenerateGraph]);

        /* handleSaveAgent was moved up to before the toolPickerModal useEffect */

        return React.createElement(
            'div',
            { className: 'w-full h-full flex flex-col lg:flex-row gap-4' },
            React.createElement(
                'div',
                {
                    className:
                        'flex-1 min-h-[320px] bg-white dark:bg-gray-900 rounded-lg shadow border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col',
                },
                React.createElement(
                    'div',
                    {
                        className:
                            'flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-xs text-gray-600 dark:text-gray-300',
                    },
                    React.createElement(
                        'div',
                        { className: 'flex items-center gap-2' },
                        React.createElement(
                            'span',
                            null,
                            String(nodes.length) + ' nodes, ' + String(edges.length) + ' connections',
                        ),
                        loading &&
                        React.createElement(
                            'span',
                            { className: 'ml-2 text-[11px] text-blue-500' },
                            'Syncing…',
                        ),
                    ),
                    React.createElement(
                        'div',
                        { className: 'flex items-center gap-1' },
                        React.createElement(
                            'button',
                            {
                                type: 'button',
                                className:
                                    'px-2 py-1 bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded',
                                onClick: function () {
                                    const inst = reactFlowInstanceRef.current;
                                    if (!inst) return;
                                    inst.zoomTo(inst.getZoom() * 1.2);
                                },
                                title: 'Zoom in',
                            },
                            React.createElement('i', { className: 'fas fa-plus' }),
                        ),
                        React.createElement(
                            'button',
                            {
                                type: 'button',
                                className:
                                    'px-2 py-1 bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded',
                                onClick: function () {
                                    const inst = reactFlowInstanceRef.current;
                                    if (!inst) return;
                                    inst.zoomTo(inst.getZoom() / 1.2);
                                },
                                title: 'Zoom out',
                            },
                            React.createElement('i', { className: 'fas fa-minus' }),
                        ),
                        React.createElement(
                            'button',
                            {
                                type: 'button',
                                className:
                                    'px-2 py-1 bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded',
                                onClick: function () {
                                    const inst = reactFlowInstanceRef.current;
                                    if (!inst) return;
                                    inst.fitView({ padding: 0.2 });
                                },
                                title: 'Fit to view',
                            },
                            React.createElement('i', { className: 'fas fa-expand-arrows-alt' }),
                        ),
                        React.createElement(
                            'button',
                            {
                                type: 'button',
                                className:
                                    'ml-2 px-2 py-1 bg-orange-600 hover:bg-orange-700 text-white rounded disabled:opacity-60',
                                disabled: !props.projectId || loading,
                                onClick: syncFromDb,
                            },
                            React.createElement('i', { className: 'fas fa-sync-alt mr-1' }),
                            'Sync',
                        ),
                        React.createElement(
                            'button',
                            {
                                type: 'button',
                                className:
                                    'ml-1 px-2 py-1 bg-green-600 hover:bg-green-700 text-white rounded disabled:opacity-60',
                                disabled: !props.projectId,
                                onClick: function () {
                                    apiExportAgents(props.projectId)
                                        .then(function (data) {
                                            downloadJson('agents_export.json', data);
                                        })
                                        .catch(function (err) {
                                            console.error('[AgentVisualBuilder] Export failed', err);
                                            if (typeof window.showNotification === 'function') {
                                                window.showNotification('Failed to export agents JSON', 'error');
                                            }
                                        });
                                },
                            },
                            'Export',
                        ),
                        React.createElement(
                            'button',
                            {
                                type: 'button',
                                className:
                                    'ml-1 px-2 py-1 bg-purple-600 hover:bg-purple-700 text-white rounded disabled:opacity-60',
                                disabled: !props.projectId,
                                onClick: function () {
                                    var input = document.createElement('input');
                                    input.type = 'file';
                                    input.accept = 'application/json';
                                    input.onchange = function (event) {
                                        var file = event.target.files && event.target.files[0];
                                        if (!file) return;
                                        var reader = new FileReader();
                                        reader.onload = function (e) {
                                            try {
                                                var text = e.target && e.target.result ? String(e.target.result) : '';
                                                var json = JSON.parse(text);
                                                apiImportAgents(json, false)
                                                    .then(function () {
                                                        if (typeof window.showNotification === 'function') {
                                                            window.showNotification('Agents imported successfully');
                                                        }
                                                        return syncFromDb();
                                                    })
                                                    .catch(function (err) {
                                                        console.error('[AgentVisualBuilder] Import failed', err);
                                                        if (typeof window.showNotification === 'function') {
                                                            window.showNotification('Failed to import agents JSON', 'error');
                                                        }
                                                    });
                                            } catch (err) {
                                                console.error('[AgentVisualBuilder] Invalid JSON file', err);
                                                if (typeof window.showNotification === 'function') {
                                                    window.showNotification('Invalid JSON file', 'error');
                                                }
                                            }
                                        };
                                        reader.readAsText(file);
                                    };
                                    input.click();
                                },
                            },
                            'Import',
                        ),
                        React.createElement(
                            'button',
                            {
                                type: 'button',
                                className: 'ml-1 px-2 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-60',
                                disabled: !props.projectId,
                                onClick: function () {
                                    let rootAgent = (window.visualBuilder && typeof window.visualBuilder.getSelectedNode === 'function') ? window.visualBuilder.getSelectedNode() : null;

                                    if (!rootAgent) {
                                        let rootAgents = (Array.from(agentsByName.values()) || []).filter(a => !a.parent_agents || (Array.isArray(a.parent_agents) && a.parent_agents.length === 0));
                                        if (rootAgents.length === 1) {
                                            rootAgent = rootAgents[0].name;
                                        } else {
                                            if (typeof window.showNotification === 'function') window.showNotification('Please select a root agent node in the graph first.', 'warning');
                                            return;
                                        }
                                    }
                                    
                                    window.chatRequestedFullscreen = true;
                                    if (typeof window.openAgentChat === 'function') {
                                        window.openAgentChat(rootAgent);
                                    } else if (typeof window.presentChatUserSelectionModal === 'function') {
                                        window.pendingChatAgentName = rootAgent;
                                        window.presentChatUserSelectionModal();
                                    } else {
                                        if (typeof window.showNotification === 'function') window.showNotification('Chat initialization failed. Please refresh.', 'error');
                                    }
                                },
                            },
                            React.createElement('i', { className: 'fas fa-comments mr-1' }),
                            'Chat',
                        ),
                    ),
                ),
                React.createElement(
                    'div',
                    { className: 'flex-1 relative cursor-tool-' + activeTool },
                    React.createElement(
                        ReactFlowComponent,
                        {
                            nodes: nodes,
                            edges: edges,
                            nodeTypes: nodeTypes,
                            onNodesChange: onNodesChange,
                            onEdgesChange: onEdgesChange,
                            onConnect: onConnect,
                            onEdgesDelete: onEdgesDelete,
                            onNodeClick: onNodeClick,
                            onEdgeClick: onEdgeClick,
                            onPaneClick: onPaneClick,
                            onSelectionChange: onSelectionChange,
                            fitView: true,
                            nodesDraggable: activeTool === 'select',
                            nodesConnectable: activeTool === 'select',
                            elementsSelectable: activeTool === 'select',
                            selectNodesOnDrag: activeTool === 'select',
                            panOnScroll: true,
                            zoomOnScroll: true,
                            onInit: function (instance) {
                                reactFlowInstanceRef.current = instance;
                                setTimeout(function () {
                                    instance.fitView({ padding: 0.3 });
                                }, 100);
                            },
                        },
                        React.createElement(MiniMap, null),
                        React.createElement(Background, { gap: 16, color: '#9ca3af' }),
                        React.createElement(Controls, null),

                        /* Excalidraw-style Floating Toolbar */
                        React.createElement(
                            'div',
                            { className: 'floating-toolbar' },
                            React.createElement('button', {
                                className: 'tool-btn ' + (activeTool === 'select' ? 'active' : ''),
                                onClick: function () { setActiveTool('select'); },
                                title: 'Select (V)'
                            }, React.createElement('i', { className: 'fas fa-mouse-pointer' }), ' Select'),
                            React.createElement('button', {
                                className: 'tool-btn ' + (activeTool === 'agent' ? 'active' : ''),
                                onClick: function () { setActiveTool('agent'); },
                                title: 'Add Agent (A)'
                            }, React.createElement('i', { className: 'fas fa-plus-square' }), ' Agent'),
                            React.createElement('button', {
                                className: 'tool-btn ' + (activeTool === 'tool' ? 'active' : ''),
                                onClick: function () { setActiveTool('tool'); },
                                title: 'Add Tool to Agent — click an agent node',
                                style: { color: activeTool === 'tool' ? '#fde68a' : undefined },
                            }, React.createElement('i', { className: 'fas fa-wrench' }), ' Tool'),
                            React.createElement('button', {
                                className: 'tool-btn ' + (activeTool === 'mcp' ? 'active' : ''),
                                onClick: function () { setActiveTool('mcp'); },
                                title: 'Add MCP Server to Agent — click an agent node',
                                style: { color: activeTool === 'mcp' ? '#c4b5fd' : undefined },
                            }, React.createElement('i', { className: 'fas fa-plug' }), ' MCP'),
                            React.createElement('button', {
                                className: 'tool-btn ' + (activeTool === 'delete' ? 'active alert' : ''),
                                onClick: function () { setActiveTool('delete'); },
                                title: 'Delete Node (Del)'
                            }, React.createElement('i', { className: 'fas fa-eraser' }), ' Delete')
                        )
                    ),
                ),
            ),
            /* ── Right panel: routes between agent config / tool config / mcp config ── */
            React.createElement(
                'div',
                {
                    className:
                        'w-full lg:w-80 xl:w-96 bg-white dark:bg-gray-800 rounded-lg shadow border border-gray-200 dark:border-gray-700 flex flex-col p-3 text-xs',
                },
                /* Panel header */
                React.createElement(
                    'div',
                    { className: 'flex items-center justify-between mb-2' },
                    React.createElement(
                        'h3',
                        { className: 'text-sm font-semibold text-gray-900 dark:text-white' },
                        selectedAttachment
                            ? (selectedAttachment.type === 'tool' ? 'Tool Settings' : 'MCP Settings')
                            : 'Agent Configuration',
                    ),
                    React.createElement(
                        'span',
                        { className: 'text-[11px] text-gray-500 dark:text-gray-400 truncate max-w-[180px]' },
                        selectedAttachment
                            ? selectedAttachment.label
                            : (selectedAgent ? selectedAgent.name : 'No agent selected'),
                    ),
                ),
                /* Tool settings panel */
                selectedAttachment && selectedAttachment.type === 'tool' &&
                React.createElement(ToolConfigPanel, {
                    attachment: selectedAttachment,
                    agent: agentsByName.get(selectedAttachment.agentName) || null,
                    onClose: function () { setSelectedAttachment(null); setSelectedAgentName(selectedAttachment.agentName); },
                    onOpenToolPicker: function (agentName) { setToolPickerModal({ agentName: agentName }); },
                    saving: saving,
                }),
                /* MCP settings panel */
                selectedAttachment && selectedAttachment.type === 'mcp' &&
                React.createElement(McpConfigPanel, {
                    attachment: selectedAttachment,
                    agent: agentsByName.get(selectedAttachment.agentName) || null,
                    onClose: function () { setSelectedAttachment(null); setSelectedAgentName(selectedAttachment.agentName); },
                    onSaveAgent: handleSaveAgent,
                    saving: saving,
                }),
                /* Basic config panel */
                !selectedAttachment &&
                React.createElement(ConfigPanel, {
                    agent: draftAgent,
                    onChange: handleDraftChange,
                    onSave: handleSave,
                    onDelete: handleDelete,
                    saving: saving,
                    projectId: props.projectId,
                    onAdvancedSetup: function () {
                        if (draftAgent && draftAgent.id) {
                            if (typeof window.editAgent === 'function') {
                                window.editAgent(draftAgent);
                            } else {
                                console.error('editAgent function not found');
                            }
                        }
                    },
                    onOpenToolPicker: function (agentName) { setToolPickerModal({ agentName: agentName }); },
                    onSelectMcpServer: function (serverName) {
                        if (!draftAgent) return;
                        setSelectedAttachment({ nodeId: '__mcp__' + draftAgent.name + '__' + serverName, type: 'mcp', agentName: draftAgent.name, label: serverName });
                        setSelectedAgentName(null);
                    },
                    onAddMcpServer: function (agentName) { setMcpAddModal({ agentName: agentName }); },
                    onOpenFileSearch: function () {
                        if (!draftAgent) return;
                        var nameInput = document.getElementById('editAgentName');
                        var projectInput = document.getElementById('editAgentProject');
                        if (nameInput) nameInput.value = draftAgent.name;
                        if (projectInput) projectInput.value = String(draftAgent.project_id || '');
                        if (typeof window.openFileSearchModal === 'function') {
                            window.openFileSearchModal('editAgent');
                        }
                    },
                }),
            ),
            /* Agent create modal */
            createModal && React.createElement(AgentCreateModal, {
                onSubmit: async function (name) {
                    setCreateModal(null);
                    if (agentsByName.has(name)) {
                        if (typeof window.showNotification === 'function') window.showNotification('Agent already exists', 'error');
                        return;
                    }
                    const agent = {
                        id: null, name: name, type: 'llm',
                        project_id: props.projectId,
                        model_name: '', description: '', instruction: '',
                        parent_agents: [], allowed_for_roles: '["user","admin"]',
                        tool_config: '{}', mcp_servers_config: '{}',
                        planner_config: '{}', generate_content_config: '{}',
                        input_schema: '{}', output_schema: '{}',
                        include_contents: '', guardrail_config: '{}',
                        max_iterations: '', disabled: false, hardcoded: false,
                    };
                    setSaving(true);
                    try {
                        await apiCreateAgent(agent);
                        if (typeof window.showNotification === 'function') window.showNotification('Agent created');
                        await syncFromDb();
                    } catch (error) {
                        console.error('[AgentVisualBuilder] Failed to create agent', error);
                        if (typeof window.showNotification === 'function') window.showNotification('Failed to create agent', 'error');
                    } finally {
                        setSaving(false);
                    }
                },
                onCancel: function () { setCreateModal(null); },
            }),
            /* Tool picker modal is now rendered using HTML config_modals macro and triggered via effect */
            /* MCP add modal */
            mcpAddModal && React.createElement(McpAddModal, {
                agentName: mcpAddModal.agentName,
                onSave: async function (serverName, serverCfg) {
                    setMcpAddModal(null);
                    const agent = agentsByName.get(mcpAddModal.agentName);
                    if (!agent) return;
                    const allMcp = parseJson(agent.mcp_servers_config);
                    if (!allMcp.mcpServers) allMcp.mcpServers = {};
                    allMcp.mcpServers[serverName] = serverCfg;
                    await handleSaveAgent(Object.assign({}, agent, { mcp_servers_config: JSON.stringify(allMcp) }));
                },
                onCancel: function () { setMcpAddModal(null); },
            }),

        );
    }

    function bootstrap() {
        const container = document.getElementById('agentVisualRoot');
        if (!container) {
            return;
        }
        const initial = readInitialState();
        if (!initial.selectedProjectId) {
            if (typeof window.showNotification === 'function') {
                window.showNotification('Select a project on the Agents page, then open the visual builder.', 'info');
            }
        }
        const rootElement = React.createElement(AgentsVisualBuilderApp, {
            configs: initial.configs,
            projectId: initial.selectedProjectId,
        });
        ReactDOM.createRoot(container).render(rootElement);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrap);
    } else {
        bootstrap();
    }
})();

