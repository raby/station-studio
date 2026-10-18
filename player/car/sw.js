// The car page's service worker: keeps a copy of the page so the home-
// screen icon opens even where the signal is weak. The page itself is
// always fetched fresh when it can be; radio streams are left alone.
const CACHE = "station-player-car-1";
const FILES = ["./", "car.js", "car.css", "manifest.webmanifest", "icon-192.png", "icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  const key = req.mode === "navigate" ? "./" : req;
  e.respondWith(fetch(req)
    .then((resp) => {
      if (resp.ok) {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(key, copy));
      }
      return resp;
    })
    .catch(async () => (await caches.match(key)) || Response.error()));
});
