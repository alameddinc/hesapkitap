// Kumbara katılımcıları ve aylık pay → ekran metni (yetenek kumbara_shares). Rakamlar sunucuda (app/jar_shares.py);
// burada yalnız söz. Mobil src/lib/kumbaraPay.ts aynı dosya. Dil suçlayıcı değil: "geride", "borçlu" yok; ne kadar
// kaldığı yazılır ("Bu ay Ayşe'nin payından ₺500 kaldı"). Faiz/yatırım önerisi yok.

export type PayDurum = "tamam" | "eksik" | "bekliyor" | "paysiz";
export interface PayKisi {
  id: number; user_id: number | null; name: string; app: boolean; me: boolean; manual: boolean;
  share: number | null; paid: number; left: number | null; over: number | null; state: PayDurum;
  total: number; months_full: number; months_counted: number;
}
export interface PayDurumu {
  month: string; target: number | null; basis: "aylik" | "hedef" | "tamam" | null; total: number; paid: number;
  remind_day: number; people: PayKisi[]; removed: { id: number; name: string; total: number; months_full: number; months_counted: number }[];
  behind: number[]; all_done: boolean; history_months: number;
}

const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
export const ayAdi = (ym: string) => AYLAR[Number(ym.slice(5, 7)) - 1] ?? ym;

const UNLU = "aeıioöuüAEIİOÖUÜ";
/** Özel adın tamlayan eki: Ayşe'nin, Mehmet'in, Oya'nın, Umut'un, Gül'ün. */
export function iyelik(ad: string): string {
  const a = ad.trim();
  const son = [...a].reverse().find((h) => UNLU.includes(h))?.toLocaleLowerCase("tr") ?? "e";
  const u = "aı".includes(son) ? "ı" : "ei".includes(son) ? "i" : "ou".includes(son) ? "u" : "ü";
  const sonHarf = a.slice(-1);
  return `${a}'${UNLU.includes(sonHarf) ? "n" : ""}${u}n`;
}

/** Ekranda kişi adı: giriş yapan "Sen". */
export const kisiAdi = (p: Pick<PayKisi, "me" | "name">) => (p.me ? "Sen" : p.name);

export interface KisiMetni { rozet: string | null; ton: "iyi" | "uyari" | "notr"; cumle: string }

/** para: TL biçimi (gizli tutar maskesi dahil). */
export function kisiMetni(p: PayKisi, para: (n: number) => string): KisiMetni {
  if (p.state === "paysiz") {
    return { rozet: null, ton: "notr", cumle: p.paid > 0 ? `Bu ay ${para(p.paid)} ${p.me ? "attın" : "attı"}` : "Bu ay için pay yok" };
  }
  if (p.state === "tamam") {
    const fazla = p.over && p.over > 0 ? `, ${para(p.over)} fazla` : "";
    return { rozet: "Tamam", ton: "iyi", cumle: `Bu ayki pay${p.me ? "ın" : "ı"} tamam${fazla}` };
  }
  return {
    rozet: p.state === "eksik" ? "Kısmen" : "Bekleniyor", ton: "uyari",
    cumle: p.me ? `Bu ay payından ${para(p.left ?? 0)} kaldı` : `Bu ay ${iyelik(p.name)} payından ${para(p.left ?? 0)} kaldı`,
  };
}

/** Kartın üst satırı: bu ay ne kadar geldi. */
export function ozetMetni(s: PayDurumu, para: (n: number) => string): string {
  const ay = ayAdi(s.month);
  if (!s.total) return s.paid > 0 ? `${ay}: ${para(s.paid)} geldi` : `${ay}: henüz ekleme yok`;
  if (s.all_done) return `${ay} payları tamam: ${para(s.paid)} geldi`;
  // fazla atan eksik atanı kapatmaz: toplam dolsa da kalanı olan varsa söylenir
  const kalan = s.behind.length && s.paid >= s.total ? `; ${s.behind.length} kişinin payında kalan var` : "";
  return `${ay}: ${para(s.paid)} / ${para(s.total)} geldi${kalan}`;
}

/** Ayın toplam payı nereden. */
export function temelMetni(s: PayDurumu, para: (n: number) => string): string {
  if (s.basis === "aylik") return `Ayda toplam ${para(s.target ?? 0)}; elle pay verilmeyenler eşit böler.`;
  if (s.basis === "hedef") return `Hedef ve son tarihe göre bu ay toplam ${para(s.target ?? 0)}; elle pay verilmeyenler eşit böler.`;
  if (s.basis === "tamam") return "Hedefe ulaşıldı: bu ay pay yok.";
  return "Aylık toplam yok: yalnız elle verilen paylar sayılır. Hedef ve son tarih ya da ayda toplam koyarsan eşit bölünür.";
}

/** Geçmiş aylar: "4/5 ay tam" (payı olan ay yoksa null). */
export const gecmisMetni = (p: { months_full: number; months_counted: number }) =>
  p.months_counted > 0 ? `${p.months_full}/${p.months_counted} ay tam` : null;
