// Kumbara katılımcıları ve aylık pay → ekran metni (yetenek kumbara_shares). Rakamlar sunucuda (app/jar_shares.py);
// burada yalnız söz. Mobil src/lib/kumbaraPay.ts aynı dosya. Dil suçlayıcı değil: "geride", "borçlu" yok; ne kadar
// kaldığı yazılır ("Bu ay Ayşe'nin payından ₺500 kaldı"). Faiz/yatırım önerisi yok.

export type PayDurum = "tamam" | "eksik" | "bekliyor" | "paysiz" | "muaf";
export interface AyTutar { month: string; amount: number }
export interface PayKisi {
  id: number; user_id: number | null; name: string; app: boolean; me: boolean; manual: boolean;
  share: number | null; paid: number; left: number | null; over: number | null; state: PayDurum;
  total: number; months_full: number; months_counted: number;
  /** v2 (kumbara_shares_v2; eski sunucuda yok): bu aya sayılan, önceki aylara sayılan, geçen aydan devreden fazla */
  paid_for_month?: number; to_past?: number; carried_in?: number; credit?: number;
  /** "Bu ay yok" işaretli mi; işaretli aylar */
  exempt?: boolean; exempt_months?: string[];
  /** önceki aylardan tamamlanacak (eskiden yeniye) */
  due?: AyTutar[]; due_total?: number;
}
/** Bir eklemenin nereye sayıldığı: önce en eski tamamlanacak aylar, sonra o ayın payı, artanı fazla. */
export interface PayDagilim { person_id: number | null; past: AyTutar[]; month: string; month_amount: number; extra: number }
export interface PayDurumu {
  /** v2: kumbaranın birimi (TRY, ALTIN_24, CEYREK, USD …) ve hassasiyeti (100 / adette 1) */
  unit?: string; scale?: number; carry_over?: boolean; allocations?: Record<string, PayDagilim>;
  /** v2: bu ayın paylarına sayılan (önceki aylara sayılan hariç, devreden fazla dahil) */
  paid_month?: number;
  month: string; target: number | null; basis: "aylik" | "hedef" | "tamam" | null; total: number; paid: number;
  remind_day: number; people: PayKisi[];
  removed: { id: number; name: string; total: number; months_full: number; months_counted: number; due?: AyTutar[]; due_total?: number }[];
  behind: number[]; all_done: boolean; history_months: number;
  /** paylar toplamı hedefin gerektirdiğinden azsa ayda gereken (hedef kutusundaki rakam); yoksa null / eski sunucuda yok */
  needed?: number | null;
}

const AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
export const ayAdi = (ym: string) => AYLAR[Number(ym.slice(5, 7)) - 1] ?? ym;
/** Ay adı; başka yıldaysa yılıyla ("Aralık 2025"). */
export const ayAdiY = (ym: string, ref: string) => (ym.slice(0, 4) === ref.slice(0, 4) ? ayAdi(ym) : `${ayAdi(ym)} ${ym.slice(0, 4)}`);
const AY_DAN = ["Ocak'tan", "Şubat'tan", "Mart'tan", "Nisan'dan", "Mayıs'tan", "Haziran'dan", "Temmuz'dan", "Ağustos'tan",
  "Eylül'den", "Ekim'den", "Kasım'dan", "Aralık'tan"];
