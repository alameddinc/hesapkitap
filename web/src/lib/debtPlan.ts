import type { Payable } from "../api";

/** GET /api/debt-plan (yetenek debt_plan; sunucu app/debt_plan.py). Bütün rakamlar ve metinler sunucudan: mobil
 *  screens/BorcPlani.tsx aynı alanları aynı sözlerle gösterir. monthly/order verilirse önizleme; PUT kaydeder. */
export type DebtOrder = "faiz" | "kucuk" | "soz";
/** card: kart ekstresi · person: elden borç · fixed: ödeme planı olan borç (yetenek debt_schedule; yalnız kayıt) */
export type PlanKind = "card" | "person" | "fixed";
/** Ödeme planı olan borcun durumu (GET /api/debts [].schedule ile aynı) */
export interface DebtSchedule {
  installment: number; count: number; start_month: string; end_month: string; day: number;
  creditor_type: "vergi_sgk" | "banka" | "kisi" | "diger"; creditor_label: string;
  interest: "yok" | "var" | "bilmiyorum" | null; interest_label: string | null; paid_count: number;
  next: { no: number; month: string; date: string; amount: number; left: number } | null; due_now: number; overdue: boolean; text: string;
}
export interface PlanDebt {
  key: string; kind: PlanKind; id: number; title: string; subtitle: string; order: number;
  total: number; paid: number; remaining: number; progress: number; due_date: string | null; due_label: string | null;
  overdue: boolean; closes: { month: string; label: string } | null; payable: Payable;
  min_payment?: number | null; interest_if_min?: number | null; warning?: string | null; rate_note?: string | null;
  projected_interest?: number; future_installments?: { total: number; last_month: string; last_label: string; text: string } | null;
  note?: string | null; promise?: { date: string; label: string; missed: boolean; text: string } | null;
  /** zararı azaltma: tutar kartın tamamına son ödeme gününe kadar yetmiyorsa (yoksa null; eski sunucuda yok) */
  harm?: PlanHarm | null;
  /** kind "fixed": taksit planı ve kime (Vergi / SGK, Banka, Kişi, Diğer) */
  schedule?: DebtSchedule; creditor_label?: string;
}
export interface PlanHarm {
  title: string; steps: string[]; text: string; min_left: number | null; planned: number; interest_planned: number | null;
  below_min: boolean; due_label: string; info: string;
  /** yalnız asgari / planladığın / tamamı → yaklaşık faiz (faiz gizliyse null) */
  compare: { key: "asgari" | "plan" | "full"; label: string; amount: number; interest: number; late: boolean }[] | null;
}
/** Başlatılan plan (POST /api/debt-plan/commit; yetenek debt_plan_commit): kaydedilen hedeflere göre bu ay. */
export interface PlanCommitment {
  started_at: string; start_month: string; start_label: string; monthly: number; order: DebtOrder; order_label: string;
  debt_free: { month: string; label: string } | null; month_no: number; month_count: number; expected: number; paid: number;
  this_target: number; text: string; differs: boolean;
  status: { key: "ok" | "behind" | "ahead" | "done"; title: string; text: string; need?: number };
  changed: { title: string; reasons: { key: string; text: string }[]; text: string; action: string; confirm: string } | null;
  continue: { action: string; confirm: string };
}
export interface PlanMonth { month: string; label: string; total: number; interest: number; left: number;
  payments: { key: string; kind: PlanKind; title: string; amount: number; closes: boolean }[];
  /** kişi kişi (yetenek debt_plan_groups; eski sunucuda yok): aynı kişinin ödemeleri tek satır */
  groups?: { key: string; kind: PlanKind; title: string; amount: number; closes: boolean; count: number; keys: string[] }[];
  fixed_due?: number; fixed_short?: number }
export interface PlanItem { key: string; kind: PlanKind; title: string; amount: number; full: boolean; done: boolean;
  due_label: string | null; text: string; payable: Payable;
  /** aynı kişinin borçlarını ayırt eden kısa ad: not ya da "3 Mart 2026 borcu" (eski sunucuda yok) */
  sub?: string | null }
/** "Bu ay ödenecekler" kişi kişi: count > 1 ise açılınca borç borç (items), payable yalnız tek borçta */
export interface PlanGroup { key: string; kind: PlanKind; title: string; count: number; amount: number; done: boolean; full: boolean;
  text: string; due_label: string | null; items: PlanItem[]; payable: Payable | null }
export interface DebtPlanData {
  available: boolean; month: string; month_label: string;
  settings: { monthly: number | null; order: DebtOrder }; monthly: number | null;
  monthly_source: "param" | "saved" | "suggestion" | null; suggestion: { amount: number; months: number; text: string } | null;
  order: DebtOrder; orders: { key: DebtOrder; label: string; text: string }[]; intro: string;
  totals: { open: number; cards: number; people: number; count: number; card_count: number; person_count: number;
    paid_this_month: number; min_monthly_no_interest: number; interest_projected: number | null;
    /** ödeme planı olan borçlar (eski sunucuda yok) */
    fixed?: number; fixed_count?: number; fixed_this_month?: number };
  debts: PlanDebt[]; months: PlanMonth[];
  debt_free: { month: string; label: string; months: number; text: string; installments_until?: { month: string; label: string };
    /** bu tutarla kartlarda faiz işliyor: borçsuz ay başarı gibi (yeşil) gösterilmez */
    interest?: boolean } | null;
  this_month: { month: string; label: string; budget: number | null; planned: number; paid: number; items: PlanItem[]; groups?: PlanGroup[] };
  /** ⓘ: tutarların nasıl yuvarlandığı (eski sunucuda yok) */
  rules_info?: string;
  /** aylık tutar bu ayın taksitlerine yetmiyor */
  fixed_short?: { due: number; short: number; title: string; text: string } | null;
  warnings: { key: string; level: "warn" | "bad" | "info"; title: string; text: string }[];
  shortfall: { min_monthly: number; interest: number | null; title: string; text: string; hint: string; allocation?: string } | null;
  commitment?: PlanCommitment | null; commit_offer?: { title: string; text: string; action: string; /** faizli plan: ikincil düğme, uyarıdan sonra */ secondary?: boolean } | null;
  commit_history?: { started_at: string; start_month: string; monthly: number; ended_at: string | null; label: string }[];
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
