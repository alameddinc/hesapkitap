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
  split_of?: string; // alt kalemse ana satırın uid'i
  split_total?: number; // ana satırdan ayrılan toplam
  original_amount?: number;
  location?: string | null; // fişteki adres
  wallet_id?: number | null; // ön ödemeli kart
  account_id?: number | null; // banka/nakit hesabı
  debt_id?: number | null;
}

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
  txn_count: number;
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
    cash_net: number; // net + borç hareketleri
    receivable: number; // açık TL alacak
    payable: number; // açık TL borç
    balance: number; // devreden + net
  };
  pending: { income: number; expense: number; count: number };
  budgets: Budget[];
  by_category: Group[];
  by_merchant: Group[];
  by_card: Group[];
  statements: Statement[];
  trend: { month: string; income: number; expense: number; net: number }[];
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
  today: string;
  categories: string[];
  income_categories: string[];
  receipt_enabled: boolean;
  receipt_engine?: "gemini" | "yerel" | null; // yerel: anahtarsız Tesseract okuma
  current_month: string;
  cycle_start_day: number;
  cycle_mode: "auto" | "manual";
  suggested_cycle_day: number | null;
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
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* gövde JSON değil */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
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
  transactions: (month?: string, includeDeleted = false) =>
    req<Row[]>("GET", `/api/transactions${q({ month, include_deleted: includeDeleted })}`),
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
  budgets: (month: string) =>
    req<{ limits: Record<string, number>; status: Budget[]; last_month: Record<string, number> }>("GET", `/api/budgets?month=${month}`),
  setBudget: (category: string, monthly_limit: number | null) => req("PUT", "/api/budgets", { category, monthly_limit }),
  payStatement: (id: number, amount: number, paid_at: string) => req("POST", `/api/statements/${id}/pay`, { amount, paid_at }),
  unpayStatement: (id: number) => req("DELETE", `/api/statements/${id}/pay`),
  payDebt: (id: number, amount: number | null, paid_at: string) => req("POST", `/api/debts/${id}/pay`, { amount, paid_at }),
  unpayDebt: (id: number) => req("DELETE", `/api/debts/${id}/pay`),
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

  rules: () => req<Rule[]>("GET", "/api/rules"),
  createRule: (body: Omit<Rule, "id">) => req("POST", "/api/rules", body),
  patchRule: (id: number, body: Partial<Rule>) => req("PATCH", `/api/rules/${id}`, body),
  deleteRule: (id: number) => req("DELETE", `/api/rules/${id}`),
  testRule: (pattern: string, text: string) =>
    req<{ normalized: string; match: boolean }>("GET", `/api/rules/test${q({ pattern, text })}`),
};
