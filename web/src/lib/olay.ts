// Sorun kaydı (sunucu app/incidents.py, yetenek "incidents"; mobil src/lib/olay.ts'in eşi): istemcinin gördüğü hata sunucuya
// KİŞİSEL VERİ OLMADAN gider — kod (sabit sözlük), ekran yolu (kimlikler sunucuda atılır), platform "web" (web sunucuyla aynı sürüm: uygulama sürümü yok) ve kısa teknik
// özet (sunucuda süzülür: rakam, tırnak içi metin, adres atılır). Tutar, isim, açıklama, dosya GİTMEZ.
// Toplu gönderim (3 sn), aynı olay 60 sn içinde bir kez, oturumda en çok 50 olay; çevrimdışıyken ağ hataları kaydedilmez.
// Sunucu yeteneği bilinmiyorsa (giriş ekranı) /api/auth/status'tan bir kez öğrenilir; eski sunucuya hiçbir şey gitmez.
// Gönderimin kendi hatası sessizce yutulur (döngü yok: lib/apiError.ts bu ucun hatasını kaydetmez; doğrudan fetch).
import { agOlayiDinle } from "./apiError.ts";

export type OlayKodu = "js_hatasi" | "ekran_coktu" | "ag_zaman_asimi" | "sunucuya_ulasilamadi" | "eklenti_hatasi";
export const OLAY_UCU = "/api/incidents/client";

interface Olay { code: OlayKodu; screen: string; detail?: string; plugin?: string }

let destek: boolean | undefined; // undefined: bilinmiyor
let soruldu = false;
let kuyruk: Olay[] = [];
let zamanlayici: ReturnType<typeof setTimeout> | null = null;
let toplam = 0;
const son = new Map<string, number>();
const OTURUM_EN_COK = 50;
const PAKET = 10;

/** Sunucu yeteneği (App: /api/meta capabilities). */
export const olayDestek = (v: boolean | undefined) => { destek = v; if (v && kuyruk.length) planla(); if (v === false) kuyruk = []; };

const ekranYolu = () => (typeof location !== "undefined" ? location.hash : "") || "#/";

/** Olayı kaydet (ateşle-unut). */
export function olayKaydet(code: OlayKodu, o: { detail?: string; plugin?: string; screen?: string } = {}): void {
  try {
    if (destek === false || toplam >= OTURUM_EN_COK) return;
    const screen = o.screen ?? ekranYolu();
    const detail = o.detail?.slice(0, 300);
    const key = `${code}|${screen}|${detail ?? ""}|${o.plugin ?? ""}`;
    const now = Date.now();
    if ((son.get(key) ?? 0) > now - 60_000) return;
    son.set(key, now);
    toplam++;
    kuyruk.push({ code, screen, ...(detail ? { detail } : {}), ...(o.plugin ? { plugin: o.plugin } : {}) });
    if (kuyruk.length > 30) kuyruk = kuyruk.slice(-30);
    planla();
  } catch { /* kayıt hiçbir akışı bozmaz */ }
}

function planla() {
  if (zamanlayici) return;
  zamanlayici = setTimeout(() => { zamanlayici = null; void gonder(); }, 3000);
}

/** Bekleyenleri gönder (testte doğrudan çağrılır). */
export async function gonder(): Promise<void> {
  if (!kuyruk.length) return;
  if (typeof navigator !== "undefined" && navigator.onLine === false) { planla(); return; }
  if (destek === undefined) {
    if (soruldu) return;
    soruldu = true;
    try {
      const st = await (await fetch("/api/auth/status")).json() as { capabilities?: string[] };
      destek = Array.isArray(st.capabilities) && st.capabilities.includes("incidents");
    } catch { soruldu = false; return; }
    if (!destek) { kuyruk = []; return; }
  }
  if (!destek) return;
  const paket = kuyruk.splice(0, PAKET);
  try {
    await fetch(OLAY_UCU, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: paket.map((x) => ({ ...x, platform: "web" })) }) });
  } catch { /* sessiz */ }
  if (kuyruk.length) planla();
}

/** Testler için: durumu sıfırla. */
export function olaySifirla(): void {
  destek = undefined; soruldu = false; kuyruk = []; toplam = 0; son.clear();
  if (zamanlayici) clearTimeout(zamanlayici);
  zamanlayici = null;
}
export const olayKuyrugu = () => kuyruk.slice();

// ---------------------------------------------------------------- yakalanmamış hatalar

const GURULTU = /ResizeObserver loop|Script error\.?$|^AbortError|The operation was aborted|Load failed$|cancelled|canceled/i;

function hataOzeti(e: unknown): string | null {
  if (!e) return null;
  if (typeof e === "string") return e;
  const x = e as { name?: string; message?: string; status?: number };
  if (typeof x.status === "number") return null; // ApiError: sunucu ya da apiError.ts zaten kaydetti, ekranda gösterildi
  return `${x.name ?? "Error"}: ${x.message ?? ""}`;
}

let dinliyor = false;
/** main.tsx'te bir kez: window.onerror ve yakalanmamış Promise reddi → js_hatasi; apiError.ts ağ olayları. */
export function olayDinle(): void {
  if (dinliyor || typeof window === "undefined") return;
  dinliyor = true;
  agOlayiDinle((tur, detail) => olayKaydet(tur, { detail }));
  window.addEventListener("error", (ev) => {
    const d = hataOzeti(ev.error ?? ev.message);
    if (d && !GURULTU.test(d)) olayKaydet("js_hatasi", { detail: d });
  });
  window.addEventListener("unhandledrejection", (ev) => {
    const d = hataOzeti(ev.reason);
    if (d && !GURULTU.test(d)) olayKaydet("js_hatasi", { detail: d });
  });
}
