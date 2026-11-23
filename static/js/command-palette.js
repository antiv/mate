/**
 * The command palette: Ctrl+K (⌘K on a Mac), or Search in the header, opens a box that
 * jumps to an agent, one of the user's recent conversations or a dashboard page
 * (templates/dashboard/partials/command_palette.html, which base.html includes).
 *
 * The pages are in the page as JSON, only those the user may open; the agents and the
 * conversations come from GET /dashboard/api/palette when the palette opens. Every word
 * typed has to match, in any case, with or without accents.
 *
 * Inside a code editor Ctrl+K stays the editor's.
 */
(function (root) {
    'use strict';

    const GROUPS = [
        { key: 'sessions', label: 'Recent conversations', empty: 5, found: 8 },
        { key: 'agents', label: 'Agents', empty: 5, found: 8 },
        { key: 'pages', label: 'Pages', empty: 6, found: 8 },
    ];
    const SEARCH_ORDER = ['agents', 'pages', 'sessions'];
    const FRESH_MS = 60 * 1000;

    /** Lower case without accents: "Ključevi" is found by "kljucevi". */
    function plain(text) {
        return String(text === null || text === undefined ? '' : text).normalize('NFD')
            .replace(/[̀-ͯ]/g, '').toLowerCase().replace(/đ/g, 'd');
    }

    const wordsOf = (text) => plain(text).split(/[^\p{L}\p{N}]+/u).filter(Boolean);

    /** How well an item matches, lower is better; null when it doesn't. */
    function score(item, query) {
        const typed = plain(query).trim();
        const words = typed.split(/\s+/).filter(Boolean);
        const label = plain(item.label);
        const all = `${label} ${plain(item.keywords)}`;
        if (!words.every((w) => all.includes(w))) return null;
        if (!words.length) return 0;
        if (label === typed) return 0;
        if (label.startsWith(typed)) return 1;
        const starts = wordsOf(label);
        if (words.every((w) => starts.some((s) => s.startsWith(w)))) return 2;
        if (words.every((w) => label.includes(w))) return 3;
        return 4;
    }

    /**
     * [{key, label, items}] of what matches, the groups without any left out. While
     * searching, the group with the closest match comes first; of equals the agents,
     * then the pages: a conversation is often named after its agent.
     */
    function match(data, query) {
        const searching = Boolean(plain(query).trim());
        const groups = GROUPS.map((group) => {
            const matches = (data[group.key] || [])
                .map((item, i) => ({ item, i, score: score(item, query) }))
                .filter((m) => m.score !== null)
                .sort((a, b) => a.score - b.score || a.i - b.i)
                .slice(0, searching ? group.found : group.empty);
            return { key: group.key, label: group.label, items: matches.map((m) => m.item),
                     best: matches.length ? matches[0].score : null };
        }).filter((group) => group.items.length);
        if (searching) groups.sort((a, b) => a.best - b.best || SEARCH_ORDER.indexOf(a.key) - SEARCH_ORDER.indexOf(b.key));
        return groups;
    }

    const api = { plain, score, match };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
        return;
    }

    const el = (id) => document.getElementById(id);
    const mac = /Mac|iPhone|iPad/.test(navigator.platform || '');
    const data = { sessions: [], agents: [], pages: [] };
    let loadedAt = 0;
    let failed = false;
    let active = 0;

    function options() {
        return Array.from(el('commandPaletteList').querySelectorAll('[role="option"]'));
    }

    function choose(index) {
        const all = options();
        active = all.length ? (index + all.length) % all.length : 0;
        all.forEach((option, i) => option.setAttribute('aria-selected', String(i === active)));
        const input = el('commandPaletteInput');
        if (all.length) {
            input.setAttribute('aria-activedescendant', all[active].id);
            all[active].scrollIntoView({ block: 'nearest' });
        } else {
            input.removeAttribute('aria-activedescendant');
        }
    }

    function option(item, id) {
        const link = document.createElement('a');
        link.className = 'm-palette-option';
        link.id = id;
        link.href = item.url;
        link.tabIndex = -1;
        link.setAttribute('role', 'option');
        const icon = document.createElement('i');
        icon.className = `fas ${item.icon}`;
        icon.setAttribute('aria-hidden', 'true');
        const label = document.createElement('span');
        label.className = 'm-palette-label';
        label.textContent = item.label;
        const hint = document.createElement('span');
        hint.className = 'm-palette-hint';
        hint.textContent = item.hint || '';
        link.append(icon, label, hint);
        return link;
    }

    function render() {
        const query = el('commandPaletteInput').value;
        const groups = match(data, query);
        const list = el('commandPaletteList');
        let n = 0;
        list.replaceChildren(...groups.map((group) => {
            const box = document.createElement('div');
            box.setAttribute('role', 'group');
            box.setAttribute('aria-labelledby', `palette-group-${group.key}`);
            const title = document.createElement('p');
            title.className = 'm-palette-group';
            title.id = `palette-group-${group.key}`;
            title.textContent = group.label;
            box.append(title, ...group.items.map((item) => option(item, `palette-option-${n++}`)));
            return box;
        }));
        const note = el('commandPaletteNote');
        note.textContent = failed ? 'Agents and conversations could not be loaded.'
            : !loadedAt ? 'Loading agents and conversations…'
                : n ? '' : `Nothing matches “${query.trim()}”.`;
        note.hidden = !note.textContent;
        el('commandPaletteInput').setAttribute('aria-expanded', String(n > 0));
        choose(0);
    }

    async function load() {
        if (loadedAt && Date.now() - loadedAt < FRESH_MS) return;
        try {
            const response = await fetch('/dashboard/api/palette', { credentials: 'same-origin' });
            if (!response.ok) throw new Error(String(response.status));
            const found = await response.json();
            data.agents = (found.agents || []).map((a) => ({ label: a.name, keywords: `${a.description} ${a.project}`,
                                                             hint: a.project, url: a.url, icon: 'fa-robot' }));
            data.sessions = (found.sessions || []).map((s) => ({
                label: s.title || `Conversation with ${s.agent}`, keywords: s.agent, url: s.url, icon: 'fa-comment',
                hint: [s.agent, s.updated_at && typeof formatDateTime === 'function' ? formatDateTime(s.updated_at) : ''].filter(Boolean).join(' · ') }));
            failed = false;
            loadedAt = Date.now();
        } catch (e) {
            failed = true;
        }
        if (!el('commandPalette').hidden) render();
    }

    function open() {
        const box = el('commandPalette');
        if (!box.hidden || document.querySelector('.m-dialog-backdrop:not([hidden])')) return;
        el('commandPaletteInput').value = '';
        mateDialog.open(box, { focus: el('commandPaletteInput') });
        render();
        load();
    }

    function close() {
        mateDialog.close('commandPalette');
    }

    function go(index, newTab) {
        const link = options()[index];
        if (!link) return;
        if (newTab) window.open(link.href, '_blank', 'noopener');
        else window.location.href = link.href;
        close();
    }

    function start() {
        const input = el('commandPaletteInput');
        if (!input) return;
        data.pages = JSON.parse(el('command-palette-pages').textContent || '[]');
        document.querySelectorAll('[data-palette-key]').forEach((k) => { k.textContent = mac ? '⌘K' : 'Ctrl K'; });

        input.addEventListener('input', render);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                choose(active + (event.key === 'ArrowDown' ? 1 : -1));
            } else if (event.key === 'Enter' && !event.isComposing) {
                event.preventDefault();
                go(active, event.metaKey || event.ctrlKey);
            }
        });
        const list = el('commandPaletteList');
        list.addEventListener('mousemove', (event) => {
            const link = event.target.closest('[role="option"]');
            const index = link ? options().indexOf(link) : -1;
            if (index >= 0 && index !== active) choose(index);
        });
        list.addEventListener('click', (event) => {
            if (event.target.closest('[role="option"]') && !event.metaKey && !event.ctrlKey && !event.shiftKey) close();
        });
        document.addEventListener('keydown', (event) => {
            const modifier = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
            if (!modifier || event.altKey || event.shiftKey || event.key.toLowerCase() !== 'k') return;
            if (event.target.closest && event.target.closest('.monaco-editor, .ace_editor')) return;
            event.preventDefault();
            if (el('commandPalette').hidden) open();
            else close();
        });
    }

    mateActions.allow('mateCommandPalette.open', 'mateCommandPalette.close');
    root.mateCommandPalette = { open, close };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})(typeof window !== 'undefined' ? window : globalThis);
