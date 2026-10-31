import { ApiError, call } from "./lib/apiError";
import { toServer, type ReceiptCheck, type ReceiptItem, type Reread } from "./components/receipt/check";

export type Kind = "harcama" | "iade" | "faiz_ucret" | "odeme" | "gelir" | "gider" | "borc";

export interface Row {
  uid: string;
  source: "card" | "manual" | "recurring" | "debt" | "estimate"; // estimate: ekstresi olmayan kart ödemesi
  id: number;
  date: string;
  month: string;
  description: string;
  merchant: string;
  amount: number;
  kind: Kind;
  category: string;
  card: string | null;
  account: string | null;
  bank: string | null;
  installment: string | null;
  installments?: number; // elle girilen kart harcamasının taksit sayısı
  card_last4?: string | null; // hangi kredi kartı (son 4 hane)
  hidden?: boolean; // ortak defterde üyeye gizli
  created_by?: number | null; // ekleyen kullanıcı (ortak defter)
  fx: string | null;
  note: string | null;
  /** sunucu 90ed954: ham banka açıklaması (yoksa null) ve kullanıcının kendi notu; note ikisinin birleşik hâli */
  raw?: string | null;
  note_user?: string | null;
  deleted: boolean;
  recurring_id?: number | null;
  skipped?: boolean;
  pending?: boolean;
  payment?: Payment | null;
  linked?: boolean;
  linked_txn_id?: number | null;
  awaiting_statement?: boolean;
  template_amount?: number;
  subscription?: "aktif" | "iptal" | "dusuruldu" | null;
  counterparty?: string | null;
  flow?: number; // borç hareketi: +1 para geldi, −1 para çıktı
  counts_as_spend?: boolean; // sunucu (a18ac2a): gider toplamına girer mi — istemci kuralı kendisi kurmaz (lib/counts)
  counts_as_income?: boolean;
  split_of?: string; // alt kalemse ana satırın uid'i
  split_total?: number; // ana satırdan ayrılan toplam
  original_amount?: number;
  location?: string | null; // fişteki adres
  receipt_file?: string | null; // saklı fiş fotoğrafı (GET /api/entries/{id}/receipt)
  bank_doc_id?: number | null; // hesap dökümünden geldi
  wallet_id?: number | null; // ön ödemeli kart
  account_id?: number | null; // banka/nakit hesabı
  debt_id?: number | null;
  match?: EntryMatch | null; // elle girilen kayıt dökümle/ekstreyle eşleşti (app/sync.py)
  /** TAHMİNİ satır (ekstresiz kart ödemesi / dökümsüz maaş): gerçek değil, hiçbir toplama girmez.
   *  Sunucu varsayılan listede vermez; /api/transactions?include_estimates=1 ile gelir. */
  estimated?: boolean;
  estimate_kind?: "card_payment" | "recurring";
  expected_period?: string | null; // beklenen ekstrenin dönemi (YYYY-MM)
}

/** Özet/yıllık özetteki tahmini kalem: gerçek toplamlara girmez, ayrı gösterilir. */
export interface EstimateItem {
  uid: string;
  source: "card_payment" | "recurring";
  kind: "gider" | "gelir";
  title: string;
  amount: number;
  date: string;
  month: string;
  card: string | null;
  card_last4: string | null;
  bank: string | null;
  expected_period: string | null;
  recurring_id: number | null;
}

/** Elle girilen kaydın bankadaki karşılığı: "Dökümle eşleşti · Garanti · 28 Eki". */
export interface EntryMatch {
  source: "dokum" | "ekstre";
  how: "otomatik" | "onay" | "kural" | "elle";
  date: string;
  description: string;
  amount: number;
  balance: number | null;
  account: string | null;
  can_undo: boolean;
}

/** Öğrenilen eşleşme: dökümdeki kalıp ↔ elle yazılan açıklama (iki onaydan sonra kendiliğinden birleşir). */
export interface MatchRule {
  id: number;
  source: "dokum" | "ekstre";
  pattern: string;
  entry_key: string;
  bank_label: string;
  entry_label: string;
  category: string | null;
  hits: number;
  used: number;
  created_at: string;
  updated_at: string;
}

