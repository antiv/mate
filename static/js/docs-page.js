/**
 * Documentation page: section tabs, page navigation, Markdown rendering and search.
 *
 * Pages come from /dashboard/api/docs (shared/utils/docs_service.py), which reads
 * the docs/ folder of the running installation. The address bar always reflects
 * what is shown (?page=user/triggers.md#anchor, or ?tab=api), so a page can be
 * linked to, reloaded and reached with the back button.
 */
const DocsPage = (() => {
    const API = '/dashboard/api/docs';
    const GUIDE_TABS = ['user', 'dev', 'reference'];
    const SECTION_LABELS = {user: 'User guide', dev: 'Developer guide', reference: 'Reference'};
    const SEARCH_DELAY_MS = 180;
    const MIN_QUERY_LENGTH = 2;

    const state = {
        pages: [],
        tab: null,
        path: null,
        apiStarted: false,
        results: [],
        activeResult: -1,
        searchSeq: 0,
        pageSeq: 0,
    };

    // html:false escapes raw HTML in a page instead of running it, and markdown-it
    // refuses javascript: links, so a page cannot inject script into the dashboard.
    const md = window.markdownit ? window.markdownit({html: false, linkify: true}) : null;

    const $ = (id) => document.getElementById(id);

    async function getJson(url) {
        const response = await fetch(url, {credentials: 'same-origin'});
        if (!response.ok) {
            const error = new Error(`Request failed (${response.status})`);
            error.status = response.status;
            throw error;
        }
        return response.json();
    }

    function el(tag, attrs, children) {
        const node = document.createElement(tag);
        for (const [key, value] of Object.entries(attrs || {})) {
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else node.setAttribute(key, value);
        }
        for (const child of children || []) node.append(child);
        return node;
    }

    // ---- Address bar ---------------------------------------------------- //

    function readLocation() {
        const params = new URLSearchParams(window.location.search);
        return {
            tab: params.get('tab'),
            path: params.get('page'),
            anchor: decodeURIComponent(window.location.hash.replace(/^#/, '')),
        };
    }

    function writeLocation({tab, path, anchor}, push) {
        const params = new URLSearchParams();
        if (tab === 'api') params.set('tab', 'api');
        else if (path) params.set('page', path);
        const query = params.toString().replace(/%2F/g, '/');
        const url = window.location.pathname + (query ? `?${query}` : '') + (anchor ? `#${encodeURIComponent(anchor)}` : '');
        if (url === window.location.pathname + window.location.search + window.location.hash) return;
        window.history[push ? 'pushState' : 'replaceState'](null, '', url);
    }

    // ---- Tabs and navigation --------------------------------------------- //

    function pagesIn(section) {
        return state.pages.filter((page) => page.section === section);
    }

    function showTab(tab) {
        state.tab = tab;
        document.querySelectorAll('.docs-tab').forEach((button) => {
            button.setAttribute('aria-selected', String(button.dataset.tab === tab));
        });
        const isApi = tab === 'api';
        $('docsGuides').classList.toggle('hidden', isApi);
        $('docsApi').classList.toggle('hidden', !isApi);
        if (isApi) {
            if (!state.apiStarted && typeof window.initApiExplorer === 'function') {
                state.apiStarted = true;
                window.initApiExplorer();
            }
            return;
        }
        renderNav(tab);
    }

    function renderNav(section) {
        const pages = pagesIn(section);
        const nav = $('docsNav');
        const select = $('docsPageSelect');
        nav.replaceChildren();
        select.replaceChildren();
        for (const page of pages) {
            const link = el('a', {
                class: 'docs-nav-link',
                href: `?page=${page.path}`,
                title: page.summary || page.title,
                text: page.title,
            });
            link.dataset.path = page.path;
            if (page.path === state.path) link.setAttribute('aria-current', 'page');
            nav.append(link);
            const option = el('option', {value: page.path, text: page.title});
            option.selected = page.path === state.path;
            select.append(option);
        }
        select.classList.toggle('hidden', pages.length < 2);
    }

    function selectTab(tab, push) {
        if (tab === 'api') {
            showTab('api');
            writeLocation({tab: 'api'}, push);
            return;
        }
        const pages = pagesIn(tab);
        const current = state.pages.find((page) => page.path === state.path);
        if (current && current.section === tab) {
            showTab(tab);
            writeLocation({path: state.path}, push);
        } else if (pages.length) {
            openPage(pages[0].path, {push});
        } else {
            state.path = null;
            showTab(tab);
            showEmpty(`No ${SECTION_LABELS[tab].toLowerCase()} pages yet.`,
                      `Pages added under docs/${tab}/ appear here.`);
            writeLocation({}, push);
        }
    }

    // ---- Article -------------------------------------------------------- //

    function showEmpty(title, detail) {
        $('docsNotice').classList.add('hidden');
        $('docsToc').replaceChildren();
        $('docsNavToc').replaceChildren();
        $('docsNavToc').classList.add('hidden');
        $('docsGuides').classList.remove('docs-no-toc');
        $('docsTocTitle').classList.add('hidden');
        const article = $('docsArticle');
        article.classList.remove('is-wide');
        article.replaceChildren(el('h1', {text: title}), el('p', {text: detail}));
    }

    function renderMarkdown(markdown) {
        const article = $('docsArticle');
        if (md) {
            article.innerHTML = md.render(markdown);
        } else {
            // The renderer is loaded from a CDN. Without it the page is still readable.
            article.replaceChildren(el('pre', {text: markdown}));
        }
        article.querySelectorAll('table').forEach((table) => {
            const wrap = el('div', {class: 'docs-table-wrap'});
            table.replaceWith(wrap);
            wrap.append(table);
        });
        article.querySelectorAll('a[href]').forEach((link) => {
            if (/^https?:\/\//i.test(link.getAttribute('href'))) {
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
            }
        });
    }

    function slugify(text) {
        return text.toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu, '').trim().replace(/\s/g, '-');
    }

    function assignHeadingIds(headings) {
        const elements = $('docsArticle').querySelectorAll('h1, h2, h3, h4, h5, h6');
        // The server computed the anchors that search results and links use. They are
        // applied in order; if the two parsers ever disagree, fall back to local slugs.
        if (elements.length === headings.length) {
            elements.forEach((heading, index) => { heading.id = headings[index].anchor; });
            return;
        }
        const seen = new Map();
        elements.forEach((heading) => {
            const base = slugify(heading.textContent) || 'section';
            const count = seen.get(base) || 0;
            seen.set(base, count + 1);
            heading.id = count ? `${base}-${count}` : base;
        });
    }

    /**
     * List the page's sections. A guide shows them in the right-hand column. A generated
     * reference page is mostly wide tables, so it takes that column for itself and its
     * sections go under the page list instead.
     */
    function renderToc(wide) {
        const aside = $('docsToc');
        const inNav = $('docsNavToc');
        aside.replaceChildren();
        inNav.replaceChildren();
        const target = wide ? inNav : aside;
        const headings = $('docsArticle').querySelectorAll(wide ? 'h2[id]' : 'h2[id], h3[id]');
        headings.forEach((heading) => {
            target.append(el('a', {
                class: 'docs-toc-link' + (heading.tagName === 'H3' ? ' pl-3' : ''),
                href: `#${heading.id}`,
                text: heading.textContent,
            }));
        });
        $('docsGuides').classList.toggle('docs-no-toc', wide);
        $('docsTocTitle').classList.toggle('hidden', wide || headings.length === 0);
        inNav.classList.toggle('hidden', !wide || headings.length === 0);
    }

    function searchTerms(query) {
        return (query.toLowerCase().match(/[\p{L}\p{N}]+/gu) || [])
            .filter((term) => term.length >= MIN_QUERY_LENGTH);
    }

    function termPattern(terms) {
        const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
        // A term matches where a word starts, as it does on the server. The first group
        // stands in for a lookbehind, which older Safari versions do not support.
        return new RegExp(`(^|[^\\p{L}\\p{N}])(${escaped.join('|')})`, 'giu');
    }

    /** Append `text` to `parent`, wrapping every term match in <mark>. */
    function appendHighlighted(parent, text, terms) {
        if (!terms.length) {
            parent.append(text);
            return 0;
        }
        const pattern = termPattern(terms);
        let last = 0;
        let count = 0;
        let match;
        while ((match = pattern.exec(text)) !== null) {
            const start = match.index + match[1].length;
            parent.append(text.slice(last, start));
            parent.append(el('mark', {text: match[2]}));
            last = start + match[2].length;
            count += 1;
            if (match[0].length === 0) pattern.lastIndex += 1;
        }
        parent.append(text.slice(last));
        return count;
    }

    function highlightArticle(terms) {
        if (!terms.length) return;
        const walker = document.createTreeWalker($('docsArticle'), NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        for (const node of nodes) {
            const fragment = document.createDocumentFragment();
            if (appendHighlighted(fragment, node.nodeValue, terms)) node.replaceWith(fragment);
        }
    }

    /**
     * The block a search result should land on: the first one, at or after the result's
     * section, that contains the query as typed; failing that, one containing every
     * term; failing that, the first one with any highlighted term.
     */
    function findMatchBlock(heading, terms, phrase) {
        const blocks = Array.from($('docsArticle').querySelectorAll('h1, h2, h3, h4, h5, h6, p, li, tr, pre'))
            .filter((block) => !heading || block === heading
                || (heading.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING));
        const text = (block) => block.textContent.toLowerCase();
        return blocks.find((block) => phrase && text(block).includes(phrase))
            || blocks.find((block) => terms.every((term) => text(block).includes(term)))
            || blocks.find((block) => block.querySelector('mark'))
            || null;
    }

    function scrollToTarget(anchor, search) {
        const heading = anchor ? document.getElementById(anchor) : null;
        let target = heading;
        if (search && search.terms.length) {
            const block = findMatchBlock(heading, search.terms, search.phrase);
            // Showing the section heading is better when the match is visible with it.
            if (block && !(heading && isNear(heading, block))) target = block;
        }
        if (!target) window.scrollTo({top: 0});
        else target.scrollIntoView({block: target === heading ? 'start' : 'center'});
    }

    /** True when `node` sits close enough below `heading` to be visible with it. */
    function isNear(heading, node) {
        const gap = node.getBoundingClientRect().top - heading.getBoundingClientRect().top;
        return gap >= 0 && gap < window.innerHeight * 0.6;
    }

    async function openPage(path, options) {
        const {anchor = '', push = false, query = ''} = options || {};
        const terms = searchTerms(query);
        const seq = ++state.pageSeq;
        let page;
        try {
            page = await getJson(`${API}/page?path=${encodeURIComponent(path)}`);
        } catch (error) {
            if (seq !== state.pageSeq) return;
            state.path = null;
            showTab(GUIDE_TABS.includes(state.tab) ? state.tab : 'user');
            if (error.status === 404) {
                showEmpty('Page not found', `There is no documentation page at ${path}. It may have been renamed or removed.`);
            } else {
                showEmpty('Could not load this page', `${error.message}. Check that the server is running, then reload.`);
            }
            return;
        }
        if (seq !== state.pageSeq) return;

        state.path = page.path;
        showTab(page.section);
        document.title = `${page.title} - Documentation - MATE`;

        const notice = $('docsNotice');
        const notes = {
            generated: 'Generated from the code. To change it, change the code and run scripts/gen_docs.py.',
            migrated: 'Moved from the earlier documentation and not yet re-checked against the code. Details may be out of date.',
        };
        const note = page.generated ? notes.generated : (notes[page.status] || '');
        notice.classList.toggle('hidden', !note);
        notice.textContent = note;

        const article = $('docsArticle');
        article.classList.toggle('is-wide', page.generated);
        renderMarkdown(page.markdown);
        assignHeadingIds(page.headings);
        renderToc(page.generated);
        highlightArticle(terms);
        writeLocation({path: page.path, anchor}, push);
        scrollToTarget(anchor, {terms, phrase: query.trim().toLowerCase()});
    }

    /** Resolve a link inside the current page to a docs path, or null if it leaves docs/. */
    function resolveDocLink(href) {
        const base = new URL(`http://docs.invalid/${state.path || ''}`);
        let url;
        try { url = new URL(href, base); } catch (error) { return null; }
        if (url.origin !== base.origin) return null;
        return {
            path: decodeURIComponent(url.pathname.replace(/^\//, '')),
            anchor: decodeURIComponent(url.hash.replace(/^#/, '')),
        };
    }

    function onArticleClick(event) {
        const link = event.target.closest('a[href]');
        if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        const href = link.getAttribute('href');
        if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return;  // http:, mailto: and friends
        event.preventDefault();
        if (href.startsWith('#')) {
            const anchor = decodeURIComponent(href.slice(1));
            writeLocation({path: state.path, anchor}, true);
            scrollToTarget(anchor, null);
            return;
        }
        const target = resolveDocLink(href);
        if (target && state.pages.some((page) => page.path === target.path)) {
            openPage(target.path, {anchor: target.anchor, push: true});
        } else if (typeof showNotification === 'function') {
            showNotification('That link points outside the documentation.', 'error');
        }
    }

    // ---- Search --------------------------------------------------------- //

    function closeResults() {
        $('docsResults').classList.add('hidden');
        $('docsSearch').setAttribute('aria-expanded', 'false');
        state.activeResult = -1;
    }

    function setActiveResult(index) {
        const buttons = $('docsResults').querySelectorAll('.docs-result');
        if (!buttons.length) return;
        state.activeResult = (index + buttons.length) % buttons.length;
        buttons.forEach((button, i) => {
            const active = i === state.activeResult;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', String(active));
            if (active) button.scrollIntoView({block: 'nearest'});
        });
    }

    function openResult(index) {
        const result = state.results[index];
        if (!result) return;
        const query = $('docsSearch').value;
        closeResults();
        openPage(result.path, {anchor: result.anchor, push: true, query});
    }

    function renderResults(query, results) {
        const panel = $('docsResults');
        const terms = searchTerms(query);
        state.results = results;
        state.activeResult = -1;
        panel.replaceChildren();
        if (!results.length) {
            panel.append(el('p', {
                class: 'px-3 py-3 text-gray-500 dark:text-gray-400',
                text: `Nothing matches "${query}". Every word has to match; try fewer words.`,
            }));
        }
        results.forEach((result, index) => {
            const title = el('div', {class: 'font-medium text-gray-900 dark:text-white'});
            appendHighlighted(title, result.heading ? `${result.title} › ${result.heading}` : result.title, terms);
            const snippet = el('div', {class: 'text-xs text-gray-600 dark:text-gray-400 mt-0.5'});
            appendHighlighted(snippet, result.snippet, terms);
            const section = el('div', {
                class: 'text-[11px] text-gray-400 dark:text-gray-500 mt-0.5',
                text: SECTION_LABELS[result.section] || result.section,
            });
            const button = el('button', {type: 'button', class: 'docs-result', role: 'option', 'aria-selected': 'false'},
                              [title, snippet, section]);
            button.addEventListener('click', () => openResult(index));
            panel.append(button);
        });
        panel.classList.remove('hidden');
        $('docsSearch').setAttribute('aria-expanded', 'true');
    }

    async function runSearch(query) {
        const seq = ++state.searchSeq;
        if (query.trim().length < MIN_QUERY_LENGTH) {
            closeResults();
            return;
        }
        try {
            const data = await getJson(`${API}/search?q=${encodeURIComponent(query)}&limit=12`);
            if (seq === state.searchSeq) renderResults(query, data.results || []);
        } catch (error) {
            if (seq !== state.searchSeq) return;
            $('docsResults').replaceChildren(el('p', {
                class: 'px-3 py-3 text-red-600 dark:text-red-400',
                text: `Search failed: ${error.message}.`,
            }));
            $('docsResults').classList.remove('hidden');
        }
    }

    function bindSearch() {
        const input = $('docsSearch');
        let timer = null;
        input.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(() => runSearch(input.value), SEARCH_DELAY_MS);
        });
        input.addEventListener('focus', () => {
            if (state.results.length && input.value.trim().length >= MIN_QUERY_LENGTH) {
                $('docsResults').classList.remove('hidden');
                input.setAttribute('aria-expanded', 'true');
            }
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown') { event.preventDefault(); setActiveResult(state.activeResult + 1); }
            else if (event.key === 'ArrowUp') { event.preventDefault(); setActiveResult(state.activeResult - 1); }
            else if (event.key === 'Enter') { event.preventDefault(); openResult(Math.max(state.activeResult, 0)); }
            else if (event.key === 'Escape') { closeResults(); input.blur(); }
        });
        document.addEventListener('click', (event) => {
            if (!$('docsSearchBox').contains(event.target)) closeResults();
        });
        document.addEventListener('keydown', (event) => {
            const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)
                || document.activeElement.isContentEditable;
            if (event.key === '/' && !typing && !event.metaKey && !event.ctrlKey) {
                event.preventDefault();
                input.focus();
                input.select();
            }
        });
    }

    // ---- Start ---------------------------------------------------------- //

    function applyLocation() {
        const {tab, path, anchor} = readLocation();
        if (tab === 'api') {
            showTab('api');
        } else if (path) {
            openPage(path, {anchor});
        } else {
            const first = GUIDE_TABS.find((section) => pagesIn(section).length);
            if (first) openPage(pagesIn(first)[0].path);
            else {
                showTab('user');
                showEmpty('No documentation found',
                          'This installation has no docs/ folder. It ships with the repository and the Docker image; a custom build may have left it out.');
            }
        }
    }

    async function init() {
        document.querySelectorAll('.docs-tab').forEach((button) => {
            button.addEventListener('click', () => selectTab(button.dataset.tab, true));
        });
        $('docsNav').addEventListener('click', (event) => {
            const link = event.target.closest('a[data-path]');
            if (!link || event.metaKey || event.ctrlKey || event.shiftKey) return;
            event.preventDefault();
            openPage(link.dataset.path, {push: true});
        });
        $('docsPageSelect').addEventListener('change', (event) => openPage(event.target.value, {push: true}));
        $('docsArticle').addEventListener('click', onArticleClick);
        $('docsToc').addEventListener('click', onArticleClick);
        $('docsNavToc').addEventListener('click', onArticleClick);
        window.addEventListener('popstate', applyLocation);
        bindSearch();

        try {
            state.pages = (await getJson(`${API}/pages`)).pages || [];
        } catch (error) {
            showTab('user');
            showEmpty('Could not load the documentation', `${error.message}. Reload the page to try again.`);
            return;
        }
        applyLocation();
    }

    document.addEventListener('DOMContentLoaded', init);

    return {openPage, selectTab};
})();
