const CACHE_NAME = 'paceforge-v2';
const STATIC_ASSETS = [
  '/static/js/app.js',
  '/static/css/app.css',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // only this origin's static files go through the cache; tiles, CDN scripts, pages and API calls go straight to the network
  const u = new URL(event.request.url);
  if (u.origin !== self.location.origin || !u.pathname.startsWith('/static/')) return;
  // Network-first for HTML and API, cache-first for static assets
  if (event.request.mode === 'navigate' || event.request.url.includes('/api/') || event.request.url.includes('/partials/')) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
  } else {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
});
