/*
 * Event delegation in place of inline on*= handlers, which the strict
 * Content-Security-Policy blocks (see server/csp.py).
 *
 *   <button data-click="closeModal">                    closeModal()
 *   <button data-click="controlAdkServer" data-args='["start"]'>
 *   <a data-click="showTokensModal" data-args='["$event"]'>   gets the event
 *   <select data-change="applyFilters">
 *
 * In data-args, "$event" is replaced by the event, "$el" by the element and
 * "$value" by the element's value (what `this.value` was in an inline handler).
 * data-prevent calls event.preventDefault() before the handler, data-stop
 * event.stopPropagation() after it.
 * The handler runs with `this` set to the element, as an inline handler did.
 * Handlers on nested elements run innermost first, and event.stopPropagation()
 * in one stops the outer ones, also as before.
 *
 * Only functions a page allows by name can be called this way, so markup that
 * gets injected into a page cannot call an arbitrary global:
 *
 *   mateActions.allow('closeModal', 'sessionsApp.loadSessions');
 *
 * Names are looked up on window when the event fires, so a page may allow a
 * function before the script that defines it has run.
 */
(function () {
    'use strict';

    const allowed = new Set();
    const EVENTS = {
        click: 'data-click',
        change: 'data-change',
        input: 'data-input',
        keyup: 'data-keyup',
        submit: 'data-submit',
    };

    // Built-in actions for one-line handlers that called no named function
    const builtins = {
        stop(event) { event.stopPropagation(); },
    };

    function resolve(name) {
        if (Object.prototype.hasOwnProperty.call(builtins, name)) return builtins[name];
        if (!allowed.has(name)) return null;
        let target = window;
        for (const part of name.split('.')) {
            if (target == null) return null;
            target = target[part];
        }
        return typeof target === 'function' ? target : null;
    }

    function argsFor(el, event) {
        const raw = el.getAttribute('data-args');
        if (!raw) return [];
        let list;
        try {
            list = JSON.parse(raw);
        } catch (e) {
            console.warn('mateActions: data-args is not JSON', el);
            return [];
        }
        if (!Array.isArray(list)) list = [list];
        return list.map((a) => (a === '$event' ? event : a === '$el' ? el : a === '$value' ? el.value : a));
    }

    function dispatch(event, attr) {
        let el = event.target instanceof Element ? event.target.closest(`[${attr}]`) : null;
        while (el) {
            const name = el.getAttribute(attr);
            if (el.hasAttribute('data-prevent')) event.preventDefault();
            const fn = resolve(name);
            if (fn) {
                // A method keeps its object as `this`; a plain function gets the element
                const owner = name.includes('.') ? name.split('.').slice(0, -1)
                    .reduce((o, part) => (o == null ? o : o[part]), window) : el;
                fn.apply(owner, name === 'stop' ? [event] : argsFor(el, event));
            } else {
                console.warn(`mateActions: "${name}" is not an allowed action`);
            }
            if (el.hasAttribute('data-stop')) event.stopPropagation();
            if (event.cancelBubble) break;
            el = el.parentElement && el.parentElement.closest(`[${attr}]`);
        }
    }

    for (const [type, attr] of Object.entries(EVENTS)) {
        document.addEventListener(type, (event) => dispatch(event, attr));
    }

    /** Escape a list of arguments for a data-args="..." attribute in generated HTML. */
    function attr(list) {
        return JSON.stringify(list)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    window.mateActions = {
        allow(...names) { names.forEach((n) => allowed.add(n)); },
        /** Whether an action name would run: allowed and defined. */
        can(name) { return resolve(name) !== null; },
        attr,
    };
})();
