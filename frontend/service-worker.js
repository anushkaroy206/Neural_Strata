/*
 * Neural Strata — service worker (Gap G2)
 * =======================================
 *
 * Three jobs, and nothing else:
 *
 *  1. OFFLINE SHELL. Cache the dashboard shell so the console still opens when
 *     the venue Wi-Fi is dead. This is the browser half of the offline story —
 *     the data half already lives in local PostgreSQL as the source of truth.
 *
 *  2. NEAR-LIVE TELEMETRY when the network is flaky. `/api/telemetry`,
 *     `/api/ai` and friends are network-first with a short timeout, falling
 *     back to the last good response. A frozen console is worse than a stale
 *     one, so the payload is returned with an `x-ns-cache: stale` marker and
 *     the UI shows a "STALE DATA" chip. Staleness is never hidden.
 *
 *  3. WEB PUSH. Real notifications in the OS tray, including when the tab is
 *     closed. We are careful here: the browser is NEVER the only alert path
 *     (siren and junction LEDs are local, Telegram/SMS are server-side). If
 *     push is unavailable, nothing about the safety chain changes.
 *
 * Bump CACHE_VERSION on every deploy. Never cache `/api/*` responses in the
 * shell cache — mixing a stale JSON into the app shell is how you get a
 * console that shows yesterday's NOMINAL.
 */

const CACHE_VERSION = 'ns-v1.0.0';
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const DATA_CACHE = `${CACHE_VERSION}-data`;

const SHELL_ASSETS = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/pwa/icons/icon-192.png',
  '/pwa/icons/icon-512.png',
  '/pwa/icons/maskable-512.png',
  '/pwa/icons/apple-touch-icon.png'
];

/* Endpoints worth serving from cache while the hub link is down. */
const DATA_ROUTES = ['/api/telemetry', '/api/ai', '/api/extended', '/api/history', '/api/map', '/api/whoami'];
const DATA_TIMEOUT_MS = 3000;

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // addAll() rejects the whole install if ONE asset 404s (e.g. index.html not
    // written yet), which would leave the app with no service worker at all.
    // Cache them one by one instead, and report what was missed.
    const missed = [];
    await Promise.all(SHELL_ASSETS.map(async (url) => {
      try {
        await cache.add(new Request(url, { cache: 'reload' }));
      } catch (err) {
        missed.push(url);
      }
    }));
    if (missed.length) console.warn('[sw] not cached (will retry at runtime):', missed.join(', '));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys
      .filter((k) => k.startsWith('ns-') && !k.startsWith(CACHE_VERSION))
      .map((k) => caches.delete(k)));
    if (self.registration.navigationPreload) {
      try { await self.registration.navigationPreload.disable(); } catch (e) { /* optional */ }
    }
    await self.clients.claim();
  })());
});

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('timeout')), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); },
                 (e) => { clearTimeout(t); reject(e); });
  });
}

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const fresh = await withTimeout(fetch(request), DATA_TIMEOUT_MS);
    if (fresh && fresh.ok) {
      const copy = fresh.clone();
      // Mark nothing here; the marker is added on the STALE path below.
      cache.put(request, copy).catch(() => {});
      return fresh;
    }
    throw new Error(`http ${fresh && fresh.status}`);
  } catch (err) {
    const hit = await cache.match(request);
    if (!hit) {
      return new Response(JSON.stringify({
        error: 'offline_no_cache',
        detail: 'The edge server is unreachable and this endpoint has no cached copy yet.',
        offline: true
      }), { status: 503, headers: { 'Content-Type': 'application/json', 'x-ns-cache': 'miss' } });
    }
    const body = await hit.text();
    return new Response(body, {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        // The UI reads this and shows the STALE chip. Never silent.
        'x-ns-cache': 'stale',
        'x-ns-cached-at': hit.headers.get('date') || 'unknown'
      }
    });
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;                  // writes always go to the server

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;        // never touch cross-origin

  if (DATA_ROUTES.some((r) => url.pathname === r || url.pathname.startsWith(r + '/'))) {
    event.respondWith(networkFirst(request, DATA_CACHE));
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        return await withTimeout(fetch(request), DATA_TIMEOUT_MS);
      } catch (err) {
        const cache = await caches.open(SHELL_CACHE);
        return (await cache.match('/index.html')) || (await cache.match('/')) ||
          new Response('<h1>Neural Strata — offline</h1><p>The console shell is not cached yet.</p>',
            { status: 200, headers: { 'Content-Type': 'text/html' } });
      }
    })());
    return;
  }

  event.respondWith((async () => {
    const cache = await caches.open(SHELL_CACHE);
    const hit = await cache.match(request);
    if (hit) {
      // Stale-while-revalidate for static assets.
      fetch(request).then((fresh) => { if (fresh && fresh.ok) cache.put(request, fresh.clone()); }).catch(() => {});
      return hit;
    }
    try {
      const fresh = await fetch(request);
      if (fresh && fresh.ok && url.origin === self.location.origin) cache.put(request, fresh.clone()).catch(() => {});
      return fresh;
    } catch (err) {
      return new Response('', { status: 504, statusText: 'offline' });
    }
  })());
});

