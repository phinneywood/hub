// Keeps the app's own files on this device, so Hub opens at once and can say so when it can't reach home.
// It covers /app/ only, and only three kinds of request: the page, the built files (asked for by version)
// and the typeface. What the house is doing is never kept here; that always comes from Hub.
const KEPT = "hub-app-1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

async function keep(request, answer) {
  const cache = await caches.open(KEPT);
  const path = new URL(request.url).pathname;
  for (const old of await cache.keys()) {
    if (new URL(old.url).pathname === path) await cache.delete(old);      // an older version of the same file
  }
  await cache.put(request, answer);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname === "/app/") {
    // The page: fresh whenever Hub answers, the kept copy when it doesn't.
    event.respondWith(
      fetch(request)
        .then((answer) => {
          if (answer.ok) event.waitUntil(keep(request, answer.clone()));
          return answer;
        })
        .catch(async () => (await caches.match(request, { ignoreSearch: true })) || Response.error()),
    );
  } else if (url.pathname.startsWith("/static/app/")) {
    // A built file or the typeface: the kept copy if this exact version is here, else fetched and kept.
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((answer) => {
            if (answer.ok) event.waitUntil(keep(request, answer.clone()));
            return answer;
          }),
      ),
    );
  }
});
