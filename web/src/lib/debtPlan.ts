import type { Payable } from "../api";

/** GET /api/debt-plan (yetenek debt_plan; sunucu app/debt_plan.py). Bütün rakamlar ve metinler sunucudan: mobil
 *  screens/BorcPlani.tsx aynı alanları aynı sözlerle gösterir. monthly/order verilirse önizleme; PUT kaydeder. */
export type DebtOrder = "faiz" | "kucuk" | "soz";
export interface PlanDebt {
  key: string; kind: "card" | "person"; id: number; title: string; subtitle: string; order: number;
  total: number; paid: number; remaining: number; progress: number; due_date: string | null; due_label: string | null;
  overdue: boolean; closes: { month: string; label: string } | null; payable: Payable;
  min_payment?: number | null; interest_if_min?: number | null; warning?: string | null; rate_note?: string | null;
  projected_interest?: number; future_installments?: { total: number; last_month: string; last_label: string; text: string } | null;
  note?: string | null; promise?: { date: string; label: string; missed: boolean; text: string } | null;
}
export interface PlanMonth { month: string; label: string; total: number; interest: number; left: number;
  payments: { key: string; kind: "card" | "person"; title: string; amount: number; closes: boolean }[] }
export interface PlanItem { key: string; kind: "card" | "person"; title: string; amount: number; full: boolean; done: boolean;
  due_label: string | null; text: string; payable: Payable }
export interface DebtPlanData {
  available: boolean; month: string; month_label: string;
  settings: { monthly: number | null; order: DebtOrder }; monthly: number | null;
  monthly_source: "param" | "saved" | "suggestion" | null; suggestion: { amount: number; months: number; text: string } | null;
  order: DebtOrder; orders: { key: DebtOrder; label: string; text: string }[]; intro: string;
  totals: { open: number; cards: number; people: number; count: number; card_count: number; person_count: number;
    paid_this_month: number; min_monthly_no_interest: number; interest_projected: number | null };
  debts: PlanDebt[]; months: PlanMonth[];
  debt_free: { month: string; label: string; months: number; text: string; installments_until?: { month: string; label: string } } | null;
  this_month: { month: string; label: string; budget: number | null; planned: number; paid: number; items: PlanItem[] };
  warnings: { key: string; level: "warn" | "bad" | "info"; title: string; text: string }[];
  shortfall: { min_monthly: number; interest: number | null; title: string; text: string; hint: string } | null;
  /** yalnız elden borç varken nötr satır ("Bu tutarla elden borçlar N ayda biter."); eski sunucuda yok */
  pace?: { months: number; text: string } | null;
  stuck: { title: string; text: string } | null; excluded: { key: string; title: string; text: string }[];
  hidden: { fees: boolean; people: boolean }; can_edit: boolean; summary_card: { eligible: boolean };
  empty: { title: string; text: string } | null;
}

export const debtPlanUrl = (monthly?: number | null, order?: DebtOrder | null) => {
  const q = new URLSearchParams();
  if (monthly != null) q.set("monthly", String(monthly));
  if (order) q.set("order", order);
  const s = q.toString();
  return `/api/debt-plan${s ? `?${s}` : ""}`;
};

/** Sunucunun kabul ettiği en büyük aylık tutar (PUT/GET /api/debt-plan: le=100_000_000). */
export const AYLIK_EN_COK = 100_000_000;
/** Plan yazarken canlı önizlenir: önizleme gecikmesi ve kayıt gecikmesi (ms). Mobil lib/endpoints/borcPlani.ts aynı. */
export const ONIZLE_MS = 300;
export const KAYDET_MS = 1000;

/** Aylık tutar kutusunun hatası (null: geçerli). v: yazılanın sayısı (boşsa null, rakam değilse NaN). Mobil ile aynı sözler. */
export function aylikTutarHatasi(v: number | null): string | null {
  if (v == null) return "Her ay borçlara ne kadar ayırabileceğini yaz; plan ay ay çıksın.";
  if (!Number.isFinite(v) || v < 0) return "Tutarı rakamla yaz (ör. 15.000).";
  if (v === 0) return "0 ile borç kapanmaz; ayırabileceğin tutarı yaz.";
  if (v > AYLIK_EN_COK) return "Bu tutar çok büyük; en çok ₺100.000.000 yazabilirsin.";
  return null;
}
