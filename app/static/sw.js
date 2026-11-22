// PaceForge service worker, served at /sw.js for the whole site. Pages and API calls are network-only, and the
// browser's own HTTP cache keeps the versioned assets (?v=<build id>, served immutable): nothing is cached here,
// and the caches earlier builds filled are retired.
//
// Its one job on a request: wait out a deployment. Coolify stops the app's containers before it starts the new
// ones (docs/ops-runbook.md, « Déployer »): for some seconds the proxy answers in the app's place (502, 503). A
// page asked for meanwhile is asked for again until the app answers, at most BUDGET: the browser shows a page
// that loads, then the page. Only a page (a navigation) and only a GET: nothing is ever sent twice.
var BUDGET = 60000, FIRST_WAIT = 1000, LONGEST_WAIT = 3000;

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

// The proxy's answer, not the app's: every answer of the app carries its Content-Security-Policy
// (app/middleware/security.py), its own 404 and 503 included, and those are final.
function fromTheProxy(response) {
  return [404, 502, 503, 504].indexOf(response.status) >= 0 && !response.headers.has('content-security-policy');
}

async function patient(request) {
  var until = Date.now() + BUDGET, wait = FIRST_WAIT;
  for (;;) {
    var response = await fetch(request);  // no network at all: the browser's own error, at once
    if (!fromTheProxy(response) || Date.now() + wait > until) return response;
    await new Promise((resolve) => setTimeout(resolve, wait));
    wait = Math.min(wait * 1.5, LONGEST_WAIT);
  }
}

self.addEventListener('fetch', (event) => {
  if (event.request.mode === 'navigate' && event.request.method === 'GET') event.respondWith(patient(event.request));
});
