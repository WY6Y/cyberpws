const CACHE = 'wy6y-weather-v5';
// Derive the app's base path from the scope set during registration.
// scope '/WX/' → base '/WX';  scope '/' → base ''
const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, '');
const SHELL = [
    BASE + '/manifest.json',
    '/static/icon-192.png',
    '/static/icon-512.png',
    '/static/icon-maskable-512.png',
    '/static/apple-touch-icon.png',
    '/static/favicon.ico',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

function isHtmlPath(pathname) {
  // Never cache-first the live dashboard HTML — hard refresh alone can't fix stale SW shells.
  if (pathname === '/' || pathname === BASE + '/' || pathname === BASE) return true;
  if (pathname === '/records' || pathname === BASE + '/records') return true;
  if (pathname === '/almanac' || pathname === BASE + '/almanac') return true;
  if (pathname.endsWith('.html')) return true;
  return false;
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;

  // Never intercept the solar dashboard — it has its own PWA + service worker.
  if (url.pathname === '/solar' || url.pathname.startsWith('/solar/')) {
    return;
  }

  // Live API — network only
  if (url.pathname.startsWith('/api/') || (BASE && url.pathname.startsWith(BASE + '/api/'))) {
    event.respondWith(
      fetch(event.request)
        .then((res) => res)
        .catch(() => caches.match(event.request))
    );
    return;
  }

  if (url.origin !== location.origin) return;

  // HTML pages: network-first so deploys show up immediately
  if (event.request.mode === 'navigate' || isHtmlPath(url.pathname)) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          }
          return res;
        })
        .catch(() => caches.match(event.request).then((c) => c || caches.match(BASE + '/')))
    );
    return;
  }

  // Static assets: cache-first with background refresh
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request).then((res) => {
        if (res && res.status === 200 && url.pathname.startsWith('/static/')) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return res;
      });
      return cached || network;
    })
  );
});