export interface MatchResult {
  ok: boolean;
  same: boolean;
  entry_id?: number;
  undo_id?: string;
  learned?: { id: number; bank: string; entry: string };
  auto_merged?: number;
}

export interface AccountMergeResult {
  ok: boolean;
  into: number;
  moved: Record<string, number>; // tablo → taşınan satır (entries, recurring, ...)
  matched: number;
  balance_from: "merged" | "kept"; // merged: birleşen hesabın bakiyesi daha yeniydi, o alındı
  undo_id: string;
}

export const movedCount = (m: AccountMergeResult["moved"]) => Object.values(m ?? {}).reduce((a, b) => a + b, 0);

export type Payment = "kredi_karti" | "nakit" | "havale" | "kart"; // kart: ön ödemeli (yemek/hediye/giyim)

export interface LinkCandidate {
  id: number;
  txn_date: string;
  description: string;
  amount: number;
  account: string;
  days: number;
  diff: number;
}

export interface Budget {
  category: string;
  limit: number;
  spent: number;
  remaining: number;
  pct: number;
}

export interface Group {
  name: string;
  amount: number;
  count: number;
}

export interface Insight {
  level: "kritik" | "uyari" | "bilgi" | "iyi";
  title: string;
  detail: string;
  filter?: { kind?: Kind; category?: string; fx?: boolean; pending?: boolean; awaiting?: boolean };
  link?: string;
  /** gecikmiş / kısmi ödenmiş kalem: "Ödedim" düğmesi bu ödemeyi açar (/api/payables içinde type + id) */
  pay?: { type: "statement" | "bill" | "debt"; id: number };
}

export interface Statement {
  id: number;
  filename: string;
  bank: string;
  card_no: string;
  period: string;
  period_end: string;
  due_date: string | null;
  total_debt: number;
  min_payment: number | null;
  credit_limit: number | null;
  interest_fees: number;
  /** faiz ve ücret: özet satırı okunmadıysa faiz/ücret işlemlerinin toplamı (sunucu hesaplar) */
  fees?: number;
  /** okunan işlemler − ekstre özeti, faiz ve ücret dahil (0 = tuttu) */
  reconciliation_diff?: number | null;
  txn_count: number;
  previous_balance?: number | null;
  payments?: number | null;
  period_activity?: number | null;
  paid_amount?: number | null;
  paid_at?: string | null;
  /** kullanıcı "rakamlar doğru" dedi (POST /api/statements/{id}/confirm) */
  confirmed_at?: string | null;
  /** ödemenin bağlı olduğu banka/nakit hesabı ve bağın kaynağı (elle seçildi / dökümden geldi) */
  paid_account_id?: number | null;
  paid_link?: "elle" | "dokum" | null;
  /** okunan işlemler ya da denklem ekstre özetiyle tutmuyor ve onay yok (sunucu) */
  needs_check?: boolean;
  /** faiz ve ücret: true harcamaların içinde, false ayrı satır, null faiz yok / hesaplanamaz (sunucu) */
  fees_included?: boolean | null;
  /** denklem farkı: yeni borç − (önceki − ödemeler + harcamalar [+ faiz]); null hesaplanamaz */
  equation_diff?: number | null;
}

export interface Summary {
  month: string;
  totals: {
    income: number;
    card_spend: number;
    fees: number;
    manual_expense: number;
    refunds: number;
    expense: number;
    net: number;
    carry_in: number; // önceki dönemlerden devreden artı
    debt_flow: number; // borç verip almanın eldeki paraya etkisi
    savings: number; // kumbaraya altın/döviz alımı: gider değil, eldeki paradan çıkar
    cash_net: number; // net + borç hareketleri
    receivable: number; // açık TL alacak
    payable: number; // açık TL borç
    balance: number; // devreden + net
    estimated_expense?: number; // tahmini (ekstre bekleniyor): expense'e dahil DEĞİL
    estimated_income?: number; // tahmini maaş (döküm yok): income'a dahil DEĞİL
    // sunucu a18ac2a (S1): istemci hesaplamaz; eski sunucuda yok
    left?: number; // Artan: gelir − gider
    left_after_savings?: number; // Elde kalan (sade): gelir − gider − birikim
    card_pending?: number;
    card_spend_incl_pending?: number;
    manual_expense_excl_card_pending?: number;
    fees_hidden?: boolean; // ortak defterde faiz gizli: Faiz kutusu gösterilmez
  };
  /** rakam tanımları (i) için: expense, income, left, left_after_savings, cash_net */
  definitions?: Partial<Record<"expense" | "income" | "left" | "left_after_savings" | "cash_net", string>>;
  estimated_items?: EstimateItem[];
  pending: { income: number; expense: number; count: number };
  budgets: Budget[];
  by_category: Group[];
  by_merchant: Group[];
  by_card: Group[];
  statements: Statement[];
  trend: { month: string; income: number; expense: number; net: number; estimated_expense?: number; estimated_income?: number }[];
  insights: Insight[];
}

