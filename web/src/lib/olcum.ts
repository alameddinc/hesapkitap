// Ölçüm (sunucu app/funnel.py, yetenek "funnel_events"): POST /api/events {name}. Sunucu yalnız günlük toplam tutar;
// kişi, IP, cihaz kimliği gönderilmez. Eski sunucuda (yetenek yok) hiç gönderilmez; hata sessizce yutulur.

export type OlcumOlayi = "paywall_view" | "plans_open" | "purchase_start" | "purchase_success" | "purchase_cancel" | "signup_demo";

const DEMO_KEY = "hk-demo-signup"; // kayıt demodan gelen bağlantıyla yapıldı; oturum açılınca bir kez bildirilir

export function olcumGonder(name: OlcumOlayi, capabilities: string[] | undefined): void {
  if (!capabilities?.includes("funnel_events")) return;
  void fetch("/api/events", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) })
    .catch(() => undefined);
}

/** Kayıt sayfası demodan gelen bağlantıyla açıldı mı (#/kayit?k=demo ya da ?k=demo). */
export function demodanGeldi(loc: { hash: string; search: string } = window.location): boolean {
  const q = new URLSearchParams(loc.hash.split("?")[1] ?? "");
  return q.get("k") === "demo" || new URLSearchParams(loc.search).get("k") === "demo";
}

/** Kayıt başarılı: demodan geldiyse işaretle (e-posta doğrulaması bitene kadar oturum yok; bildirim oturumla gider). */
export function demoKaydiniIsaretle(): void {
  try { if (demodanGeldi()) localStorage.setItem(DEMO_KEY, "1"); } catch { /* özel mod */ }
}

/** Oturum açık ve sunucu destekliyorsa bekleyen "demodan gelen hesap" olayını bir kez gönder. */
export function demoKaydiniGonder(capabilities: string[] | undefined): void {
  try {
    if (localStorage.getItem(DEMO_KEY) !== "1" || !capabilities?.includes("funnel_events")) return;
    localStorage.removeItem(DEMO_KEY);
  } catch { return; }
  olcumGonder("signup_demo", capabilities);
}
