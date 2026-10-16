// PaceForge service worker. Pages and API calls are network-only, and the browser's own HTTP
// cache keeps the versioned assets (?v=<build id>, served immutable), so nothing is precached
// here: earlier builds filled a cache with asset names the pages never request. This worker
// only retires those caches.
self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k.startsWith('paceforge-')).map((k) => caches.delete(k)))
    )
  );
});