export type PlanLineType = "kart_gercek" | "taksit" | "abonelik" | "degisken" | "sabit" | "tek_seferlik" | "fatura" | "tasarruf_notu";

/** Kesilmemiş ekstre: bugüne kadar biriken ve kesimde tahmini (kart başına). */
export interface CardCycle {
  account: string;
  cut: string;
  due: string | null;
  days_left: number;
  installments: number;
  subscriptions: number;
  spent: number;
  unassigned: number;
  entries: number;
  so_far: number;
  at_cut: number;
}

export interface Payable {
  type: "statement" | "bill" | "estimate" | "debt";
  id: number | null;
  title: string;
  subtitle: string;
  amount: number;
  min_payment: number | null;
  due_date: string;
  days_left: number;
  paid_amount: number | null;
  paid_at: string | null;
  status: "bekliyor" | "gecikti" | "kismi" | "odendi" | "tahmini";
  has_file?: boolean;
  category?: string | null;
  note?: string | null;
  description?: string | null;
  cycle?: CardCycle | null; // tahmini ekstre: dökümü
  /** toplama girer mi (sunucu); false: ödenmemiş eski ekstre, kalanı rolled_into'daki son ekstreye devretti */
  in_total?: boolean;
  rolled_into?: number | null;
}

/** GET /api/payables?totals=1 toplamları: istemci toplamaz (finance.payables_totals). */
export interface PayableTotals {
  open: number; open_count: number; card_debt: number; statements: number; bills: number; debts: number;
  overdue: number; overdue_count: number; estimated: number;
  window?: { days: number; total: number; count: number; overdue: number; estimated: number } | null;
}

export interface BillDraft {
  id?: number;
  provider?: string;
  description?: string | null;
  amount?: number | null;
  due_date?: string | null;
  category?: string | null;
  note?: string | null;
  file_name?: string | null;
  confidence?: string;
  error?: string;
}

export interface Bill {
  id: number;
  provider: string;
  description?: string | null;
  amount: number;
  due_date: string;
  category?: string | null;
  note?: string | null;
  file_name?: string | null;
  paid_at?: string | null;
}

export interface MailLogItem {
  message_id: string;
  received_at: string;
  sender: string;
  subject: string;
  kind: string;
  status: "ok" | "skip" | "error";
  detail: string;
  processed_at?: string;
}

/** Defterin e-posta adresi (hazır sürüm, Premium/Aile). locked: paketinde yok. */
export type MailAddress =
  | { locked: true; address: null }
  | { locked: false; address: string; senders: string[]; log: MailLogItem[] };

export interface PlanLine {
  type: PlanLineType;
  kind: "gelir" | "gider" | "bilgi";
  amount: number;
  description: string;
  account?: string;
  recurring_id?: number;
  entry_id?: number;
  confirmed?: boolean;
  custom?: boolean;
  template_amount?: number;
  month?: string;
  due_date?: string;
}

export interface PlanMonth {
  month: string;
  income: number;
  expense: number; // kesin giderler
  estimated: number; // tahmini kart harcaması (bilgi amaçlı)
  net: number; // gelir - kesin gider
  net_with_estimate: number;
  cumulative: number;
  cumulative_with_estimate: number;
  groups: Partial<Record<PlanLineType | "gelir", number>>;
  lines: PlanLine[];
}

