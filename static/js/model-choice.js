/**
 * Model or Group, for the agent page (agent-page.js) and the Visual Builder: the model
 * groups the workspace's agents can be on, and which model answers now. Both stand in one
 * field of the agent, model_name: "group/<name>" names a group.
 *
 * "group/" alone is Group chosen and no group yet: saving it is refused.
 */
(function () {
    'use strict';

    const PREFIX = 'group/';
    let loading = null;

    /** {groups, default_model} of the workspace; null when they did not load. */
    function load() {
        if (!loading) {
            loading = fetch('/dashboard/api/workspace-models/groups', { credentials: 'same-origin' })
                .then((response) => (response.ok ? response.json() : null))
                .catch(() => null);
        }
        return loading;
    }

    const isGroup = (modelName) => String(modelName || '').trim().startsWith(PREFIX);

    function find(modelName, data) {
        return ((data && data.groups) || []).find((g) => g.model_name === String(modelName || '').trim());
    }

    /** What the list of groups offers: [{value, text, disabled}], the agent's own always in it. */
    function options(modelName, data) {
        const list = [{ value: PREFIX, text: 'Choose a group', disabled: false }];
        ((data && data.groups) || []).forEach((g) => {
            list.push({ value: g.model_name, disabled: !g.has_models,
                        text: `${g.label} (${g.model_name})${g.has_models ? '' : ', no models'}` });
        });
        const own = String(modelName || '').trim();
        if (isGroup(own) && !list.some((o) => o.value === own)) {
            list.push({ value: own, text: data ? `${own}, no such group` : own, disabled: false });
        }
        return list;
    }

    /** The line under the field: {text, error}. hasEndpoint: the agent has a base URL or a key of its own. */
    function hint(modelName, data, hasEndpoint) {
        if (!isGroup(modelName)) {
            const empty = !String(modelName || '').trim();
            return { text: empty && data ? `Empty: ${data.default_model}, the server's default.` : '', error: false };
        }
        if (hasEndpoint) {
            return { text: 'A group takes no base URL or API key: clear both under Model endpoint.', error: true };
        }
        if (!data) return { text: 'The model groups did not load.', error: true };
        if (String(modelName).trim() === PREFIX) {
            return data.groups.some((g) => g.has_models)
                ? { text: 'The first model of the group that this workspace has a key for answers.', error: false }
                : { text: 'No group has models yet. A platform administrator adds them under Model groups.', error: false };
        }
        const group = find(modelName, data);
        if (!group) return { text: `There is no model group ${modelName}. Choose another one, or a model.`, error: true };
        if (!group.answers) return { text: `${group.reason}.`, error: true };
        return { text: `Answers now: ${group.answers}${group.own_servers_only ? ' (own servers only)' : ''}`, error: false };
    }

    window.mateModelChoice = { PREFIX, load, isGroup, options, hint };
})();
