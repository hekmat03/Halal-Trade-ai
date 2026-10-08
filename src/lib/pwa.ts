PATH: src/lib/pwa.ts

const SW_URL = "/sw.js";

export async function registerServiceWorker(): Promise<
  ServiceWorkerRegistration | undefined
> {
  if (
    typeof window === "undefined" ||
    !("serviceWorker" in navigator)
  ) {
    return undefined;
  }

  if (
    window.location.protocol !== "https:" &&
    window.location.hostname !== "localhost"
  ) {
    return undefined;
  }

  try {
    return await navigator.serviceWorker.register(SW_URL);
  } catch (error) {
    console.error("Failed to register service worker:", error);
    return undefined;
  }
}

export async function unregisterServiceWorkers(): Promise<void> {
  if (
    typeof window === "undefined" ||
    !("serviceWorker" in navigator)
  ) {
    return;
  }

  const registrations =
    await navigator.serviceWorker.getRegistrations();

  await Promise.all(
    registrations.map((registration) =>
      registration.unregister(),
    ),
  );
}
