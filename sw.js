/* tuffgit21 APT Repo — Service Worker for PWA install on Android */
const CACHE_VERSION = 'tuffgit21-apt-v2';
const CORE_CACHE = CACHE_VERSION + '-core';
const RUNTIME_CACHE = CACHE_VERSION + '-runtime';

// Core shell — cached on install for offline and for installability
const CORE_ASSETS = [
  './',
  './index.html',
  './styles.css',
  './manifest.json',
  './favicon.svg',
  './favicon.ico',
  './favicon-16x16.png',
  './favicon-32x32.png',
  './favicon-48x48.png',
  './favicon-192x192.png',
  './favicon-512.png',
  './apple-touch-icon.png',
  './public.key',
  './tuffgit21.gpg'
];

// Extra browsable shells — pre-cache for faster nav (optional, not required for install)
const SHELL_PAGES = [
  './dists/',
  './dists/index.html',
  './pool/',
  './pool/index.html'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CORE_CACHE).then((cache) => {
      // addAll with no-cache to get fresh copy on install
      return cache.addAll([...CORE_ASSETS, ...SHELL_PAGES].map(u => new Request(u, {cache: 'reload'})));
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter(k => !k.startsWith(CACHE_VERSION)).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Network-first for Packages/Release/InRelease (always fresh), cache-first for shell
function isDynamic(url) {
  const p = url.pathname;
  return p.includes('/dists/') && (p.endsWith('/Packages') || p.endsWith('/Packages.gz') || p.endsWith('/Release') || p.endsWith('/InRelease') || p.endsWith('/Release.gpg'));
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  // Only handle same-origin (repo) requests; let CDN/external pass through
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // For pages under repo scope, handle
  if (!url.pathname.startsWith(new URL(self.registration.scope).pathname)) {
    // if manifest scope is ./ and sw is at root, this is same as origin+base; still handle relative
  }

  if (isDynamic(url)) {
    // Network first, fallback to cache
    event.respondWith(
      fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then(c => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req).then(r => r || caches.match('./index.html')))
    );
    return;
  }

  // For navigation requests (html), network first fallback to cache -> offline index
  if (req.mode === 'navigate' || req.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then(c => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req).then(r => r || caches.match('./index.html').then(r2 => r2 || caches.match('./'))))
    );
    return;
  }

  // For static assets (css, js, images, fonts) — cache first, update in background
  event.respondWith(
    caches.match(req).then((cached) => {
      const fetched = fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then(c => c.put(req, copy));
        }
        return res;
      }).catch(() => null);
      return cached || fetched;
    })
  );
});

// Allow page to trigger update
self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') self.skipWaiting();
});
