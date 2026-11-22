/**
 * Tabs on the m-tabs component (static/css/components.css), set up for every
 * [data-m-tabs] on the page:
 *
 *   <div class="m-tabs" role="tablist" data-m-tabs aria-label="Approvals">
 *     <button class="m-tab" role="tab" id="tab-waiting" aria-controls="panel-waiting" aria-selected="true">Waiting</button>
 *     <button class="m-tab" role="tab" id="tab-history" aria-controls="panel-history" aria-selected="false">History</button>
 *   </div>
 *   <div id="panel-waiting" role="tabpanel" aria-labelledby="tab-waiting">…</div>
 *   <div id="panel-history" role="tabpanel" aria-labelledby="tab-history" hidden>…</div>
 *
 * A click or the arrow keys, Home and End select a tab; only the selected one is in
 * the Tab order. The tab list then fires "m-tab-change" with detail {id} (the tab's id).
 * mateTabs.select('tab-history') selects one from code.
 */
(function () {
    'use strict';

    function tabsOf(list) {
        return Array.from(list.querySelectorAll('[role="tab"]'));
    }

    function select(tab, focus) {
        if (typeof tab === 'string') tab = document.getElementById(tab);
        if (!tab) return;
        const list = tab.closest('[role="tablist"]');
        const changed = tab.getAttribute('aria-selected') !== 'true';
        tabsOf(list).forEach((t) => {
            const on = t === tab;
            t.setAttribute('aria-selected', String(on));
            t.tabIndex = on ? 0 : -1;
            const panel = document.getElementById(t.getAttribute('aria-controls'));
            if (panel) panel.hidden = !on;
        });
        if (focus) tab.focus();
        if (changed) list.dispatchEvent(new CustomEvent('m-tab-change', { detail: { id: tab.id } }));
    }

    function init(list) {
        if (list.dataset.mTabsReady) return;
        list.dataset.mTabsReady = '1';
        const tabs = tabsOf(list);
        const current = tabs.find((t) => t.getAttribute('aria-selected') === 'true') || tabs[0];
        tabs.forEach((t) => { t.tabIndex = t === current ? 0 : -1; });
        list.addEventListener('click', (event) => {
            const tab = event.target.closest('[role="tab"]');
            if (tab && list.contains(tab)) select(tab, false);
        });
        list.addEventListener('keydown', (event) => {
            const all = tabsOf(list);
            const i = all.indexOf(document.activeElement);
            if (i < 0) return;
            const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: all.length - 1 }[event.key];
            if (next === undefined) return;
            event.preventDefault();
            select(all[(next + all.length) % all.length], true);
        });
    }

    function initAll() {
        document.querySelectorAll('[data-m-tabs]').forEach(init);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAll);
    else initAll();

    window.mateTabs = { init, select: (id) => select(id, false) };
})();