/** "Ekim'den" (çıkma hâli). */
export const aydan = (ym: string) => AY_DAN[Number(ym.slice(5, 7)) - 1] ?? ym;
const ve = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} ve ${xs[xs.length - 1]}`);

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
  if (p.state === "muaf") return { rozet: "Bu ay yok", ton: "notr", cumle: "Bu ay için pay yok olarak işaretli" };
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
export function ozetMetni(s0: PayDurumu, para: (n: number) => string): string {
  const s = { ...s0, paid: s0.paid_month ?? s0.paid };
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

/** Paylar toplamı hedefe yetmiyorsa nötr not (yoksa null): "Hedefe yetişmek için ayda ₺7.000 gerekiyor; paylar toplamı ₺6.000." */
export const yetmeyenNot = (s: PayDurumu, para: (n: number) => string) =>
  s.needed ? `Hedefe yetişmek için ayda ${para(s.needed)} gerekiyor; paylar toplamı ${para(s.total)}.` : null;

/** Tek dokunuş: ayda toplamı hedefin gerektirdiğine çek (elle paylar korunur, kalan eşit bölünür). */
export const toplamiYapEtiketi = (s: PayDurumu, para: (n: number) => string) => (s.needed ? `Toplamı ${para(s.needed)} yap` : null);

/** Önceki aylardan tamamlanacak: "Tamamlanacak: ₺500 (Ekim)" · "Tamamlanacak: ₺1.300 (Eylül ₺800, Ekim ₺500)". */
export function tamamlanacakMetni(p: Pick<PayKisi, "due" | "due_total">, para: (n: number) => string, ref: string): string | null {
  const d = p.due ?? [];
  if (!d.length || !p.due_total) return null;
  if (d.length === 1) return `Tamamlanacak: ${para(p.due_total)} (${ayAdiY(d[0]!.month, ref)})`;
  return `Tamamlanacak: ${para(p.due_total)} (${d.map((x) => `${ayAdiY(x.month, ref)} ${para(x.amount)}`).join(", ")})`;
}

/** Fazla devri açıkken: "Geçen aydan ₺500 fazla geldi". */
export const devirMetni = (p: Pick<PayKisi, "carried_in">, para: (n: number) => string) =>
  p.carried_in && p.carried_in > 0 ? `Geçen aydan ${para(p.carried_in)} fazla geldi` : null;

/** Eklemenin dağılımı (yalnız önceki aya sayılan varsa): "₺500 Ekim eksiğine, ₺200 Kasım payına sayıldı." */
export function dagilimMetni(a: PayDagilim | undefined, para: (n: number) => string, ref: string): string | null {
  if (!a || !a.past.length) return null;
  const past = `${ve(a.past.map((x) => `${para(x.amount)} ${ayAdiY(x.month, ref)}`))} eksiğine`;
  const ay = a.month_amount > 0 ? `, ${para(a.month_amount)} ${ayAdiY(a.month, ref)} payına` : "";
  return `${past}${ay} sayıldı.`;
}

/** Uygulamayı kullanmayana gönderilecek hazır, nötr mesaj (kullanıcı kendi paylaşır). Muafta null. */
export function hatirlatMesaji(kumbara: string, s: PayDurumu, p: PayKisi, para: (n: number) => string): string | null {
  if (p.state === "muaf" && !p.due_total) return null;
  const ay = ayAdi(s.month);
  const geldi = (p.paid_for_month ?? p.paid) + (p.carried_in ?? 0);
  const bas = p.share ? `${kumbara}: ${ay} payın ${para(p.share)}, şu ana kadar ${para(geldi)} geldi.`
    : `${kumbara}: ${ay} ayında şu ana kadar ${para(geldi)} geldi.`;
  const d = p.due ?? [];
  const tam = !d.length || !p.due_total ? "" : d.length === 1 ? ` ${aydan(d[0]!.month)} ${para(p.due_total)} tamamlanacak.`
    : ` Önceki aylardan ${para(p.due_total)} tamamlanacak.`;
  return bas + tam;
}

/** "Bu ay yok" seçimi için aylar: 6 ay önceden 6 ay sonrasına ("Kasım 2026"). */
export function aySecenekleri(cur: string, geri = 6, ileri = 6): { v: string; t: string }[] {
  const [y, m] = cur.split("-").map(Number) as [number, number];
  const out: { v: string; t: string }[] = [];
  for (let i = -geri; i <= ileri; i++) {
    const k = y * 12 + (m - 1) + i;
    const v = `${Math.floor(k / 12)}-${String((k % 12) + 1).padStart(2, "0")}`;
    out.push({ v, t: `${ayAdi(v)} ${v.slice(0, 4)}` });
  }
  return out;
}
