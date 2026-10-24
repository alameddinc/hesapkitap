import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./components/InstallApp"; // beforeinstallprompt erken yakalansın
import App from "./App";
import "./index.css";

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