export interface Recurring {
  id: number;
  description: string;
  amount: number;
  kind: "gelir" | "gider";
  category: string | null;
  day_of_month: number;
  start_month: string;
  end_month: string | null;
  note: string | null;
  advance_pay?: number; // maaş peşin (memur)
  gross_amount?: number | null;
  account_id?: number | null;
  amount_now?: number; // bu ayki tutar (tutar değişikliği / brütten net sonrası)
}

export interface Plan {
  months: PlanMonth[];
  dues: { date: string; account: string; bank: string; amount: number | null; min_payment: number | null; estimated: boolean }[];
  recurring: Recurring[];
}

export interface Meta {
  months: string[];
  /** şimdiki dönemin sınırları (sunucu a18ac2a); istemci dönem sınırını kendisi hesaplamasın */
  period?: { month: string; start: string; end: string };
  today: string;
  categories: string[];
  income_categories: string[];
  receipt_enabled: boolean;
  receipt_engine?: "gemini" | "yerel" | null; // yerel: anahtarsız Tesseract okuma
  current_month: string;
  cycle_start_day: number;
  cycle_mode: "auto" | "manual";
  suggested_cycle_day: number | null;
  modules?: Record<string, boolean>; // açık özellikler (defter bazında)
  modules_chosen?: boolean;
  module_labels?: Record<string, { label: string; desc: string }>;
  module_consent?: Record<string, string>; // hazır sürüm: açarken açık rıza isteyen özellikler (sade pencerede gösterilen metin)
  module_consent_title?: Record<string, string | null>; // o pencerenin başlığı ("Zekât hesaplamayı açalım mı?")
  ui?: { simple: boolean; layout: string[] | null }; // kişisel görünüm
  plan?: PlanInfo; // paket (barındırılan sürüm); kendi sunucunda "self": her şey açık
}

export interface PlanInfo {
  plan: "self" | "free" | "premium" | "aile";
  label: string;
  hosted: boolean;
  until: string | null;
  features: Record<string, boolean>;
  feature_labels: Record<string, string>;
  ai_docs: number | null; // hazır sürüm: ayda belge okuma hakkı (50 / 1000); null: kendi sunucusu (kota /api/me/ai)
  member: boolean;
}

export interface Rule {
  id: number;
  pattern: string;
  category: string;
  priority: number;
}

export type DocKind = "card" | "account" | "dekont";

export interface ImportBatch {
  id: string;
  created_at: string;
  label: string;
  undone_at: string | null;
  counts: Record<string, { added: number; changed: number; removed: number }>;
}

export interface UploadResult {
  filename: string;
  status: "saved" | "duplicate" | "error" | "password_required" | "account_statement" | "ask";
  guess?: DocKind; // ask: türünden emin değil, tahmin
  message?: string;
  bank?: string;
  card_no?: string;
  period_end?: string;
  total_debt?: number;
  txn_count?: number;
  reconciliation_diff?: number;
  linked?: number;
  ai?: boolean;
  cycle_day?: number;
  dekont?: boolean; // tek işlem dekontu: hesaba eklendi, bakiye değişmedi
  card_excel?: { statements: number; transactions: number; range: [string, string] | null; cut_day: number }; // kart işlem Excel'i
  manual?: boolean; quota_full?: boolean; limit?: number; upgrade?: boolean; // hazır sürüm: belge okuma hakkı doldu (okunmadı)
}

export { ApiError };
export const errStatus = (e: unknown) => (e instanceof ApiError ? e.status : 0);

function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  return call<T>(method, url, body);
}

const q = (params: Record<string, string | number | boolean | undefined>) =>
  "?" +
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => [k, String(v)]),
  ).toString();

