import type { Payable } from "../api";
import type { DebtJarFlags, PlanJar, PlanJarRow } from "./borcKumbara";

/** GET /api/debt-plan (yetenek debt_plan; sunucu app/debt_plan.py). Bütün rakamlar ve metinler sunucudan: mobil
 *  screens/BorcPlani.tsx aynı alanları aynı sözlerle gösterir. monthly/order verilirse önizleme; PUT kaydeder. */
/** tarih: söz tarihine göre (varsayılan) · kucuk: en küçükten. Eski sunucu "faiz" / "soz" da gönderebilir. */
export type DebtOrder = "tarih" | "kucuk" | "faiz" | "soz";
/** card: kart ekstresi · person: diğer borç (kişi ya da kurum) · fixed: ödeme planı olan borç (yetenek debt_schedule) */
export type PlanKind = "card" | "person" | "fixed";
/** Ödeme planı olan borcun durumu (GET /api/debts [].schedule ile aynı) */
export interface DebtSchedule {
  installment: number; count: number; start_month: string; end_month: string; day: number;
  /** alacakta (yetenek debt_terms_receivable) kisi | kurum | diger */
  creditor_type: "vergi_sgk" | "banka" | "magaza" | "kisi" | "diger" | "kurum"; creditor_label: string;
  interest: "yok" | "var" | "bilmiyorum" | null; interest_label: string | null; paid_count: number;
  next: { no: number; month: string; date: string; amount: number; left: number } | null; due_now: number; overdue: boolean; text: string;
  /** yetenek debt_schedule_edit: planın kapsadığı tutar (borç eksi ilk taksit ayından önce ödenen), plan öncesi ödenen,
   *  kalan taksit sayısı, son taksit (eski sunucuda yok) */
  total?: number; paid_before?: number; left_count?: number; last_amount?: number;
  /** yetenek debt_payoff: faizli planda girilen bugün kapatma tutarı ve bugünkü yaklaşık karşılığı (yoksa null) */
  payoff?: SchedulePayoff | null; rate?: number | null;
}
/** Girilen kapatma tutarı (amount, date), aylık oran (yüzde), bugünkü yaklaşık kapatma tutarı (now), kalan taksitler
 *  (left), kalan faiz (interest), erken kapatma kazandırıyor mu (benefit). Hesap sunucuda. */
export interface SchedulePayoff { amount: number; date: string; rate: number | null; now: number; left: number; interest: number; benefit: boolean }
/** Borç planında ödeme planlı borcun erken kapatma satırı (yalnız faiz "var"): ask (tutar girilmemiş: nötr ipucu) · plan
 *  (kapatma ayı ve kurtulunan faiz) · no_benefit · not_reached (bu tutarla birikmiyor) · known (aylık tutar yok) */
export interface PlanPayoff {
  /** locked: Premium değil (yetenek debt_plan_plus): hesap yok, nötr kilit metni */
  state: "ask" | "plan" | "no_benefit" | "not_reached" | "known" | "locked"; title: string; text: string; note?: string; badge?: string;
  month?: string; label?: string; amount?: number | null; installments?: number | null; saved?: number | null;
  entered?: number; entered_date?: string; rate?: number | null; now?: number | null;
}
/** Kapatma satırında "Kapattım": POST /api/debts/{debt_id}/pay {amount, paid_at, close_early: true} */
export interface PlanCloseEarly { debt_id: number; amount: number; label: string; confirm: string }
export interface PlanDebt {
  key: string; kind: PlanKind; id: number; title: string; subtitle: string; order: number;
  total: number; paid: number; remaining: number; progress: number; due_date: string | null; due_label: string | null;
  overdue: boolean; closes: { month: string; label: string } | null; payable: Payable;
  min_payment?: number | null; interest_if_min?: number | null; warning?: string | null; rate_note?: string | null;
  projected_interest?: number; future_installments?: { total: number; last_month: string; last_label: string; text: string } | null;
  note?: string | null; promise?: { date: string; label: string; missed: boolean; text: string } | null;
  /** zararı azaltma: tutar kartın tamamına son ödeme gününe kadar yetmiyorsa (yoksa null; eski sunucuda yok) */
  harm?: PlanHarm | null;
  /** kind "fixed": taksit planı ve kime (Vergi / SGK, Banka, Mağaza / senet, Kişi, Diğer) */
  schedule?: DebtSchedule; creditor_label?: string | null;
  /** borcun koşulları (yetenek debt_terms; eski sunucuda yok) */
  terms?: "parca" | "tek" | "taksitli" | "bilinmiyor"; terms_label?: string; strict?: boolean; date_kind?: "kesin" | "soz" | null;
  creditor_type?: string | null; asset?: string;
  /** söz/vade tarihine yetişmiyor: sözde "Yeni tarihte anlaştık" (action), kesinde gereken aylık tutar (warning) */
  late?: PlanLate | null;
  /** tek seferde ödenecek borçta birikim */
  saving?: { month: string | null; label: string | null; amount: number; text: string } | null;
  /** kesin günü maaş gününden önce: nötr not */
  day_note?: { day: number; salary_day: number; text: string } | null;
  /** altın / döviz: "2 gram altın · ≈₺6.000" ve "TL karşılığı fiyata göre değişir." */
  qty_text?: string; tl_text?: string; approx_note?: string;
  /** kind "fixed", faiz "var": erken kapatma (yetenek debt_payoff; eski sunucuda yok) */
  payoff?: PlanPayoff | null;
  /** borç planı birikimi kumbarada (yetenek debt_jar; eski sunucuda yok): offer · locked · linked */
  jar?: PlanJar | null;
}
export interface PlanLate {
  kind: "kesin" | "soz"; date: string; label: string; head: string; open: number; overdue: boolean;
  close: { month: string; label: string } | null; text: string;
  need_monthly?: number | null; warning?: string; hint?: string;
  action?: { label: string; date: string; date_label: string; confirm: string };
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
  /** tek seferde ödenecek borçta geçmiş ayların birikimi: ödenene kadar "biriktirildi" (eski sunucuda yok) */
  saved?: { ident: string; title: string; amount: number; text: string; /** kumbaradaki gerçek bakiye (debt_jar) */ jar_id?: number }[];
  status: { key: "ok" | "behind" | "ahead" | "done"; title: string; text: string; need?: number };
  changed: { title: string; reasons: { key: string; text: string }[]; text: string; action: string; confirm: string } | null;
  continue: { action: string; confirm: string };
}
export interface PlanMonth { month: string; label: string; total: number; interest: number; left: number;
  payments: { key: string; kind: PlanKind; title: string; amount: number; closes: boolean; saving?: boolean; saved?: number;
    from_saved?: number; units_text?: string;
    /** erken kapatma satırı (anahtar borc:<id>:kapat): birikim ya da kapatma (pay_total) */
    payoff?: boolean; pay_total?: number; interest_saved?: number }[];
  /** kişi kişi (yetenek debt_plan_groups; eski sunucuda yok): aynı kişinin ödemeleri tek satır; birikim ayrı satır */
  groups?: { key: string; kind: PlanKind; title: string; amount: number; closes: boolean; count: number; keys: string[];
    saving?: boolean; units_text?: string; payoff?: boolean; pay_total?: number }[];
  fixed_due?: number; fixed_short?: number;
  /** ay sonunda birikimde duran (tek seferde ödenecek borçlar için; eski sunucuda yok) */
  saved?: number }
