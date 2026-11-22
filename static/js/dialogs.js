/**
 * Dialogs on the m-dialog component (static/css/components.css).
 *
 *   <div id="triggerModal" class="m-dialog-backdrop" hidden>
 *     <div class="m-dialog" role="dialog" aria-modal="true" aria-labelledby="triggerModalTitle">…
 *
 *   mateDialog.open('triggerModal')     shows it and moves focus into it
 *   mateDialog.close('triggerModal')    hides it and gives focus back
 *
 * An open dialog closes on Escape and on a click on its backdrop, and keeps Tab
 * inside it. With dialogs over dialogs, those act on the one on top.
 *
 * showConfirm(message, title, confirmText, cancelText, type) asks in a dialog and
 * resolves to true or false; cancelText null shows one button, for a message to read.
 */
(function () {
    'use strict';

    const FOCUSABLE = 'input:not([type="hidden"]):not([disabled]), select:not([disabled]), '
        + 'textarea:not([disabled]), button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';
    const open_ = [];   // {el, returnTo, onClose}, the one on top last
    let pressedOn = null;
    let ids = 0;

    function resolve(target) {
        return typeof target === 'string' ? document.getElementById(target) : target;
    }

    function focusable(backdrop) {
        return Array.from(backdrop.querySelectorAll(FOCUSABLE))
            .filter((el) => el.offsetParent !== null || el === document.activeElement);
    }

    /** Show a dialog. options.focus: the element to focus; options.onClose(result): called once it closes. */
    function open(target, options = {}) {
        const backdrop = resolve(target);
        if (!backdrop || open_.some((d) => d.el === backdrop)) return;
        open_.push({ el: backdrop, returnTo: document.activeElement, onClose: options.onClose });
        backdrop.hidden = false;
        const first = resolve(options.focus) || backdrop.querySelector('[autofocus]')
            || focusable(backdrop.querySelector('.m-dialog-body') || backdrop)[0]
            || focusable(backdrop)[0];
        if (first) first.focus();
    }

    /** Hide a dialog; result is handed to its onClose. */
    function close(target, result) {
        const backdrop = resolve(target);
        if (!backdrop) return;
        backdrop.hidden = true;
        const i = open_.findIndex((d) => d.el === backdrop);
        if (i < 0) return;
        const [entry] = open_.splice(i, 1);
        if (entry.returnTo && document.contains(entry.returnTo)) entry.returnTo.focus();
        if (entry.onClose) entry.onClose(result);
    }

    function top() {
        return open_.length ? open_[open_.length - 1] : null;
    }

    document.addEventListener('keydown', (event) => {
        const current = top();
        if (!current) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopImmediatePropagation();
            close(current.el, false);
        } else if (event.key === 'Tab') {
            const items = focusable(current.el);
            if (!items.length) return;
            const first = items[0];
            const last = items[items.length - 1];
            if (event.shiftKey && (document.activeElement === first || !current.el.contains(document.activeElement))) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !current.el.contains(document.activeElement))) {
                event.preventDefault();
                first.focus();
            }
        }
    }, true);

    // A click on the backdrop closes the dialog; a drag that starts in a field and ends outside does not
    document.addEventListener('mousedown', (event) => { pressedOn = event.target; }, true);
    document.addEventListener('click', (event) => {
        const current = top();
        if (current && event.target === current.el && pressedOn === current.el) close(current.el, false);
    });

    const CONFIRM_TYPES = {
        danger: { icon: 'fa-exclamation-triangle', tone: 'm-tone-danger', ok: 'm-btn-danger' },
        warning: { icon: 'fa-exclamation-triangle', tone: 'm-tone-warning', ok: 'm-btn-primary' },
        info: { icon: 'fa-info-circle', tone: 'm-tone-accent', ok: 'm-btn-primary' },
    };

    function showConfirm(message, title = 'Confirm', confirmText = 'Confirm', cancelText = 'Cancel', type = 'warning') {
        return new Promise((done) => {
            const kind = CONFIRM_TYPES[type] || { icon: 'fa-question-circle', tone: '', ok: 'm-btn-primary' };
            const id = `mate-confirm-${++ids}`;
            const backdrop = document.createElement('div');
            backdrop.className = 'm-dialog-backdrop m-dialog-top';
            backdrop.id = 'customConfirmModal';
            backdrop.hidden = true;
            backdrop.innerHTML = `
                <div class="m-dialog m-dialog-plain" role="alertdialog" aria-modal="true"
                     aria-labelledby="${id}-title" aria-describedby="${id}-message">
                    <div class="m-dialog-head">
                        <div class="m-dialog-icon ${kind.tone}"><i class="fas ${kind.icon}" aria-hidden="true"></i></div>
                        <h3 class="m-dialog-title" id="${id}-title"></h3>
                    </div>
                    <div class="m-dialog-body" id="${id}-message"></div>
                    <div class="m-dialog-foot">
                        <button type="button" class="m-btn m-btn-secondary" data-answer="no"></button>
                        <button type="button" class="m-btn ${kind.ok}" data-answer="yes"></button>
                    </div>
                </div>`;
            // Callers put names into the message (a token, a template, a rate-limit
            // scope), so all four strings go in as text, not markup
            backdrop.querySelector('.m-dialog-title').textContent = title;
            backdrop.querySelector('.m-dialog-body').textContent = message;
            const cancel = backdrop.querySelector('[data-answer="no"]');
            const ok = backdrop.querySelector('[data-answer="yes"]');
            ok.textContent = confirmText;
            if (cancelText === null) cancel.remove();
            else cancel.textContent = cancelText;
            cancel.addEventListener('click', () => close(backdrop, false));
            ok.addEventListener('click', () => close(backdrop, true));

            document.body.appendChild(backdrop);
            // A destructive question starts on Cancel, so Enter alone does not confirm it
            open(backdrop, {
                focus: type === 'danger' && cancelText !== null ? cancel : ok,
                onClose: (result) => {
                    backdrop.remove();
                    done(result === true);
                },
            });
        });
    }

    window.mateDialog = { open, close };
    window.showConfirm = showConfirm;
})();
