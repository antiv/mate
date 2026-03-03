/* MATE Dashboard Service Worker - Offline shell */

const CACHE_NAME = 'mate-dashboard-v1';
const STATIC_ASSETS = [
  '/static/css/dashboard-responsive.css',
  '/static/css/agents.css',
  '/static/css/widget/chat.css',
  '/static/js/server-control.js',
  '/static/js/dashboard-nav.js',
  '/static/logo.png',
  '/static/favicon.ico',
  '/static/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch(() => {
        // Ignore failures for optional assets
      });
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/dashboard/api/') || url.pathname.startsWith('/auth/')) {
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) =>
        cached || fetch(event.request).then((res) => {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return res;
        })
      )
    );
    return;
  }
});
