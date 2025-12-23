// static/sw.js

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "رسالة جديدة", body: "لديك رسالة جديدة" };
  }

  const title = data.title || "💬 رسالة جديدة";
  const options = {
    body: data.body || "لديك رسالة جديدة",
    icon: data.icon || "/static/logo.png",
    badge: data.badge || "/static/logo.png",
    data: {
      url: data.url || "/",
    },
    tag: data.tag || "chat-push",
    renotify: true,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const urlToOpen = (event.notification && event.notification.data && event.notification.data.url) ? event.notification.data.url : "/";

  event.waitUntil(
    (async () => {
      const allClients = await clients.matchAll({ type: "window", includeUncontrolled: true });

      // لو فيه نافذة مفتوحة على نفس الموقع: ركّز عليها
      for (const client of allClients) {
        try {
          if ("focus" in client) {
            await client.focus();
            // افتح الرابط داخل نفس النافذة
            client.navigate(urlToOpen);
            return;
          }
        } catch (e) {}
      }

      // لو مفيش: افتح نافذة جديدة
      if (clients.openWindow) {
        return clients.openWindow(urlToOpen);
      }
    })()
  );
});
