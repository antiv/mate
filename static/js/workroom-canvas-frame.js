/*
 * The Work Room canvas page (/dashboard/workroom/canvas). It runs code an agent
 * wrote, so its policy sandboxes it: the page has an opaque origin and cannot
 * reach the dashboard (see canvas_policy in server/csp.py).
 *
 * The Work Room, which framed this page or opened it in a tab, sends the
 * document to show once the page says it is ready. Only that window, on MATE's
 * own origin, is listened to.
 */
(function () {
    'use strict';

    // location.origin is "null" in a sandbox; the URL still names MATE's origin
    var mateOrigin = new URL(window.location.href).origin;
    var opener = window.parent !== window ? window.parent : window.opener;
    if (!opener) return;

    window.addEventListener('message', function onMessage(event) {
        if (event.source !== opener || event.origin !== mateOrigin) return;
        if (!event.data || event.data.type !== 'mate-canvas-doc') return;
        window.removeEventListener('message', onMessage);
        document.open();
        document.write(String(event.data.html));
        document.close();
    });
    opener.postMessage({ type: 'mate-canvas-ready' }, mateOrigin);
})();
