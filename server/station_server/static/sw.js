// Station Server's service worker. Its only job: when Station Server isn't
// running (say its window was opened from the Dock or taskbar after a
// Quit), show a page that starts it and waits, instead of "This site
// can't be reached".
const CACHE = "station-server-offline-1";
const OFFLINE = ["/offline.html", "/offline.js", "/style.css", "/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(OFFLINE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Everything always comes from Station Server while it's running; the
// saved copies are only used when it can't be reached.
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(async () =>
      (await caches.match("/offline.html")) || Response.error()));
  } else if (OFFLINE.includes(new URL(req.url).pathname)) {
    e.respondWith(fetch(req).catch(async () => (await caches.match(req)) || Response.error()));
  }
});
