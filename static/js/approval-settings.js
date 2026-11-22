/**
 * The editor of an agent's approval settings: require_confirmation in its tool_config
 * (shared/utils/approval_config.py), in the dialog of
 * templates/dashboard/modals/approval_settings_modal.html.
 *
 *   mateApprovalSettings.open(agent, async (toolConfigJson) => saved)
 *
 * The callback saves the agent with the new tool_config and resolves to true when it
 * was saved; the dialog stays open otherwise. The settings are written as the list of
 * tools when nothing else is set, else as the object.
 */
if (typeof mateActions !== 'undefined') {
    mateActions.allow('mateApprovalSettings.close', 'mateApprovalSettings.save',
                      'mateApprovalSettings.showTarget');
}
(function (root) {
    'use strict';

    const KEY = 'require_confirmation';
    const APPROVERS = ['chat', 'inbox', 'either'];
    const NOTIFY_TYPES = ['http', 'email', 'slack', 'discord'];
    const MAX_MINUTES = 60 * 24 * 90;

    /** The settings in a tool_config object, as approval_config.parse reads them. */
    function read(toolConfig) {
        const raw = (toolConfig || {})[KEY];
        const settings = { tools: [], approver: 'chat', notify: null, expires: null };
        if (!raw) return settings;
        if (typeof raw === 'string') return Object.assign(settings, { tools: [raw] });
        if (Array.isArray(raw)) return Object.assign(settings, { tools: raw.filter((t) => typeof t === 'string' && t) });
        if (typeof raw !== 'object') return settings;
        const tools = typeof raw.tools === 'string' ? [raw.tools] : (Array.isArray(raw.tools) ? raw.tools : []);
        settings.tools = tools.filter((t) => typeof t === 'string' && t);
        if (APPROVERS.includes(raw.approver)) settings.approver = raw.approver;
        if (raw.notify && typeof raw.notify === 'object' && NOTIFY_TYPES.includes(raw.notify.type)) settings.notify = raw.notify;
        if (Number.isInteger(raw.expires_after_minutes) && raw.expires_after_minutes > 0
            && raw.expires_after_minutes <= MAX_MINUTES) settings.expires = raw.expires_after_minutes;
        return settings;
    }

    /** The tool names in what was typed: split on commas and white space, without repeats. */
    function toolNames(text) {
        const seen = [];
        String(text || '').split(/[\s,]+/).forEach((t) => { if (t && !seen.includes(t)) seen.push(t); });
        return seen;
    }

    /**
     * A copy of toolConfig with the settings written in, or {error} when they don't validate.
     * No tools turns approvals off and drops the entry, if nothing else was set.
     */
    function write(toolConfig, settings) {
        const out = Object.assign({}, toolConfig || {});
        const tools = settings.tools || [];
        const approver = settings.approver || 'chat';
        const notify = settings.notify || null;
        const expires = settings.expires;
        const extras = approver !== 'chat' || notify || expires !== null && expires !== undefined && expires !== '';
        if (!tools.length) {
            if (extras) return { error: 'Name at least one tool, or clear the other settings.' };
            delete out[KEY];
            return { toolConfig: out };
        }
        if (!APPROVERS.includes(approver)) return { error: 'Choose who decides.' };
        let minutes = null;
        if (expires !== null && expires !== undefined && expires !== '') {
            minutes = Number(expires);
            if (!Number.isInteger(minutes) || minutes < 1 || minutes > MAX_MINUTES) {
                return { error: `The wait must be a whole number of minutes, from 1 to ${MAX_MINUTES}.` };
            }
        }
        if (notify) {
            if (!NOTIFY_TYPES.includes(notify.type)) return { error: 'Choose where to send the message.' };
            const target = notify.type === 'email' ? notify.to : notify.url;
            if (!String(target || '').trim()) {
                return { error: notify.type === 'email' ? 'Enter the email address.' : 'Enter the webhook URL.' };
            }
        }
        if (!extras) {
            out[KEY] = tools.slice();
        } else {
            const value = { tools: tools.slice(), approver };
            if (notify) value.notify = notify;
            if (minutes !== null) value.expires_after_minutes = minutes;
            out[KEY] = value;
        }
        return { toolConfig: out };
    }

    /** One line for the agent's panel: "Off", or "2 tools · inbox · notifies". */
    function summary(toolConfig) {
        const s = read(toolConfig);
        if (!s.tools.length) return 'Off';
        const parts = [s.tools.length === 1 ? '1 tool' : `${s.tools.length} tools`];
        parts.push({ chat: 'the chat decides', inbox: 'an administrator decides', either: 'chat or administrator' }[s.approver]);
        if (s.notify) parts.push(`tells ${s.notify.type}`);
        return parts.join(' · ');
    }

    // ── The dialog ───────────────────────────────────────────

    let current = null;   // {toolConfig, onSave, notify}

    function el(id) { return document.getElementById(id); }

    function showTarget() {
        const type = el('approvalSettingsNotifyType').value;
        el('approvalSettingsTargetField').hidden = !type;
        el('approvalSettingsTargetLabel').textContent = type === 'email' ? 'Email address' : 'Webhook URL';
        el('approvalSettingsTarget').type = type === 'email' ? 'email' : 'url';
    }

    function open(agent, onSave) {
        let toolConfig = {};
        try { toolConfig = JSON.parse((agent && agent.tool_config) || '{}') || {}; } catch (e) { toolConfig = {}; }
        const s = read(toolConfig);
        current = { toolConfig, onSave, notify: s.notify };
        el('approvalSettingsTitle').textContent = `Approvals: ${agent ? agent.name : ''}`;
        el('approvalSettingsTools').value = s.tools.join(', ');
        el('approvalSettingsApprover').value = s.approver;
        el('approvalSettingsNotifyType').value = s.notify ? s.notify.type : '';
        el('approvalSettingsTarget').value = s.notify ? (s.notify.type === 'email' ? s.notify.to : s.notify.url) || '' : '';
        el('approvalSettingsExpires').value = s.expires === null ? '' : String(s.expires);
        el('approvalSettingsError').hidden = true;
        el('approvalSettingsSave').disabled = false;
        showTarget();
        mateDialog.open('approvalSettingsDialog', { focus: 'approvalSettingsTools', onClose: () => { current = null; } });
    }

    function close() {
        mateDialog.close('approvalSettingsDialog');
    }

    function _fail(message) {
        const error = el('approvalSettingsError');
        error.textContent = message;
        error.hidden = false;
    }

    async function save() {
        if (!current) return;
        const type = el('approvalSettingsNotifyType').value;
        let notify = null;
        if (type) {
            // Keep what the dialog doesn't show (headers, timeout, subject) while the type stays
            notify = current.notify && current.notify.type === type ? Object.assign({}, current.notify) : { type };
            const target = el('approvalSettingsTarget').value.trim();
            delete notify.url;
            delete notify.to;
            notify[type === 'email' ? 'to' : 'url'] = target;
        }
        const result = write(current.toolConfig, {
            tools: toolNames(el('approvalSettingsTools').value),
            approver: el('approvalSettingsApprover').value,
            notify,
            expires: el('approvalSettingsExpires').value.trim(),
        });
        if (result.error) {
            _fail(result.error);
            return;
        }
        const button = el('approvalSettingsSave');
        button.disabled = true;
        el('approvalSettingsError').hidden = true;
        let saved = false;
        try {
            saved = await current.onSave(JSON.stringify(result.toolConfig));
        } catch (err) {
            _fail(err && err.message ? err.message : String(err));
        }
        button.disabled = false;
        if (saved) close();
    }

    const api = { open, close, save, showTarget, read, write, summary, toolNames };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.mateApprovalSettings = api;
})(typeof window !== 'undefined' ? window : globalThis);
