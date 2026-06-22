const CACHE = 'wy6y-weather-v1';
const SHELL = ['/', '/manifest.json', '/static/icon-192.png', '/static/icon-512.png', '/static/icon-maskable-512.png', '/static/apple-touch-icon.png', '/static/favicon.ico'];

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

  // Live weather data: network first, short offline tolerance
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request)
        .then((res) => res)
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // App shell: stale-while-revalidate for same-origin navigations and static assets
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