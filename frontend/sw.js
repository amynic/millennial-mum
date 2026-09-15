/* Millennial Mum service worker — offline app shell for the installed PWA.
 *
 * Strategy:
 *  - Precache the static shell (HTML/CSS/JS/icons/manifest) on install.
 *  - Refresh navigation + static assets from the network when available, with
 *    cached fallbacks for offline use. This prevents an installed PWA from
 *    running an old app.js after a deployment.
 *  - NEVER cache /api/* — chat requests must always hit the network so the
 *    parent gets a live answer (and no stale replies).
 */

const CACHE = 'mm-shell-v3';
const SHELL = [
  '/',
  '/index.html',
  '/styles.css',
  '/app.js',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never cache API calls — always go to the network.
  if (url.pathname.startsWith('/api/')) return;
  if (request.method !== 'GET') return;

  // App shell: network-first so deployed fixes reach installed PWAs immediately.
  event.respondWith(
    fetch(request)
      .then((resp) => {
        if (resp.ok && url.origin === self.location.origin) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(request, copy));
        }
        return resp;
      })
      .catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        return request.mode === 'navigate' ? caches.match('/index.html') : undefined;
      })
  );
});
