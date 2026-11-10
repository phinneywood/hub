// Kitchen notifications. Payloads are short fixed notices; chat text stays on the Mac.
self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  event.waitUntil(self.registration.showNotification(data.title || "Kitchen", {
    body: data.body || "Something new in Kitchen", data: { url: data.url || "/kitchen" }, tag: "kitchen"
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/kitchen";
  event.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if (new URL(list[i].url).pathname === url && "focus" in list[i]) return list[i].focus();
    }
    return clients.openWindow(url);
  }));
});
