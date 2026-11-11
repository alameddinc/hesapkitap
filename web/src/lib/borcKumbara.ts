// Borç planı birikimi kumbarada (sunucu yeteneği "debt_jar"; app/debt_plan.py, POST /api/debts/{id}/jar,
// POST /api/jars/{id}/pay-debt). Bağlantı Premium "kumbara" kuralıdır; plan ücretsiz. Bütün tutar ve metinler sunucudan;
// burada yalnız hangi düğmenin görüneceği (mobil src/lib/borcKumbara.ts ile aynı kural).

/** "Kumbaradan öde": tek seferde borçta kumbaradaki kadar (en çok kalan borç; azsa kısmi), kredide kapatma tutarı. */
export interface PlanJarPay {
  label: string; jar_id: number; debt_id: number; amount: number; from_jar: number; close_early: boolean; partial: boolean;
  leftover: number; text: string; confirm: string;
}
/** Bağlı kumbaranın durumu borç planının takviminden (kumbara kartında tek kaynak): tamam · bu_ay · yolunda ·
 *  baslamadi (plan sonraki ayda başlıyor: nötr) · plansiz. line/detail sunucudan. */
export interface PlanJarPlan {
  status: "tamam" | "bu_ay" | "yolunda" | "baslamadi" | "plansiz"; badge: string | null; line: string; detail: string | null;
  this_month: number; schedule: { month: string; label: string; amount: number }[]; pay_month: string | null; pay_label: string | null;
}
/** "Kumbaraya ayır" (tutar sorulur): plan bu ay ayırmıyorsa da; amount bu ayın plan tutarı (yoksa null) */
export interface PlanJarPut { label: string; jar_id: number; note: string; amount: number | null }
/** debts[].jar: offer ("Kumbara aç") · locked (Premium değil, nötr öneri) · linked (bağlı kumbara) */
export type PlanJar =
  | { state: "offer"; label: string; text: string; name: string; goal: number | null; deadline: string | null;
      deadline_label: string | null; monthly: number | null; /** planın takvimi (aylık yerine gösterilir) */ plan_text?: string | null }
  | { state: "locked"; label: string; badge: string; text: string }
  | { state: "linked"; id: number; name: string; balance: number; goal: number | null; pct: number | null; used: boolean;
      text: string; can_move: boolean; pay: PlanJarPay | null; plan?: PlanJarPlan | null; put?: PlanJarPut | null };
/** "Kumbaraya ayır" (POST /api/jars/{jar_id}/moves {amount, note}) */
export interface PlanJarAction { label: string; jar_id: number; amount: number; note: string }
export interface PlanJarRow { jar_id?: number; jar_action?: PlanJarAction; jar_pay?: PlanJarPay }
export interface DebtJarFlags {
  available: boolean; premium: boolean; can_open: boolean; can_move: boolean; can_pay: boolean;
  lock: { state: "locked"; label: string; badge: string; text: string } | null; saved_this_month: number;
}
export interface JarPayResult {
  ok: true; payment_id: number; move_id: number; paid: number; from_jar: number; leftover: number; jar_closed: boolean;
  debt_closed: boolean; debt_left: number; close_early: boolean; message: string;
  ask: { key: "leftover"; amount: number; text: string; close: string; convert: string } | null;
}
/** GET /api/jars [].debt: "Borç: <ad> için" etiketi, borcun durumu ve borç kapandı/silindiyse nötr öneri */
export interface JarDebt {
  id: number | null; title: string | null; label: string; route: string; state: "acik" | "kapandi" | "silindi" | null;
  remaining: number | null; suggestion: { text: string; close: string; convert: string } | null;
  /** açık borçta planın durumu (varsa kumbaranın kendi hedef durumu gelmez) */
  plan?: PlanJarPlan | null;
}
/** POST /api/debts/{id}/jar yanıtı: message planın o ayki durumuna göre ("Bu ay ₺… ayır" / "Plan … ayırır") */
export interface JarOpenResult { id: number; message?: string }

export type JarButton = "ode" | "ayir" | null;

/** "Bu ay ödenecekler" satırındaki kumbara düğmesi. Eski sunucu (yetenek yok) ya da yetki yoksa null; ödenen satırda yok. */
export function jarButton(row: PlanJarRow & { done: boolean }, flags: DebtJarFlags | null | undefined, cap: boolean): JarButton {
  if (!cap || !flags || row.done) return null;
  if (row.jar_pay && flags.can_pay) return "ode";
  if (row.jar_action && flags.can_move) return "ayir";
  return null;
}

/** Borç kartındaki kumbara kutusu: eski sunucuda ya da kumbara yoksa çizilmez. */
export function jarBox(jar: PlanJar | null | undefined, cap: boolean): PlanJar | null {
  return cap && jar ? jar : null;
}

/** Kumbaradan öde penceresi: kredide tutar yazılır (bankanın kapatma tutarı), diğerinde sunucunun tutarı. */
export function payAmountEditable(p: PlanJarPay): boolean {
  return p.close_early;
}

/** Hedef / son tarih / aylık özeti ("Hedef ₺12.000 · Şubat 2027 · ayda ₺3.000"). */
export function offerSummary(o: Extract<PlanJar, { state: "offer" }>, para: (n: number) => string): string {
  if (o.plan_text) return [o.goal ? `Hedef ${para(o.goal)}` : null, o.plan_text].filter(Boolean).join(" · ");
  return [o.goal ? `Hedef ${para(o.goal)}` : null, o.deadline_label, o.monthly ? `ayda ${para(o.monthly)}` : null]
    .filter(Boolean).join(" · ");
}

/** Kumbara açıldı toast'ı: sunucunun planına göre sözü; eski yanıt (message yok) için genel söz. */
export function openToast(r: JarOpenResult): string {
  return r.message ?? "Kumbara açıldı; istediğinde \"Kumbaraya ayır\"la para koy.";
}

/** Durum rozeti tonu: tamam/bu ay/yolunda iyi; başlamadı ve plansız nötr ("Yolunda" denmez). */
export function planTon(p: PlanJarPlan): "iyi" | "notr" {
  return p.status === "baslamadi" || p.status === "plansiz" ? "notr" : "iyi";
}
