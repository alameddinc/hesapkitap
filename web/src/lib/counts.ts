// İşlem toplamları tek kuraldan (sunucu finance.counts, a18ac2a): satırdaki counts_as_spend / counts_as_income bayrağı.
// Eski sunucu bayrak vermezse aynı kuralın istemci kopyası (silinen, atlanan, bekleyen, eşleşmiş, tahmini, birikim,
// kart ödemesi, borç hareketi sayılmaz; iade eksi tutarla gideri düşürür).
import type { Row } from "../api";

const SAVINGS = "Altın & Döviz Birikimi";

/** Satır gidere sayılır mı? */
export function countsAsSpend(r: Row): boolean {
  if (r.counts_as_spend !== undefined) return r.counts_as_spend;
  if (r.deleted || r.pending || r.skipped || r.linked || r.estimated) return false;
  return r.kind !== "gelir" && r.kind !== "odeme" && r.kind !== "borc" && r.category !== SAVINGS;
}

/** Satır gelire sayılır mı? */
export function countsAsIncome(r: Row): boolean {
  if (r.counts_as_income !== undefined) return r.counts_as_income;
  if (r.deleted || r.pending || r.skipped || r.linked || r.estimated) return false;
  return r.kind === "gelir";
}

const round = (x: number) => Math.round(x * 100) / 100;

/** Süzgeçten geçen satırların gider ve gelir toplamı (kuruşa yuvarlı). */
export function rowTotals(rows: Row[]): { spend: number; income: number } {
  let spend = 0, income = 0;
  for (const r of rows) {
    if (countsAsSpend(r)) spend += r.amount;
    else if (countsAsIncome(r)) income += r.amount;
  }
  return { spend: round(spend), income: round(income) };
}

/** Pivot/tablo hücresi: gider artı, gelir eksi; sayılmayan satır (kart ödemesi, borç, birikim) 0. */
export const netSpend = (r: Row) => (countsAsSpend(r) ? r.amount : countsAsIncome(r) ? -r.amount : 0);