/* --------------------------------------------------------------------- *
 * Web Push
 * --------------------------------------------------------------------- */

self.addEventListener('push', (event) => {
  let payload = {
    title: 'Neural Strata',
    body: 'Ground movement alert',
    severity: 'WARNING',
    url: '/'
  };
  try {
    if (event.data) payload = Object.assign(payload, event.data.json());
  } catch (err) {
    if (event.data) payload.body = event.data.text();
  }

  const sev = String(payload.severity || 'WARNING').toUpperCase();
  const urgent = ['HIGH', 'CRITICAL', 'IMMINENT'].includes(sev);

  event.waitUntil(self.registration.showNotification(
    `${sev} · ${payload.title || 'Neural Strata'}`,
    {
      body: payload.body,
      icon: '/pwa/icons/icon-192.png',
      badge: '/pwa/icons/icon-192.png',
      tag: payload.tag || `ns-${sev}`,      // same tag collapses repeats
      renotify: urgent,
      requireInteraction: urgent,           // critical alerts stay until touched
      vibrate: urgent ? [200, 100, 200, 100, 400] : [100],
      data: { url: payload.url || '/', severity: sev, alert_id: payload.alert_id || null },
      actions: payload.actions || [
        { action: 'open', title: 'Open console' },
        { action: 'ack', title: 'Acknowledge' }
      ]
    }
  ));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const target = event.action === 'ack'
    ? `/api/alerts/${data.alert_id || ''}/ack`
    : (data.url || '/');

  event.waitUntil((async () => {
    if (event.action === 'ack' && data.alert_id) {
      try {
        await fetch(target, { method: 'POST' });
      } catch (err) {
        // Offline acknowledgement: the alert stays ACTIVE and the operator
        // retries. We never fake an acknowledgement that did not reach the hub.
      }
    }
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if ('focus' in c) { c.postMessage({ type: 'ns-notification-open', data }); return c.focus(); }
    }
    if (self.clients.openWindow) return self.clients.openWindow(data.url || '/');
    return undefined;
  })());
});

/* --------------------------------------------------------------------- *
 * Messages from the page
 * --------------------------------------------------------------------- */

self.addEventListener('message', (event) => {
  const msg = event.data || {};
  if (msg.type === 'ns-skip-waiting') self.skipWaiting();
  if (msg.type === 'ns-clear-caches') {
    event.waitUntil(caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k.startsWith('ns-')).map((k) => caches.delete(k)))));
  }
  if (msg.type === 'ns-version' && event.source) {
    event.source.postMessage({ type: 'ns-version', version: CACHE_VERSION });
  }
});
