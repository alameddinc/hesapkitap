const tlFmt = new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY", maximumFractionDigits: 2 });
const num1 = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 });
const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];

// Gizlilik: açıkken tüm tutarlar maskelenir (dışarıdan maaş vb. görünmesin)
const MASK = "•••• ₺";
let amountsHidden = false;
export const setAmountsHidden = (v: boolean) => { amountsHidden = v; };
export const areAmountsHidden = () => amountsHidden;

export const tl = (x: number) => (amountsHidden ? MASK : tlFmt.format(x));
/** Genel gizlemeyi yok sayar: kullanıcı o bölümde gözle açıkça "göster" dediyse. */
export const tlShown = (x: number) => tlFmt.format(x);
/** Sunucudan gelen metinlerdeki tutarları da maskele ("16.793,18 ₺ taksit…" → "•••• ₺ taksit…"). */
export const maskText = (s: string) =>
  amountsHidden ? s.replace(/[-+]?₺?\s?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?\s?₺|₺\s?[-+]?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?/g, MASK) : s;
export function tlCompact(x: number) {
  if (amountsHidden) return MASK;
  const a = Math.abs(x);
  if (a >= 1e6) return `${num1.format(x / 1e6)} mn ₺`;
  if (a >= 1e3) return `${num1.format(x / 1e3)} bin ₺`;
  return `${num1.format(x)} ₺`;
}

export function monthLabel(ym: string, short = false) {
  const [y, m] = ym.split("-").map(Number);
  const name = MONTHS[m - 1];
  return short ? `${name.slice(0, 3)} ${String(y).slice(2)}` : `${name} ${y}`;
}

export function dateTR(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
}

/** Sunucu zaman damgası (SQLite CURRENT_TIMESTAMP: UTC, saat dilimsiz "2026-10-30 13:07:14") → Date. Saat dilimi
 *  yazılıysa (Z / +03:00) olduğu gibi; yalnız tarihse yerel gece yarısı. Sözleşme: *_at / last_* alanları UTC'dir. */
export function parseTs(ts: string): Date {
  if (/^\d{4}-\d{2}-\d{2}$/.test(ts)) return new Date(`${ts}T00:00:00`);
  const iso = ts.replace(" ", "T");
  return new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`);
}

/** Zaman damgasının yerel (TSİ) tarihi: gece yarısına yakın UTC saatleri doğru güne düşer. */
export function tsDateTR(ts: string) {
  const d = parseTs(ts);
  return isNaN(d.getTime()) ? dateTR(ts) : d.toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Bugünün yerel tarihi (YYYY-AA-GG): toISOString UTC verir, gece yarısından sonra bir gün geride kalır. */
export function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function addMonths(ym: string, n: number) {
  const [y, m] = ym.split("-").map(Number);
  const t = y * 12 + (m - 1) + n;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, "0")}`;
}

export const KIND_LABEL: Record<string, string> = {
  harcama: "Harcama",
  iade: "İade",
  faiz_ucret: "Faiz/Ücret",
  odeme: "Kart ödemesi",
  gelir: "Gelir",
  gider: "Gider",
  borc: "Borç hareketi",
};

export const PLAN_LABEL: Record<string, string> = {
  kart_gercek: "Kart ekstresi",
  taksit: "Taksitler",
  abonelik: "Abonelikler",
  degisken: "Tahmini kart harcaması",
  sabit: "Sabit giderler",
  tek_seferlik: "Tek seferlik",
  fatura: "Faturalar",
  tasarruf_notu: "Kısma hedefi",
  gelir: "Gelir",
};

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

/** Arama için katla: büyük/küçük, Türkçe aksan ve ı/i farkını yok say ("SCRIBD" ~ "scribd", "İSPARK" ~ "ispark"). */
export const fold = (s: string) =>
  s.toLocaleLowerCase("tr").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/ı/g, "i");

/** Kullanıcının yazdığı tutar: "214.350,75" (Türk) ya da "214350.75" / "214350" → sayı. Geçersizse NaN. */
export function parseAmount(v: string): number {
  const t = v.trim().replace(/\s|₺|TL/gi, "");
  if (!t) return NaN;
  if (t.includes(",")) return Number(t.replace(/\./g, "").replace(",", "."));
  // tek nokta ve arkasında 3 hane varsa binlik ayırıcıdır ("220.000"); değilse ondalık
  if (/^\d{1,3}(\.\d{3})+$/.test(t)) return Number(t.replace(/\./g, ""));
  return Number(t);
}
