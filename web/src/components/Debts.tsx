import { Check, Handshake, Plus, RotateCcw, Trash2 } from "lucide-react";
import { api } from "../api";
import { ask } from "../lib/ask";
import { call } from "../lib/apiError";
import { useState } from "react";
import { cx, dateTR, maskText, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import AccountSelect, { useAccounts } from "./AccountSelect";
import CategoryPicker from "./CategoryPicker";
import { useCap, useSave } from "./hooks";
import Segmented from "./Segmented";
import type { DebtSchedule } from "../lib/debtPlan";
import { ALACAKLI, KOSULLAR, TARIH_TURU, kosulGovdesi, kosulOzeti, varsayilanKesin, type CreditorType, type PayTerms } from "../lib/borcKosul";
import DebtScheduleSection, { PayoffFields, ScheduleForm, payoffBody } from "./DebtScheduleSection";

export interface Debt {
  id: number;
  person: string;
  direction: "borc" | "alacak";
  asset: string;
  amount: number;
  paid_amount: number;
  remaining: number;
  debt_date: string;
  due_date: string | null;
  note: string | null;
  settled_at: string | null;
  written_off_at: string | null;
  account_id: number | null;
  payments: { id: number; amount: number; paid_at: string; entry_id: number | null; account_id: number | null }[];
  /** ödeme planı olan borç (yetenek debt_schedule): kayıtlı taksitler; diğer borçta null (eski sunucuda yok) */
  schedule?: DebtSchedule | null;
  plan_installment?: number | null; plan_count?: number | null; plan_start?: string | null; plan_day?: number | null;
  creditor_type?: CreditorType | null; plan_interest?: DebtSchedule["interest"];
  /** erken kapatma (yetenek debt_payoff): girilen bugün kapatma tutarı, öğrenildiği gün, aylık oran (yüzde) */
  payoff_amount?: number | null; payoff_date?: string | null; plan_rate?: number | null;
  /** borcun koşulları (yetenek debt_terms; alacakta ve eski sunucuda null/yok): kayıtlı değerler ve geçerli olanlar */
  pay_terms?: PayTerms | null; due_strict?: boolean | null;
  terms?: PayTerms | null; terms_label?: string | null; strict?: boolean | null; creditor_label?: string | null;
}

/** Ödeme planı olan borçta "Ödedim"in varsayılanı: sıradaki taksit (geçmişte eksik kalan dahil), bütün borç değil. */
export const dueInstallment = (d: Debt) => (d.schedule?.next ? Math.min(d.remaining, Math.max(d.schedule.next.left, d.schedule.due_now)) : d.remaining);

// Birimler: TL, döviz, altın; listede yoksa serbest yazılır ("22 ayar bilezik")
export const ASSETS: { value: string; label: string }[] = [
  { value: "TRY", label: "TL" }, { value: "USD", label: "Dolar" }, { value: "EUR", label: "Euro" },
  { value: "GBP", label: "Sterlin" }, { value: "GRAM_ALTIN", label: "gram altın" },
  { value: "CEYREK_ALTIN", label: "çeyrek altın" }, { value: "YARIM_ALTIN", label: "yarım altın" },
  { value: "TAM_ALTIN", label: "tam altın" }, { value: "CUMHURIYET_ALTIN", label: "Cumhuriyet altını" },
];
const assetLabel = (a: string) => ASSETS.find((x) => x.value === a)?.label ?? a;

/** 1.5 çeyrek altın / ₺200,00 — TL dışındaki birimler gizlilik modunda da maskelenir */
export function fmtAsset(x: number, asset: string) {
  if (asset === "TRY") return tl(x);
  const n = x.toLocaleString("tr-TR", { maximumFractionDigits: 3 });
  return maskText(`${n} ${assetLabel(asset)}`);
}

const json = (method: string, url: string, body?: unknown) => call<any>(method, url, body);
const today = () => new Date().toISOString().slice(0, 10);

/** Borç / alacak defteri: gelir-gider toplamlarını etkilemez; kim kime ne (TL, döviz, altın) borçlu. */
export default function Debts({ debts, people }: { debts: Debt[]; people: string[] }) {
  const ui = useUI();
  const [adding, setAdding] = useState(false);
  const [showClosed, setShowClosed] = useState(false);
  const [paying, setPaying] = useState<Debt | null>(null);
  const open = debts.filter((d) => !d.settled_at);
  const closed = debts.filter((d) => d.settled_at);

  const del = useSave((id: number) => json("DELETE", `/api/debts/${id}`), () => ui.toast("Kayıt silindi"));
  const unpay = useSave((id: number) => json("DELETE", `/api/debts/${id}/pay`), () => ui.toast("Yeniden açıldı"));
  // geri al = DELETE /pay (tüm ödemeler + vazgeçme silinir): yalnız önceden ödeme yoksa önceki duruma birebir döner
  const reopen = (d: Debt) => () => json("DELETE", `/api/debts/${d.id}/pay`);
  const writeOff = useSave((d: Debt) => json("POST", `/api/debts/${d.id}/write-off`, { on: today() }),
    (_r, d) => ui.toast("Kapatıldı; kalan bugünün gider/gelirine yazıldı", d.paid_amount > 0 ? undefined : { undo: reopen(d) }));

  const accounts = useAccounts();
  const accName = (id: number | null) => accounts.data?.accounts.find((a) => a.id === id)?.name;
  const setAcc = useSave(({ url, id }: { url: string; id: number | null }) => json("PATCH", url, { account_id: id }),
    () => ui.toast("Hesap kaydedildi"));
  const [openId, setOpenId] = useState<number | null>(null);

  const accountsOf = (d: Debt) => (
    <div className="mt-1 space-y-1 rounded-md bg-stone-50 p-2 text-xs dark:bg-stone-800/50">
      {d.payments.filter((p) => !p.entry_id).map((p) => (
        <label key={p.id} className="flex items-center gap-2">
          <span className="w-28 shrink-0 text-stone-500">{dateTR(p.paid_at)} · {fmtAsset(p.amount, d.asset)}</span>
          <select className="input py-1" value={p.account_id ?? ""} onChange={(e) => setAcc.mutate({ url: `/api/debt-payments/${p.id}`, id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">Nakit / belirtme</option>
            {accounts.data?.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
      ))}
      {d.payments.some((p) => p.entry_id) && <p className="text-stone-500">Havaleyle bağlanan ödemelerin hesabı o kayıttan gelir.</p>}
    </div>
  );

  const row = (d: Debt) => {
    const late = !d.settled_at && d.due_date && d.due_date < today();
    return (
      <li key={d.id} className={cx("py-2", d.settled_at && "opacity-60")}>
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1 cursor-pointer" role="button" tabIndex={0} aria-expanded={openId === d.id}
          aria-label={`${d.person} ${d.direction === "borc" ? "borcu" : "alacağı"}: düzenle`}
          onClick={() => setOpenId(openId === d.id ? null : d.id)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpenId(openId === d.id ? null : d.id); } }}>
          <div className="text-sm">
            <b>{maskText(d.person)}</b>{" "}
            <span className={cx("rounded-full px-1.5 py-0.5 text-xs whitespace-nowrap",
              d.direction === "borc" ? "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
                : "bg-green-50 text-green-800 dark:bg-green-950/40 dark:text-green-300")}>
              {d.direction === "borc" ? "borcum" : "alacağım"}
            </span>
          </div>
          {d.direction === "borc" && !d.settled_at && !d.schedule && kosulOzeti(d) && (
            <div className="text-xs text-stone-600 dark:text-stone-300" data-testid="borc-kosul-ozet">{kosulOzeti(d)}</div>
          )}
          {d.schedule && !d.settled_at && (
            <div className="text-xs text-stone-600 dark:text-stone-300" data-testid="borc-taksit">
              Ödeme planı · {d.schedule.creditor_label} · {d.schedule.paid_count}/{d.schedule.count} taksit ödendi
              {d.schedule.next && <span className={cx(d.schedule.overdue && "font-medium text-red-600 dark:text-red-400")}>
                {" "}· sıradaki {dateTR(d.schedule.next.date)} {fmtAsset(dueInstallment(d), "TRY")}</span>}
            </div>
          )}
          <div className="text-xs text-stone-500">
            {d.schedule ? maskText(d.schedule.text) : dateTR(d.debt_date)}
            {d.due_date && <span className={cx(late && "font-medium text-red-600")}> · {d.direction === "borc" && d.strict === false ? "söz" : "vade"} {dateTR(d.due_date)}</span>}
            {d.paid_amount > 0 && !d.settled_at && ` · ${fmtAsset(d.paid_amount, d.asset)} ödendi`}
            {d.settled_at && (d.written_off_at ? ` · vazgeçildi ${dateTR(d.written_off_at)}` : ` · kapandı ${dateTR(d.settled_at)}`)}
            {d.note && ` · ${d.note}`}
            {d.asset === "TRY" && !d.schedule && (accName(d.account_id) ? ` · ${accName(d.account_id)}` : " · hesap?")}
          </div>
        </div>
        <span className="shrink-0 text-right text-sm font-semibold tabular-nums">{fmtAsset(d.settled_at ? d.amount : d.remaining, d.asset)}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center justify-end gap-2">
        {d.settled_at ? (
          <button className="btn-ghost px-2 py-1 text-xs text-stone-500" disabled={unpay.isPending}
            onClick={() => void ask(`${d.person} kaydı yeniden açılsın mı?\n\n${d.written_off_at ? "Kapatma geri alınır" : "Yazılan ödemeler silinir"}; bağlı havaleler yeniden normal gelir/gider sayılır.`,
              { confirm: "Yeniden aç", danger: false }).then((ok) => { if (ok) unpay.mutate(d.id); })}>
            <RotateCcw size={14} aria-hidden /> Yeniden aç</button>
        ) : (
          <button className="btn-primary px-2 py-1 text-xs" onClick={() => setPaying(d)}>
            <Check size={14} /> {d.direction === "borc" ? "Ödedim" : "Aldım"}
          </button>
        )}
        {!d.settled_at && (
          <button className="btn-ghost px-1.5 py-1 text-xs text-stone-500" disabled={writeOff.isPending}
            onClick={() => void ask(d.direction === "alacak"
              ? `${d.person} ödemeyecek mi? Kalan ${fmtAsset(d.remaining, d.asset)} bugün gider olarak yazılır.`
              : `${d.person} borcu sildi mi? Kalan ${fmtAsset(d.remaining, d.asset)} bugün gelir olarak yazılır.`, { confirm: "Evet, kapat", danger: true }).then((ok) => { if (ok) writeOff.mutate(d); })}>
            {d.direction === "alacak" ? "Alamayacağım (vazgeç)" : "Borcu sildi (affetti)"}
          </button>
        )}
      </div>
      {openId === d.id && (
        <div className="mt-2 space-y-2">
          <DebtForm people={people} debt={d} onDone={() => setOpenId(null)} />
          {/* ödeme planı borcun alt kaydı: sonradan eklenir, düzenlenir, kaldırılır */}
          <DebtScheduleSection d={d} />
          {d.payments.some((p) => !p.entry_id) && d.asset === "TRY" && (<>
            <div className="text-xs font-medium text-stone-500">Önceki ödemelerin hesabı</div>
            {accountsOf({ ...d, payments: d.payments })}
          </>)}
          {/* silme satırdaki günlük eylemlerden (Ödedim, Borcu sildi) ayrı: yalnız düzenleme açıkken */}
          <div className="flex justify-end border-t border-stone-100 pt-2 dark:border-stone-800">
            <button className="btn-ghost px-2 py-1 text-xs text-red-600 dark:text-red-400" disabled={del.isPending}
              onClick={() => void ask(`${d.person} kaydı silinsin mi?\n\nBağlı havaleler yeniden normal gelir/gider sayılır.`).then((ok) => { if (ok) { del.mutate(d.id); setOpenId(null); } })}>
              <Trash2 size={14} aria-hidden /> Kaydı sil
            </button>
          </div>
        </div>
      )}
      </li>
    );
  };

  return (
    <section className="card p-4">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold"><Handshake size={18} /> Borç / alacak</h2>
        {!adding && <button className="btn-ghost px-2 py-1 text-sm" onClick={() => setAdding(true)}><Plus size={16} /> Ekle</button>}
      </div>
      <p className="mb-2 text-xs text-stone-500">
        Havaleyle verip aldıysan banka dökümündeki o havaleler kendiliğinden bağlanır, gider/gelir sayılmaz. Döviz ve
        altında "Ödedim / Aldım" de. Vadesi gelince Telegram'dan hatırlatılır.
      </p>
      {adding && <DebtForm people={people} onDone={() => setAdding(false)} />}
      {paying && <PayDebt d={paying} onDone={() => setPaying(null)} />}
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">
        {open.map(row)}
        {!open.length && !adding && <li className="py-3 text-sm text-stone-500">Açık borç ya da alacak yok.</li>}
      </ul>
      {closed.length > 0 && (
        <>
          <button className="mt-2 text-xs text-stone-500 underline" onClick={() => setShowClosed(!showClosed)}>
            {showClosed ? "Kapananları gizle" : `Kapananlar (${closed.length})`}
          </button>
          {showClosed && <ul className="divide-y divide-stone-100 dark:divide-stone-800">{closed.map(row)}</ul>}
        </>
      )}
    </section>
  );
}

/** Seçim düğmeleri (dar ekranda alt alta kırılır; Segmented tek satırda kesilirdi). */
function Chips<T extends string>({ value, onChange, options, label, cols = "grid-cols-2 sm:grid-cols-4", testid }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; label: string; cols?: string; testid?: string }) {
  return (
    <div role="radiogroup" aria-label={label} className={cx("grid gap-1.5", cols)} data-testid={testid}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
          className={cx("rounded-lg border px-2 py-1.5 text-left text-xs font-medium leading-snug transition-colors pointer-coarse:min-h-11",
            value === o.value ? "border-blue-500 bg-blue-50 text-blue-900 dark:border-blue-500 dark:bg-blue-950/40 dark:text-blue-100"
              : "border-stone-200 text-stone-600 dark:border-stone-700 dark:text-stone-300")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Yeni borç/alacak ya da (debt verilirse) var olanı düzenle: PATCH /api/debts/{id}. Borçta (yetenek debt_terms) kime,
 *  nasıl ödenecek ve tarih türü; "Taksitli (ödeme planı)" seçilince plan alanları açılır (yeni borçta bu formda, var
 *  olan borçta Ödeme planı formu). Taksitliden başka koşula geçmek planı kaldırır (onay sorulur). borcOnly: Borç
 *  planı'ndan açılır (yön "borç aldım"). Mobil screens/BorcAlacak.tsx AddDebt aynı alanlar, aynı sözler. */
export function DebtForm({ people, onDone, debt, borcOnly }: { people: string[]; onDone: () => void; debt?: Debt; borcOnly?: boolean }) {
  const ui = useUI();
  const termsCap = useCap("debt_terms");
  const known = !debt || ASSETS.some((a) => a.value === debt.asset);
  const [direction, setDirection] = useState<Debt["direction"]>(borcOnly ? "borc" : debt?.direction ?? "borc");
  const [creditor, setCreditor] = useState<CreditorType>(debt?.creditor_type ?? (debt?.schedule ? "diger" : "kisi"));
  const [terms, setTerms] = useState<PayTerms>(debt?.terms ?? (debt?.schedule ? "taksitli" : "parca"));
  // null: kullanıcı değiştirmedi (sunucu varsayılanı: kurumda kesin, kişide söz); düzenlemede kayıtlı açık değer
  const [strict, setStrict] = useState<boolean | null>(debt?.due_strict ?? null);
  const [person, setPerson] = useState(debt?.person ?? "");
  const [asset, setAsset] = useState(debt ? (known ? debt.asset : "_") : "TRY");
  const [custom, setCustom] = useState(debt && !known ? debt.asset : "");
  const [amount, setAmount] = useState(debt ? String(debt.amount).replace(".", ",") : "");
  const [date, setDate] = useState(debt?.debt_date ?? today());
  const [due, setDue] = useState(debt?.due_date ?? "");
  const [note, setNote] = useState(debt?.note ?? "");
  const [accountId, setAccountId] = useState<number | null>(debt?.account_id ?? null);
  // yalnız yeni borçta: ödeme planlı oluştur (var olan borcun planı DebtScheduleSection'da)
  const [inst, setInst] = useState("");
  const [count, setCount] = useState("");
  const [endMonth, setEndMonth] = useState("");
  const [startMonth, setStartMonth] = useState(today().slice(0, 7));
  const [day, setDay] = useState(String(Number(today().slice(8, 10))));
  const [total, setTotal] = useState("");
  const [interest, setInterest] = useState<string>("");
  const payoffCap = useCap("debt_payoff");
  const [payoff, setPayoff] = useState("");
  const [rate, setRate] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const borc = direction === "borc";
  const kosulOn = borc && termsCap; // eski sunucu: yalnız eski alanlar
  const taksitli = borc && terms === "taksitli";
  const plan = !debt && taksitli;
  // var olan, planı olmayan borçta taksitli seçildi: plan aşağıda Ödeme planı formunda kaydedilir
  const planEkle = !!debt && taksitli && !debt.schedule;
  const effStrict = strict ?? varsayilanKesin(creditor, terms);
  const kosul = kosulOn ? kosulGovdesi({ creditor, terms, strict, hasDue: !!due }) : {};
  const save = useSave(async () => {
    if (plan) {
      const i = parseAmount(inst);
      const n = Number(count);
      const t = total.trim() ? parseAmount(total) : null;
      if (!person.trim()) throw new Error("Kime borçlu olduğunu yaz");
      if (!(i > 0)) throw new Error("Aylık taksit tutarını yaz");
      if (!(n >= 1) && !endMonth) throw new Error("Taksit sayısını ya da son taksit ayını yaz");
      const dd = Number(day);
      if (!(dd >= 1 && dd <= 31)) throw new Error("Ödeme günü 1 ile 31 arasında olmalı");
      const body = { person: person.trim(), direction: "borc", asset: "TRY", plan_installment: i,
        ...(n >= 1 ? { plan_count: Math.round(n) } : { plan_end: endMonth }), plan_start: startMonth, plan_day: dd,
        creditor_type: creditor, plan_interest: interest || null, note: note.trim() || null, ...(t != null && t > 0 ? { amount: t } : {}),
        ...(payoffCap && interest === "var" ? payoffBody(payoff, rate) : {}) };
      return json("POST", "/api/debts", body);
    }
    const a = parseAmount(amount);
    if (!person.trim() || !(a > 0)) throw new Error("Kişi ve miktar gerekli");
    const unit = asset === "_" ? custom.trim() : asset;
    if (!unit) throw new Error("Birim yaz (ör. 22 ayar bilezik)");
    if (debt && a + 1e-9 < debt.paid_amount) throw new Error(`Bu kayda ${fmtAsset(debt.paid_amount, debt.asset)} ödeme yapılmış; miktar bundan az olamaz`);
    // ödeme planı kalkıyor (taksitliden başka koşula): onay; borç silinmez, tutar ve ödemeler değişmez
    const planOff = !!debt?.schedule && kosulOn && !taksitli;
    if (planOff && !(await ask("Ödeme planı kaldırılsın mı?\n\nBorç silinmez: diğer borç olarak kalır; tutar ve ödemeler değişmez.",
      { confirm: "Planı kaldır", danger: false }))) throw new Error("Ödeme planı duruyor; koşulu değiştirmedin");
    // ödeme planlı borçta vade taksitlerden gelir: söz tarihi gönderilmez (plan aşağıdaki bölümde)
    const keepPlan = !!debt?.schedule && !planOff;
    const body = { person: person.trim(), direction, asset: unit, amount: a, debt_date: date,
      ...(keepPlan ? {} : { due_date: due || null }), note: note.trim() || null, account_id: unit === "TRY" ? accountId : null,
      ...(borc && !planEkle ? kosul : {}), ...(planOff ? { plan_off: true } : {}) };
    return debt ? json("PATCH", `/api/debts/${debt.id}`, body) : json("POST", "/api/debts", body);
  }, () => { ui.toast(debt ? "Güncellendi" : "Kaydedildi"); onDone(); });
  const c = ALACAKLI.find((x) => x.value === creditor) ?? ALACAKLI[0];
  const kosulNot = KOSULLAR.find((x) => x.value === terms)?.note;
  const tlOnly = asset === "TRY" || plan;

  return (
    <div className="mb-3 space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700" data-testid="borc-formu">
      {!borcOnly && <Segmented value={direction} onChange={setDirection} label="Yön" options={[
        { value: "borc", label: "Borç aldım", tone: "red" }, { value: "alacak", label: "Borç verdim", tone: "green" },
      ]} />}
      {kosulOn && (<>
        <div className="space-y-1">
          <span className="label">Kime</span>
          <Chips value={creditor} onChange={setCreditor} label="Kime" cols="grid-cols-2 sm:grid-cols-5" testid="borc-kime"
            options={ALACAKLI.map((x) => ({ value: x.value, label: x.label }))} />
        </div>
        <div className="space-y-1">
          <span className="label">Nasıl ödenecek</span>
          <Chips value={terms} onChange={setTerms} label="Nasıl ödenecek" testid="borc-kosul"
            options={KOSULLAR.filter((x) => x.value !== "taksitli" || tlOnly || !!debt?.schedule).map((x) => ({ value: x.value, label: x.label }))} />
          {kosulNot && <p className="text-xs text-stone-500 dark:text-stone-400" data-testid="borc-turu-not">{kosulNot}</p>}
        </div>
      </>)}
      {borc && !debt && !termsCap && (
        <p className="text-xs text-stone-500 dark:text-stone-400" data-testid="borc-turu-not">Diğer borç: ne zaman ödeyeceğini söz tarihi olarak yazabilirsin.</p>
      )}
      {plan ? (<>
        <div>
          <span className="label">Alacaklının adı</span>
          <CategoryPicker value={person} onChange={setPerson} options={people} placeholder={c.hint} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs">
            <span className="label">Aylık taksit</span>
            <input className="input text-right" inputMode="decimal" placeholder="ör. 2.000" value={inst} onChange={(e) => setInst(e.target.value)} />
          </label>
          <label className="text-xs">
            <span className="label">Taksit sayısı</span>
            <input className="input text-right" inputMode="numeric" placeholder="ör. 6" value={count} onChange={(e) => setCount(e.target.value.replace(/\D/g, ""))} />
          </label>
          <label className="col-span-2 text-xs sm:col-span-1">
            <span className="label">İlk taksit ayı</span>
            <input type="month" className="input" value={startMonth} onChange={(e) => setStartMonth(e.target.value)} />
          </label>
          <label className="col-span-2 text-xs sm:col-span-1">
            <span className="label">Ayın kaçında</span>
            <input className="input text-right" inputMode="numeric" value={day} onChange={(e) => setDay(e.target.value.replace(/\D/g, "").slice(0, 2))} />
          </label>
          {!count && (
            <label className="col-span-2 text-xs sm:col-span-1">
              <span className="label">ya da son taksit ayı</span>
              <input type="month" className="input" value={endMonth} onChange={(e) => setEndMonth(e.target.value)} />
            </label>
          )}
          <label className="col-span-2 text-xs sm:col-span-1">
            <span className="label">Toplam (opsiyonel)</span>
            <input className="input text-right" inputMode="decimal" placeholder="taksit × sayı" value={total} onChange={(e) => setTotal(e.target.value)} />
          </label>
        </div>
        <label className="block text-xs">
          <span className="label">Bu planın faizi / gecikme zammı var mı?</span>
          <select className="input" value={interest} onChange={(e) => setInterest(e.target.value)}>
            <option value="">Belirtme</option><option value="yok">Yok</option><option value="var">Var</option><option value="bilmiyorum">Bilmiyorum</option>
          </select>
        </label>
        {payoffCap && interest === "var" && <div className="grid grid-cols-2"><PayoffFields payoff={payoff} setPayoff={setPayoff} rate={rate} setRate={setRate} /></div>}
        <input className="input" placeholder="Not (opsiyonel)" value={note} onChange={(e) => setNote(e.target.value)} />
      </>) : planEkle ? (
        <ScheduleForm d={{ ...debt!, creditor_type: creditor }} creditor={creditor} onDone={onDone} />
      ) : (<>
      <div>
        <span className="label">{borc && kosulOn && creditor !== "kisi" ? "Alacaklının adı" : "Kişi"}</span>
        <CategoryPicker value={person} onChange={setPerson} options={people} placeholder={borc && kosulOn ? c.hint : "Seç veya yaz"} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs">
          <span className="label">Miktar</span>
          <input className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label className="text-xs">
          <span className="label">Birim</span>
          <select className="input" value={asset} onChange={(e) => setAsset(e.target.value)}>
            {ASSETS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            <option value="_">Diğer…</option>
          </select>
        </label>
      </div>
      {asset === "_" && (
        <input className="input" placeholder="ör. 22 ayar bilezik (gram)" value={custom} onChange={(e) => setCustom(e.target.value)} />
      )}
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs">
          <span className="label">Tarih</span>
          <input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        {!(debt?.schedule && taksitli) && (
          <label className="text-xs">
            <span className="label">{borc && kosulOn ? (effStrict ? "Son ödeme tarihi" : "Söz verdiğin tarih") : "Vade"} (opsiyonel)</span>
            <input type="date" className="input" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
        )}
      </div>
      {kosulOn && !taksitli && due && (
        <div className="space-y-1">
          <span className="label">Tarih türü</span>
          <Chips value={effStrict ? "kesin" : "soz"} onChange={(v) => setStrict(v === "kesin")} label="Tarih türü" cols="grid-cols-2" testid="borc-tarih-turu"
            options={TARIH_TURU.map((x) => ({ value: x.value, label: x.label }))} />
          <p className="text-xs text-stone-500 dark:text-stone-400">{TARIH_TURU.find((x) => x.value === (effStrict ? "kesin" : "soz"))!.note}</p>
        </div>
      )}
      {asset === "TRY" && (
        <AccountSelect value={accountId} onChange={setAccountId}
          label={direction === "alacak" ? "Hangi hesaptan verdin? (nakitse boş bırak)" : "Hangi hesaba geldi? (nakitse boş bırak)"} />
      )}
      <input className="input" placeholder="Not (opsiyonel)" value={note} onChange={(e) => setNote(e.target.value)} />
      </>)}
      {debt && debt.payments.some((p) => p.entry_id) && !planEkle && (
        <p className="text-xs text-stone-500 dark:text-stone-400">Bu kayda havaleyle bağlanmış ödeme var; kişiyi ya da miktarı değiştirirsen bağlantı korunur, kalan yeniden hesaplanır.</p>
      )}
      {err && <p className="text-xs text-red-600 dark:text-red-400" role="alert">{err}</p>}
      {!planEkle && (
        <div className="flex gap-2">
          <button className="btn-primary" disabled={save.isPending}
            onClick={() => { setErr(null); save.mutate(undefined, { onError: (e) => setErr(e.message) }); }}>{debt ? "Güncelle" : "Kaydet"}</button>
          <button className="btn-ghost" onClick={onDone}>Vazgeç</button>
        </div>
      )}
    </div>
  );
}

function PayDebt({ d, onDone }: { d: Debt; onDone: () => void }) {
  const ui = useUI();
  const [amount, setAmount] = useState(String(dueInstallment(d)).replace(".", ","));
  const [date, setDate] = useState(today());
  const [accountId, setAccountId] = useState<number | null>(d.account_id);
  const save = useSave(() => json("POST", `/api/debts/${d.id}/pay`, { amount: parseAmount(amount), paid_at: date,
    account_id: d.asset === "TRY" ? accountId : null }),
    // "Geri al": sunucu payment_id verirse yalnız bu ödeme silinir (öncekiler kalır); eski sunucuda yalnız ilk ödemede
    // DELETE /pay (hepsini siler, önceden ödeme yoksa birebir dönüş)
    (r: { payment_id?: number }) => {
      const undo = r?.payment_id != null ? () => api.undoDebtPayment(r.payment_id!)
        : d.paid_amount > 0 ? null : () => json("DELETE", `/api/debts/${d.id}/pay`);
      ui.toast(parseAmount(amount) + 1e-6 < d.remaining ? "Kısmi ödeme kaydedildi" : "Kapandı", undo ? { undo } : undefined);
      onDone();
    }, { inline: true });
  return (
    <div className="mb-3 space-y-2 rounded-lg border border-blue-200 bg-blue-50/50 p-3 dark:border-blue-900 dark:bg-blue-950/20">
      <div className="text-sm">
        <b>{maskText(d.person)}</b> · {d.direction === "borc" ? "ne kadar ödedin?" : "ne kadar aldın?"} ({assetLabel(d.asset)})
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input className="input text-right" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
      </div>
      {d.asset === "TRY" && (
        <AccountSelect value={accountId} onChange={setAccountId}
          label={d.direction === "borc" ? "Hangi hesaptan ödedin? (nakitse boş)" : "Hangi hesaba geldi? (nakitse boş)"} />
      )}
      <p className="text-xs text-stone-500">{d.schedule?.next
        ? `Sıradaki taksit yazılı; bütün borcu ödediysen kalanın tamamını (${fmtAsset(d.remaining, "TRY")}) yaz.`
        : "Kalanın tamamı kapatır; azını yazarsan kısmi ödeme olarak düşer."}</p>
      {save.error && <p className="text-xs text-red-600 dark:text-red-400" role="alert">{(save.error as Error).message}</p>}
      <div className="flex gap-2">
        <button className="btn-primary" disabled={!(parseAmount(amount) > 0) || save.isPending} onClick={() => save.mutate(undefined)}>Kaydet</button>
        <button className="btn-ghost" onClick={onDone}>Vazgeç</button>
      </div>
    </div>
  );
}