export interface PlanItem extends PlanJarRow { key: string; kind: PlanKind; title: string; amount: number; full: boolean; done: boolean;
  due_label: string | null; text: string; payable: Payable;
  /** aynı kişinin borçlarını ayırt eden kısa ad: not ya da "3 Mart 2026 borcu" (eski sunucuda yok) */
  sub?: string | null;
  /** tek seferde ödenecek borç: bu ay birikim (Ödedim yok) · birikim yetti: tamamı (pay_total) bir kerede */
  saving?: boolean; saved?: number; from_saved?: number; pay_total?: number; units_text?: string; approx_note?: string;
  /** erken kapatma satırı (payable yok): birikim ya da kapatma; kapatmada "Kapattım" (close_early) */
  payoff?: boolean; close_early?: PlanCloseEarly }
/** "Bu ay ödenecekler" kişi kişi: count > 1 ise açılınca borç borç (items), payable yalnız tek borçta */
export interface PlanGroup extends PlanJarRow { key: string; kind: PlanKind; title: string; count: number; amount: number; done: boolean; full: boolean;
  text: string; due_label: string | null; items: PlanItem[]; payable: Payable | null;
  saving?: boolean; units_text?: string; from_saved?: number; pay_total?: number; payoff?: boolean; close_early?: PlanCloseEarly }
export interface DebtPlanData {
  available: boolean; month: string; month_label: string;
  settings: { monthly: number | null; order: DebtOrder }; monthly: number | null;
  monthly_source: "param" | "saved" | "suggestion" | null; suggestion: { amount: number; months: number; text: string } | null;
  order: DebtOrder; orders: { key: DebtOrder; label: string; text: string }[]; intro: string;
  totals: { open: number; cards: number; people: number; count: number; card_count: number; person_count: number;
    paid_this_month: number; min_monthly_no_interest: number; interest_projected: number | null;
    /** ödeme planı olan borçlar (eski sunucuda yok) */
    fixed?: number; fixed_count?: number; fixed_this_month?: number; saving_this_month?: number;
    /** erken kapatmayla kurtulunan yaklaşık faiz (faiz gizliyse null; eski sunucuda yok) */
    payoff_saving?: number | null };
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
  /** kesin günü maaştan önce olan ödemeler ve altın/döviz notu (yetenek debt_terms) */
  day_notes?: { key: string; title: string; text: string }[]; price_note?: string | null;
  /** yalnız diğer borç varken nötr satır ("Bu tutarla diğer borçlar N ayda biter."); eski sunucuda yok */
  pace?: { months: number; text: string } | null;
  stuck: { title: string; text: string } | null; excluded: { key: string; title: string; text: string }[];
  hidden: { fees: boolean; people: boolean }; can_edit: boolean; summary_card: { eligible: boolean };
  /** borç planı ↔ kumbara düğmeleri (yetenek debt_jar; eski sunucuda yok) */
  debt_jar?: DebtJarFlags;
  /** gelişmiş kısım (yetenek debt_plan_plus): takip ve erken kapatma hesabı Premium; lock null = açık */
  plus?: PlanPlus;
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

/** Borç planının gelişmiş kısmı (Premium "borc_plani_plus"): ücretsizde takip ve erken kapatma hesabı kilitli
 *  (planın kendisi, sırası ve tutarları aynı). had_plan: daha önce başlatılmış plan kayıtlı (Premium'la geri gelir). */
export interface PlanPlusLock { key: "commit" | "payoff"; title: string; badge: string; text: string }
export interface PlanPlus { available: boolean; had_plan: boolean; lock: { commit: PlanPlusLock; payoff: PlanPlusLock } | null }

/** Kilitli takip kartının metni: kayıtlı plan varsa onu da söyler (veri silinmedi). */
export function plusCommitText(p: PlanPlus): string | null {
  if (!p.lock) return null;
  return p.had_plan ? `Başlattığın plan kayıtlı. ${p.lock.commit.text}` : p.lock.commit.text;
}
