import { Download, Share } from "lucide-react";
import { useSyncExternalStore } from "react";

// Chrome/Edge "yüklenebilir" dediğinde beforeinstallprompt gelir; erken yakalayıp saklıyoruz (main.tsx'ten önce import)
type Prompt = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };
let deferred: Prompt | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e as Prompt; emit(); });
  window.addEventListener("appinstalled", () => { deferred = null; emit(); });
}
const subscribe = (l: () => void) => { listeners.add(l); return () => listeners.delete(l); };

const standalone = () => window.matchMedia?.("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

/** "Uygulamayı yükle": Chrome/Edge'de tek dokunuş; iPhone'da Ana Ekrana Ekle yönlendirmesi; yüklüyse hiç görünmez. */
export default function InstallApp({ compact = false }: { compact?: boolean }) {
  const prompt = useSyncExternalStore(subscribe, () => deferred, () => null);
  if (standalone()) return compact ? null : <p className="text-sm text-green-700 dark:text-green-400">✓ Uygulama olarak açık.</p>;
  if (prompt) {
    return (
      <button className="btn-primary" onClick={async () => { await prompt.prompt(); await prompt.userChoice; deferred = null; emit(); }}>
        <Download size={16} /> Uygulamayı yükle
      </button>
    );
  }
  if (isIOS()) {
    return (
      <p className="text-sm text-stone-600 dark:text-stone-400">
        iPhone'da: Safari'de alttaki <Share size={14} className="inline" /> <b>Paylaş</b> → <b>Ana Ekrana Ekle</b>.
      </p>
    );
  }
  return compact ? null : (
    <p className="text-sm text-stone-600 dark:text-stone-400">
      Tarayıcı menüsünden yükle: Android'de <b>⋮ → Ana ekrana ekle / Uygulamayı yükle</b>; bilgisayarda
      <b> ⋮ → Yayınla, kaydet ve paylaş → Sayfayı uygulama olarak yükle</b> (ya da adres çubuğundaki yükle simgesi).
    </p>
  );
}
