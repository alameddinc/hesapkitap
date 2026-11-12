// Özet "Bu dönem ödenen faiz ve ücret" kartının metni (sunucu summary.interest_paid; tutar sunucuda, burada toplama yok).
// Mobil src/lib/faizOzet.ts ile aynı metinler. Dil nötr: faiz yoksa olumlu ("faiz ödemedin"), varsa yalnız bilgi;
// asgari ödeme ya da faize yönlendiren öneri yok. Faiz GELİRİ ayrı satırdır (faizGeliriNotu), burada yok.
import type { InterestLatest, InterestPaid } from "../api.ts";

export interface FaizKartMetni {
  title: string;
  /** tutar (biçimlenmiş); faiz yoksa null */
  value: string | null;
  sub: string;
  tone: "warn" | "ok" | "neutral";
}

/** dönem öbeği: içinde bulunulan dönem "Bu dönem", geçmiş dönem "Eylül döneminde" (ad: summary.period.name). */
export const donemObegi = (past: boolean, name?: string | null) => (past && name ? `${name} döneminde` : "Bu dönem");

/** null: kart yok (eski sunucu, ortak defterde faiz gizli ya da hiç kart ekstresi yok). para: tutar biçimleyici. */
export function faizKartMetni(p: InterestPaid | null | undefined, para: (x: number) => string, donem = "Bu dönem"): FaizKartMetni | null {
  if (!p || !p.has_cards) return null;
  if (p.state === "var") {
    const parts = ([["faiz", p.interest], ["vergi", p.tax], ["ücret", p.fee]] as const)
      .filter(([, v]) => v > 0).map(([k, v]) => `${k} ${para(v)}`);
    return { title: `${donem} ödenen faiz ve ücret`, value: para(p.total), sub: parts.join(" · ") || "Faiz ve ücretlerin ayrıntısı", tone: "warn" };
  }
  if (p.state === "yok") return { title: `${donem} faiz ödemedin`, value: null, sub: "Kart ekstrelerinde faiz ya da ücret yok", tone: "ok" };
  // bu dönemin ekstresi yok: son ekstreli dönemin değeri (sunucu last_period; ayın ilk yarısında hep bu durum)
  const lp = p.last_period;
  if (lp) {
    const o = `${lp.name} döneminde`;
    return lp.state === "var"
      ? { title: `${o} ödenen faiz ve ücret`, value: para(lp.total), sub: SONRA_GUNCELLENIR, tone: "warn" }
      : { title: `${o} faiz ödemedin`, value: null, sub: SONRA_GUNCELLENIR, tone: "ok" };
  }
  return { title: `${donem} faiz ve ücret görünmüyor`, value: null, sub: "Bu dönemin kart ekstresi gelince kesinleşir", tone: "neutral" };
}

export const SONRA_GUNCELLENIR = "Bu dönemin ekstresi gelince güncellenir";

/** Özet'in faiz satırı ve kartı (sunucu summary.interest_latest): dönem adı yerine kart başına SON ekstre — "Son
 *  ekstrelerinde faiz ve ücret ₺1.114,82" / alt metin "Mavi Bank 7 Kas · Örnek Bank 5 Kas ekstreleri". Tek ekstrede
 *  "Son ekstrende". null: satır yok (faiz gizli, kart ekstresi yok). Mobil lib/faizOzet.ts ile aynı. */
export function faizSonEkstreMetni(p: InterestLatest | null | undefined, para: (x: number) => string): FaizKartMetni | null {
  if (!p) return null;
  const son = p.statement_count === 1 ? "Son ekstrende" : "Son ekstrelerinde";
  if (p.state === "var") return { title: `${son} faiz ve ücret`, value: para(p.total), sub: p.source, tone: "warn" };
  return { title: `${son} faiz yok`, value: null, sub: p.source, tone: "ok" };
}

/** Özet bileşeni: yeni sunucu (interest_latest alanı var) ve içinde bulunulan dönemde son ekstre metni; geçmiş dönemde
 *  ya da eski sunucuda dönem metni (faizKartMetni). Ortak defterde faiz gizliyse ikisi de null. */
export function ozetFaizMetni(s: { interest_paid?: InterestPaid | null; interest_latest?: InterestLatest | null }, para: (x: number) => string,
  past: boolean, donem: string): FaizKartMetni | null {
  if (!past && s.interest_latest !== undefined) return faizSonEkstreMetni(s.interest_latest, para);
  return faizKartMetni(s.interest_paid, para, donem);
}
