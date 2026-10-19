const tlFmt = new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY", maximumFractionDigits: 2 });
const num1 = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 });
const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];

export const tl = (x: number) => tlFmt.format(x);
export function tlCompact(x: number) {
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
