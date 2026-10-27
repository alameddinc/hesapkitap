import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./components/InstallApp"; // beforeinstallprompt erken yakalansın
import App from "./App";
import "./index.css";

// Mağaza uygulaması (Capacitor): <html class="in-app">. Android'de kenardan kenara çizilir; güvenli alan değerleri
// hiç gelmezse (eski WebView, eklenti değişkenleri yok) saat ve hareket çubuğu için makul bir boşluk bırak.
if (/HesapKitapApp\//.test(navigator.userAgent)) {
  const root = document.documentElement;
  root.classList.add("in-app");
  if (/Android/.test(navigator.userAgent)) {
    requestAnimationFrame(() => {
      const probe = document.createElement("div");
      probe.style.cssText = "position:fixed;visibility:hidden;padding-top:var(--sat);padding-bottom:var(--sab)";
      document.body.appendChild(probe);
      const cs = getComputedStyle(probe);
      if (parseFloat(cs.paddingTop) === 0) root.style.setProperty("--sat", "28px");
      if (parseFloat(cs.paddingBottom) === 0) root.style.setProperty("--sab", "20px");
      probe.remove();
    });
  }
}

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: false, retry: 1 } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);

// Yeni sürüm kontrolü: iOS'ta ana ekrandaki uygulama arka planda açık kalır, sayfa yenilenmez. Öne gelince
// sunucudaki index.html'in derleme dosyası değiştiyse kendini yenile (formda yazılmamış bir şey yoksa).
if (import.meta.env.PROD) {
  const current = () => document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]')?.getAttribute("src");
  let last = 0;
  const check = async () => {
    if (document.visibilityState !== "visible" || Date.now() - last < 60_000) return;
    last = Date.now();
    try {
      const html = await (await fetch("/", { cache: "no-store" })).text();
      const latest = html.match(/<script[^>]+type="module"[^>]+src="([^"]+)"/)?.[1];
      const typing = document.activeElement instanceof HTMLInputElement || document.activeElement instanceof HTMLTextAreaElement;
      if (latest && current() && latest !== current() && !typing && !document.querySelector("[role=dialog]")) window.location.reload();
    } catch { /* çevrimdışı */ }
  };
  document.addEventListener("visibilitychange", check);
  window.addEventListener("focus", check);
}

// PWA: sadece production'da (dev'de önbellek kafa karıştırır)
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
