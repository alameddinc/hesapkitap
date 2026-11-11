// Yönetici duyurusu (sunucu app/announce.py, yetenek "push_announce"): hedef özeti, onay metni, geçmiş satırı.
// Saf yardımcılar (bileşen components/Announce.tsx; mobil aynı metinleri kullanır).

export type DuyuruPlatform = "all" | "ios" | "android";
export interface DuyuruHedef { users: number; devices: number; by_platform: { ios: number; android: number }; opted_out: number; enabled: boolean }
export interface Duyuru {
  id: number; created_at: string; finished_at: string | null; admin_name: string | null; title: string; body: string;
  route: string | null; platform: DuyuruPlatform; below_version: string | null; test_only: boolean;
  target_users: number; target_devices: number; sent: number; failed: number; queued: number; status: "sending" | "queued" | "done";
}

export const BASLIK_MAX = 80;
export const METIN_MAX = 300;
export const SURUM_RE = /^\d+(\.\d+){0,3}$/;
export const ROTA_RE = /^#\/[\w\-/?=&%.~]*$/;

const n = (x: number) => x.toLocaleString("tr-TR");

/** "3 kişi · 4 cihaz (iOS 1, Android 3)" */
export function hedefOzeti(h: DuyuruHedef): string {
  const p = [h.by_platform.ios ? `iOS ${n(h.by_platform.ios)}` : "", h.by_platform.android ? `Android ${n(h.by_platform.android)}` : ""].filter(Boolean);
  return `${n(h.users)} kişi · ${n(h.devices)} cihaz${p.length ? ` (${p.join(", ")})` : ""}`;
}

/** Göndermeden önce sorulan onay (ask): "N kişiye, M cihaza gidecek. Gönderilsin mi?" */
export function onayMetni(h: DuyuruHedef, test: boolean): string {
  if (test) return `Duyuru yalnız senin ${n(h.devices)} cihazına gidecek. Gönderilsin mi?`;
  return `${n(h.users)} kişiye, ${n(h.devices)} cihaza gidecek. Gönderilsin mi?`;
}

/** Form denetimi: hata metni ya da null. */
export function formHatasi(f: { title: string; body: string; route: string; below_version: string }): string | null {
  if (!f.title.trim()) return "Başlık boş olamaz";
  if (!f.body.trim()) return "Metin boş olamaz";
  if (f.title.trim().length > BASLIK_MAX) return `Başlık en çok ${BASLIK_MAX} karakter olabilir`;
  if (f.body.trim().length > METIN_MAX) return `Metin en çok ${METIN_MAX} karakter olabilir`;
  const r = f.route.trim();
  if (r && r !== "store" && !ROTA_RE.test(r)) return 'Yol "#/…" ya da "store" olmalı';
  if (f.below_version.trim() && !SURUM_RE.test(f.below_version.trim())) return "Sürüm 1.0.5 biçiminde olmalı";
  return null;
}

const PLATFORM_ADI: Record<DuyuruPlatform, string> = { all: "Tüm telefonlar", ios: "iOS", android: "Android" };

/** Geçmiş satırının hedef ve sonuç özeti. */
export function gecmisOzeti(a: Duyuru): string {
  const hedef = [a.test_only ? "Yalnız bana" : PLATFORM_ADI[a.platform] ?? a.platform, a.below_version ? `${a.below_version} altı` : ""]
    .filter(Boolean).join(" · ");
  const sonuc = [`${n(a.sent)} ulaştı`, a.failed ? `${n(a.failed)} başarısız` : "", a.queued ? `${n(a.queued)} kişi sessiz saatte bekliyor` : ""]
    .filter(Boolean).join(" · ");
  return `${hedef} · ${n(a.target_users)} kişi, ${n(a.target_devices)} cihaz · ${a.status === "sending" ? "gönderiliyor" : sonuc}`;
}

export const rotaAdi = (r: string | null) => (r === "store" ? "Mağaza sayfası" : r || "Özet");
