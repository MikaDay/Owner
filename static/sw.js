// Service worker Cirforvest: notificările push și pornirea rapidă.
// Serverul gratuit „doarme” după 15 minute; prima deschidere poate dura un minut. Dacă pagina nu
// vine în 4 secunde și nici /ping nu răspunde, arătăm imediat „Serverul pornește…” (din cache),
// care reîncarcă singur pagina când serverul e treaz. Paginile cu date nu se păstrează în cache.
const CACHE = "cirforvest-v1";
const OFFLINE = "/static/offline.html";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll([OFFLINE, "/static/icon-192.png"])));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

const later = (ms) => new Promise((r) => setTimeout(() => r(null), ms));

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.mode !== "navigate" || req.method !== "GET") return;
  e.respondWith((async () => {
    const page = fetch(req);
    const first = await Promise.race([page.catch(() => null), later(4000)]);
    if (first) return first;
    // Încet: e serverul care pornește, sau doar o pagină mai lungă? Întrebăm /ping.
    const alive = await Promise.race([fetch("/ping", { cache: "no-store" }).then((r) => r.ok).catch(() => false), later(1500)]);
    if (alive) return page;
    return (await caches.match(OFFLINE)) || page;
  })());
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || "Cirforvest", {
    body: d.body || "", tag: d.tag, data: { url: d.url || "/" },
    icon: "/static/icon-192.png", badge: "/static/icon-192.png",
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if ("focus" in w) { try { await w.navigate(url); } catch (err) {} return w.focus(); }
    }
    return self.clients.openWindow(url);
  })());
});
