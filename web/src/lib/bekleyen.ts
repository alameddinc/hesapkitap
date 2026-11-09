// Bağlantısız kayıt kutusu (sahip kararı 09.11; mobil lib/bekleyen.ts ile aynı davranış ve metin). Tam çevrimdışı mod
// YOK; ama bağlantı yokken basılan "Kaydet" çıkmaza düşmez:
//   - istek (yöntem, adres, gövde, başlıklar, Idempotency-Key) taslak olarak localStorage'a yazılır (defter başına anahtar),
//     sayfa yenilense de kalır; ekranda "İnternet yok · Bağlanınca kaydedeceğiz"
//   - bağlantı gelince BİR KEZ gönderilir: aynı Idempotency-Key ile (yanıt yolda kaybolup yeniden denense de sunucu ikinci
//     kaydı açmaz), aynı anda iki gönderim olmaz (kilit, components/Bekleyen.tsx), başarılı olan silinir
//   - sunucu reddederse (4xx) taslak hata metniyle kalır, kendiliğinden yeniden denenmez ("Şimdi dene" / "Taslağı sil")
// Kapsam: elle gider/gelir ekle-düzenle, transfer, kart işlemi düzenleme. Dosyalı istek (FormData) taslak olmaz.
// Saf yardımcılar: React'e bağlı değil (node --test).

export interface Taslak {
  id: string;
  method: string;
  url: string;
  /** JSON gövde metni (olduğu gibi gönderilir) */
  body: string | null;
  /** Idempotency-Key dahil başlıklar */
  headers: Record<string, string>;
  /** kullanıcıya: "Gider · Market · ₺120,00" */
  ozet: string;
  at: number;
  /** sunucunun ret metni (4xx): kendiliğinden yeniden denenmez */
  hata?: string | null;
  dur?: boolean;
}

export type Depo = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export const ANAHTAR = "hk-bekleyen";
/** Defter başına anahtar: ortak defter üyesinin "kendi" ve "ortak" defterleri ayrı kutu. */
export const anahtar = (defter: string | null | undefined) => `${ANAHTAR}:${defter || "tek"}`;

export function oku(depo: Depo, key: string): Taslak[] {
  try {
    const v = JSON.parse(depo.getItem(key) ?? "[]");
    return Array.isArray(v) ? v.filter((t) => t && typeof t.id === "string" && typeof t.url === "string") : [];
  } catch { return []; }
}

export function yaz(depo: Depo, key: string, list: Taslak[]): void {
  try {
    if (list.length) depo.setItem(key, JSON.stringify(list));
    else depo.removeItem(key);
  } catch { /* özel mod / dolu: taslak bu oturumda bellekte kalır */ }
}

/** Aynı eylem (aynı Idempotency-Key = id) zaten bekliyorsa ikinci kez eklenmez (aynı Kaydet'e iki kez basmak). Aynı
 *  tutarda iki ayrı kayıt (iki kahve) ayrı eylemdir, ikisi de kalır. */
export function ekle(list: Taslak[], t: Taslak): Taslak[] {
  if (list.some((x) => x.id === t.id)) return list;
  // aynı kaydın (PATCH aynı adres) eski düzenlemesi yenisiyle değişir: son hâli gider
  if (t.method === "PATCH") return [...list.filter((x) => !(x.method === "PATCH" && x.url === t.url)), t];
  return [...list, t];
}

export type Sonuc = { ok: true } | { ok: false; status: number; message: string };

/** Sırayla gönder. Ağ yoksa (status 0) durur, kalanlar bekler; 4xx: taslak hata metniyle durdurulur, sıradakine geçilir;
 *  5xx: bekler (sonra yeniden denenir). zorla: durdurulmuşları da dene ("Şimdi dene"). */
export async function gonder(list: Taslak[], fetcher: (t: Taslak) => Promise<Sonuc>, zorla = false):
  Promise<{ kalan: Taslak[]; gonderilen: number }> {
  const kalan: Taslak[] = [];
  let gonderilen = 0;
  let agYok = false;
  for (const t of list) {
    if (agYok || (t.dur && !zorla)) { kalan.push(t); continue; }
    const r = await fetcher(t);
    if (r.ok) { gonderilen++; continue; }
    if (r.status === 0) { agYok = true; kalan.push(t); continue; }
    if (r.status >= 400 && r.status < 500) kalan.push({ ...t, hata: r.message, dur: true });
    else kalan.push({ ...t, hata: r.message });
  }
  return { kalan, gonderilen };
}
