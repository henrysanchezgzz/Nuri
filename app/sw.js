// NURI: permite abrir la app sin internet (con los últimos datos). Cambia VERSION al actualizar la app.
const VERSION = 'nuri-v11';
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './apple-touch-icon.png',
  './logo-aero.png', './logo-dark.png', './logo-light.png', './favicon.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(VERSION).then(c => c.addAll(FILES))); self.skipWaiting(); });
self.addEventListener('activate', e => {
  // sólo borra cachés viejos de NURI: Namufin vive en el mismo dominio y tiene los suyos
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('nuri-') && k !== VERSION).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin === location.origin) {
    if (/\.(enc|json)$/.test(url.pathname) || /splash-/.test(url.pathname)) return; // datos siempre frescos
    e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(VERSION).then(c => c.put(e.request, copy)); return r; })
      .catch(() => caches.match(e.request).then(r => r || caches.match('./index.html'))));
  } else if (url.hostname.includes('fonts.')) {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(res => { const copy = res.clone(); caches.open(VERSION).then(c => c.put(e.request, copy)); return res; })));
  }
});
