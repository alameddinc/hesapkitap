// İşlemler süzgeci adres çubuğunda: #/islemler?kategori=…&isyeri=…&kart=…&yil=2026&gider=1&kim=u7
// Yıl özeti, İstatistik ve Özet'teki ">" satırları buraya bağlanır; liste o satırın saydığı işlemleri gösterir.
// Sunucu /api/transactions yalnız ay (ya da tüm geçmiş) süzer: kategori/işyeri/kart/yıl istemcide uygulanır.
import type { Row } from "../api";
import { countsAsSpend } from "./counts.ts";

export interface TxnFilter {
  kategori?: string;
  isyeri?: string;
  kart?: string;
  /** dönem yılı (satırın month alanı "2026-…"); verilince ay seçici yerine yılın tüm dönemleri */
  yil?: number;
  /** yalnız gidere sayılan satırlar (sunucu bayrağı counts_as_spend): özetteki tutar ve işlem sayısıyla aynı küme */
  gider?: boolean;
  /** faiz/ücret satırları hariç: işyeri ve kart sıralamaları (sunucu _spends(r, CARD_SPEND [+ EXPENSE])) faizi saymaz */
  faizsiz?: boolean;
  /** Aile "Kim ne harcadı": harcayan (u<id> | ortak). Satırın spender alanı sunucunun kuralından (finance.Spenders):
   *  Kim ne harcadı raporu ve /api/transactions?who ile aynı küme */
  kim?: string;
}

export function parseTxnFilter(hash: string): TxnFilter {
  const p = new URLSearchParams(hash.split("?")[1] ?? "");
  const s = (k: string) => p.get(k)?.trim() || undefined;
  const y = Number(p.get("yil"));
  return {
    kategori: s("kategori"), isyeri: s("isyeri"), kart: s("kart"),
    yil: Number.isInteger(y) && y >= 2000 && y <= 2100 ? y : undefined,
    gider: p.get("gider") === "1" || undefined,
    faizsiz: p.get("faizsiz") === "1" || undefined,
    kim: /^(u\d+|ortak)$/.test(p.get("kim") ?? "") ? p.get("kim")! : undefined,
  };
}

export const hasTxnFilter = (f: TxnFilter) => !!(f.kategori || f.isyeri || f.kart || f.yil || f.gider || f.faizsiz || f.kim);

/** ui.go yerine: süzgeçli İşlemler adresi (hash, başında "#"sız) */
export function txnFilterHash(f: TxnFilter): string {
  const p = new URLSearchParams();
  if (f.kategori) p.set("kategori", f.kategori);
  if (f.isyeri) p.set("isyeri", f.isyeri);
  if (f.kart) p.set("kart", f.kart);
  if (f.yil) p.set("yil", String(f.yil));
  if (f.gider) p.set("gider", "1");
  if (f.faizsiz) p.set("faizsiz", "1");
  if (f.kim) p.set("kim", f.kim);
  const qs = p.toString();
  return `/islemler${qs ? `?${qs}` : ""}`;
}

/** Kırılımdaki grup adı (sunucu finance.report_category, ?zakat=1): zekâta bağlı satır "Zekât", diğerleri kategorisi.
 *  Eski sunucuda donation_kind yok: kategori. */
export const ZAKAT_GROUP = "Zekât";
export const reportCategory = (r: Pick<Row, "category" | "donation_kind">): string =>
  r.donation_kind === "zekat" ? ZAKAT_GROUP : r.category;

/** currentMonth: yıl süzgecinde (gider=1) içinde bulunulan dönemden sonraki taksit parçaları yıl özetine girmez; burada da. */
export function matchTxnFilter(r: Row, f: TxnFilter, currentMonth?: string): boolean {
  if (f.kategori && reportCategory(r) !== f.kategori) return false;
  if (f.isyeri && r.merchant !== f.isyeri) return false;
  if (f.kart && r.card !== f.kart) return false;
  if (f.yil && !r.month.startsWith(`${f.yil}-`)) return false;
  if (f.yil && f.gider && currentMonth && r.month > currentMonth) return false;
  if (f.gider && !countsAsSpend(r)) return false;
  if (f.faizsiz && r.kind === "faiz_ucret") return false;
  if (f.kim && r.spender !== f.kim) return false;
  return true;
}

/** Süzgeçli İşlemler'e git (ui.go gibi sayfa başına kaydırır) */
export function openTxns(f: TxnFilter): void {
  window.location.hash = txnFilterHash(f);
  window.scrollTo(0, 0);
}

/** Süzgeç başlığı: "Kira & Aidat · 2026 yılı · yalnız gider". kimAd: harcayan anahtarı → ad ("Ayşe", "Sen", "Ortak"). */
export function txnFilterLabel(f: TxnFilter, mask: (s: string) => string = (s) => s, kimAd: (k: string) => string = (k) => k): string {
  return [f.kim && `Kim harcadı: ${kimAd(f.kim)}`, f.kategori, f.isyeri && mask(f.isyeri), f.kart && `Kart: ${f.kart}`, f.yil && `${f.yil} yılı`, f.gider && "yalnız gider", f.faizsiz && "faiz/ücret hariç"]
    .filter(Boolean).join(" · ");
}