export const api = {
  meta: () => req<Meta>("GET", "/api/meta"),
  summary: (month: string) => req<Summary>("GET", `/api/summary${q({ month })}`),
  transactions: (month?: string, includeDeleted = false, includeEstimates = false) =>
    req<Row[]>("GET", `/api/transactions${q({ month, include_deleted: includeDeleted, include_estimates: includeEstimates || undefined })}`),
  plan: (months: number) => req<Plan>("GET", `/api/plan${q({ months })}`),

  patchCard: (id: number, body: Record<string, unknown>) =>
    req<{ ok: true; rule: string | null }>("PATCH", `/api/card-transactions/${id}`, body),

  createEntry: (body: Record<string, unknown>) =>
    req<{ id: number; linked: boolean; debt_settled: number | null; splits?: { category: string; amount: number }[] }>("POST", "/api/entries", body),
  linkCandidates: (id: number) => req<LinkCandidate[]>("GET", `/api/entries/${id}/candidates`),
  linkEntry: (id: number, txnId: number | null) => req("POST", `/api/entries/${id}/link`, { txn_id: txnId }),
  patchEntry: (id: number, body: Record<string, unknown>) => req("PATCH", `/api/entries/${id}`, body),
  deleteEntry: (id: number) => req("DELETE", `/api/entries/${id}`),

  recurring: () => req<Recurring[]>("GET", "/api/recurring"),
  createRecurring: (body: Record<string, unknown>) => req<{ id: number }>("POST", "/api/recurring", body),
  patchRecurring: (id: number, body: Record<string, unknown>) => req("PATCH", `/api/recurring/${id}`, body),
  deleteRecurring: (id: number) => req("DELETE", `/api/recurring/${id}`),
  confirmRecurring: (id: number, month: string, amount?: number) =>
    req("POST", `/api/recurring/${id}/confirm`, { month, amount }),
  skipRecurring: (id: number, month: string) => req("POST", `/api/recurring/${id}/skip`, { month }),

  payables: () => req<Payable[]>("GET", "/api/payables"),
  /** days: başlık toplamı penceresi (totals.window: vadesi N gün içinde ya da geçmiş) — web ve mobil aynı tanım */
  payablesWithTotals: (days = 15) => req<{ items: Payable[]; totals: PayableTotals }>("GET", `/api/payables?totals=1&days=${days}`),
  budgets: (month: string) =>
    req<{ limits: Record<string, number>; status: Budget[]; last_month: Record<string, number> }>("GET", `/api/budgets?month=${month}`),
  setBudget: (category: string, monthly_limit: number | null) => req("PUT", "/api/budgets", { category, monthly_limit }),
  /** account_id (banka/nakit, isteğe bağlı): o hesaptan çıkan kart ödemesi kaydı (gider değil, bakiye düşer);
   *  linked: dökümdeki aynı ödemeye bağlandı. */
  // account_id: undefined → anahtar gönderilmez (bağlı hesap korunur, tutar/tarih güncellenir); null → hesap kaydı kaldırılır
  payStatement: (id: number, amount: number, paid_at: string, account_id?: number | null) =>
    req<{ ok: boolean; card_payment_id: number | null; linked: boolean; link?: "elle" | "dokum" | null; warning?: string }>(
      "POST", `/api/statements/${id}/pay`, { amount, paid_at, ...(account_id !== undefined ? { account_id } : {}) }),
  unpayStatement: (id: number) => req("DELETE", `/api/statements/${id}/pay`),
  /** payment_id (sunucu a211704): bu ödemeyi tek başına geri almak için → undoDebtPayment */
  payDebt: (id: number, amount: number | null, paid_at: string, account_id?: number | null) =>
    req<{ ok: boolean; payment_id?: number }>("POST", `/api/debts/${id}/pay`, { amount, paid_at, account_id: account_id ?? null }),
  unpayDebt: (id: number) => req("DELETE", `/api/debts/${id}/pay`),
  /** tek borç ödemesini geri al (öncekiler kalır); idempotent */
  undoDebtPayment: (pid: number) => req<{ ok: boolean; deleted: boolean }>("DELETE", `/api/debt-payments/${pid}`),
  people: () => req<{ people: { name: string }[] }>("GET", "/api/transfers").then((t) => t.people.map((p) => p.name)),
  parseBill: (file: File | Blob, name: string) => {
    const fd = new FormData();
    fd.append("file", file, name);
    return req<BillDraft>("POST", "/api/bills/parse", fd);
  },
  createBill: (body: Record<string, unknown>) => req<{ id: number }>("POST", "/api/bills", body),
  patchBill: (id: number, body: Record<string, unknown>) => req("PATCH", `/api/bills/${id}`, body),
  deleteBill: (id: number) => req("DELETE", `/api/bills/${id}`),
  payBill: (id: number, amount: number, paid_at: string, payment: Payment) =>
    req("POST", `/api/bills/${id}/pay`, { amount, paid_at, payment }),
  unpayBill: (id: number) => req("DELETE", `/api/bills/${id}/pay`),
  bills: () => req<Bill[]>("GET", "/api/bills"),
  mailAddress: () => req<MailAddress>("GET", "/api/mail/address"),
  regenerateMailAddress: () => req<{ address: string }>("POST", "/api/mail/address/regenerate"),
  setMailSenders: (senders: string[]) => req<{ senders: string[] }>("PUT", "/api/mail/senders", { senders }),

  statements: () => req<Statement[]>("GET", "/api/statements"),
  upload: (files: File[], password?: string, remember?: boolean, kind?: DocKind) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    if (password) fd.append("password", password);
    if (remember) fd.append("remember", "true");
    if (kind) fd.append("kind", kind);
    return req<UploadResult[]>("POST", "/api/statements/upload", fd);
  },
  imports: () => req<ImportBatch[]>("GET", "/api/imports"),
  undoImport: (id: string, force = false) =>
    req<{ ok: boolean; conflicts?: string[]; already?: boolean }>("POST", `/api/imports/${id}/undo${force ? "?force=true" : ""}`),
  deleteStatement: (id: number) => req("DELETE", `/api/statements/${id}`),
  confirmStatement: (id: number) => req<{ ok: boolean; confirmed_at: string | null }>("POST", `/api/statements/${id}/confirm`),
  unconfirmStatement: (id: number) => req<{ ok: boolean }>("DELETE", `/api/statements/${id}/confirm`),

  rules: () => req<Rule[]>("GET", "/api/rules"),
  createRule: (body: Omit<Rule, "id">) => req("POST", "/api/rules", body),
  patchRule: (id: number, body: Partial<Rule>) => req("PATCH", `/api/rules/${id}`, body),
  deleteRule: (id: number) => req("DELETE", `/api/rules/${id}`),
  // eşitleme: elle girilen ↔ döküm / ekstre
  matchEntry: (id: number, other: string, same: boolean) => req<MatchResult>("POST", `/api/entries/${id}/match`, { other, same }),
  unmatchEntry: (id: number) =>
    req<{ ok: boolean; source: string; other_id?: number; txn_id?: number; undo_id: string }>("POST", `/api/entries/${id}/unmatch`),
  matchRules: () => req<MatchRule[]>("GET", "/api/match-rules"),
  deleteMatchRule: (id: number) => req("DELETE", `/api/match-rules/${id}`),
  mergeAccount: (id: number, into: number) => req<AccountMergeResult>("POST", `/api/accounts/${id}/merge`, { into }),
  separateAccount: (id: number, other: number) => req<{ ok: boolean }>("POST", `/api/accounts/${id}/separate`, { other }),
  // fiş kalemleri (app/receipt_check.py): kayıtlı giderin kalemleri, yeniden okuma (kaydedilmez)
  receiptItems: (id: number) => req<{ entry_id: number; items: ReceiptItem[]; check: ReceiptCheck }>("GET", `/api/entries/${id}/receipt-items`),
  saveReceiptItems: (id: number, items: ReceiptItem[]) =>
    req<{ saved: number; entry_id: number; items: ReceiptItem[]; check: ReceiptCheck }>("PUT", `/api/entries/${id}/receipt-items`, { items: toServer(items) }),
  rereadEntryReceipt: (id: number) => req<Reread>("POST", `/api/entries/${id}/receipt-reread`),
  rereadReceipt: (receipt_file: string, total?: number | null) => req<Reread>("POST", "/api/receipts/reread", { receipt_file, total: total ?? null }),
  testRule: (pattern: string, text: string) =>
    req<{ normalized: string; match: boolean }>("GET", `/api/rules/test${q({ pattern, text })}`),
};
