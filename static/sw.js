const CACHE = 'wy6y-weather-v3';
// Derive the app's base path from the scope set during registration.
// scope '/WX/' → base '/WX';  scope '/' → base ''
const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, '');
const SHELL = [
    BASE + '/',
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

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;

  // Never intercept the solar dashboard — it has its own PWA + service worker.
  if (url.pathname === '/solar' || url.pathname.startsWith('/solar/')) {
    return;
  }

  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request)
        .then((res) => res)
        .catch(() => caches.match(event.request))
    );
    return;
  }

  if (url.origin === location.origin) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        const network = fetch(event.request).then((res) => {
          if (res && res.status === 200 && (url.pathname.startsWith('/static/') || url.pathname === '/' || url.pathname === '/manifest.json')) {
            const copy = res.clone();
            caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          }
          return res;
        });
        return cached || network;
      })
    );
  }
});