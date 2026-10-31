import { Check, Handshake, Plus, RotateCcw, Trash2 } from "lucide-react";
import { ask } from "../lib/ask";
import { call } from "../lib/apiError";
import { useState } from "react";
import { cx, dateTR, maskText, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import AccountSelect, { useAccounts } from "./AccountSelect";
import CategoryPicker from "./CategoryPicker";
import { useSave } from "./hooks";
import Segmented from "./Segmented";

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
}

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

  const del = useSave((id: number) => json("DELETE", `/api/debts/${id}`), () => ui.toast("Silindi"));
  const unpay = useSave((id: number) => json("DELETE", `/api/debts/${id}/pay`), () => ui.toast("Yeniden açıldı"));
  const writeOff = useSave((d: Debt) => json("POST", `/api/debts/${d.id}/write-off`, { on: today() }),
    () => ui.toast("Kapatıldı; kalan bugünün gider/gelirine yazıldı"));

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
          <div className="text-xs text-stone-500">
            {dateTR(d.debt_date)}
            {d.due_date && <span className={cx(late && "font-medium text-red-600")}> · vade {dateTR(d.due_date)}</span>}
            {d.paid_amount > 0 && !d.settled_at && ` · ${fmtAsset(d.paid_amount, d.asset)} ödendi`}
            {d.settled_at && (d.written_off_at ? ` · vazgeçildi ${dateTR(d.written_off_at)}` : ` · kapandı ${dateTR(d.settled_at)}`)}
            {d.note && ` · ${d.note}`}
            {d.asset === "TRY" && (accName(d.account_id) ? ` · ${accName(d.account_id)}` : " · hesap?")}
          </div>
        </div>
        <span className="shrink-0 text-right text-sm font-semibold tabular-nums">{fmtAsset(d.settled_at ? d.amount : d.remaining, d.asset)}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center justify-end gap-2">
        {d.settled_at ? (
          <button className="btn-ghost p-1.5 text-stone-400" aria-label="Yeniden aç" onClick={() => unpay.mutate(d.id)}><RotateCcw size={14} /></button>
        ) : (
          <button className="btn-primary px-2 py-1 text-xs" onClick={() => setPaying(d)}>
            <Check size={14} /> {d.direction === "borc" ? "Ödedim" : "Aldım"}
          </button>
        )}
        {!d.settled_at && (
          <button className="btn-ghost px-1.5 py-1 text-xs text-stone-500" title={d.direction === "alacak"
            ? "Geri gelmeyecek: kalan bugün gider olur" : "Karşı taraf sildi: kalan bugün gelir olur"}
            onClick={() => void ask(d.direction === "alacak"
              ? `${d.person} ödemeyecek mi? Kalan ${fmtAsset(d.remaining, d.asset)} bugün gider olarak yazılır.`
              : `${d.person} borcu sildi mi? Kalan ${fmtAsset(d.remaining, d.asset)} bugün gelir olarak yazılır.`, { confirm: "Evet, kapat", danger: true }).then((ok) => { if (ok) writeOff.mutate(d); })}>
            Vazgeçtim
          </button>
        )}
        <button className="btn-ghost p-1.5 text-stone-400 hover:text-red-600" aria-label="Sil" disabled={del.isPending}
          onClick={() => void ask("Kayıt silinsin mi? Bağlı havaleler yeniden normal gelir/gider sayılır.").then((ok) => { if (ok) del.mutate(d.id); })}>
          <Trash2 size={14} />
        </button>
      </div>
      {openId === d.id && (
        <div className="mt-2 space-y-2">
          <DebtForm people={people} debt={d} onDone={() => setOpenId(null)} />
          {d.payments.some((p) => !p.entry_id) && d.asset === "TRY" && (<>
            <div className="text-xs font-medium text-stone-500">Önceki ödemelerin hesabı</div>
            {accountsOf({ ...d, payments: d.payments })}
          </>)}
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

/** Yeni borç/alacak ya da (debt verilirse) var olanı düzenle: PATCH /api/debts/{id}. */
function DebtForm({ people, onDone, debt }: { people: string[]; onDone: () => void; debt?: Debt }) {
  const ui = useUI();
  const known = !debt || ASSETS.some((a) => a.value === debt.asset);
  const [direction, setDirection] = useState<Debt["direction"]>(debt?.direction ?? "borc");
  const [person, setPerson] = useState(debt?.person ?? "");
  const [asset, setAsset] = useState(debt ? (known ? debt.asset : "_") : "TRY");
  const [custom, setCustom] = useState(debt && !known ? debt.asset : "");
  const [amount, setAmount] = useState(debt ? String(debt.amount).replace(".", ",") : "");
  const [date, setDate] = useState(debt?.debt_date ?? today());
  const [due, setDue] = useState(debt?.due_date ?? "");
  const [note, setNote] = useState(debt?.note ?? "");
  const [accountId, setAccountId] = useState<number | null>(debt?.account_id ?? null);
  const [err, setErr] = useState<string | null>(null);
  const save = useSave(async () => {
    const a = parseAmount(amount);
    if (!person.trim() || !(a > 0)) throw new Error("Kişi ve miktar gerekli");
    const unit = asset === "_" ? custom.trim() : asset;
    if (!unit) throw new Error("Birim yaz (ör. 22 ayar bilezik)");
    if (debt && a + 1e-9 < debt.paid_amount) throw new Error(`Bu kayda ${fmtAsset(debt.paid_amount, debt.asset)} ödeme yapılmış; miktar bundan az olamaz`);
    const body = { person: person.trim(), direction, asset: unit, amount: a, debt_date: date,
      due_date: due || null, note: note.trim() || null, account_id: unit === "TRY" ? accountId : null };
    return debt ? json("PATCH", `/api/debts/${debt.id}`, body) : json("POST", "/api/debts", body);
  }, () => { ui.toast(debt ? "Güncellendi" : "Kaydedildi"); onDone(); });

  return (
    <div className="mb-3 space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
      <Segmented value={direction} onChange={setDirection} options={[
        { value: "borc", label: "Borç aldım", tone: "red" }, { value: "alacak", label: "Borç verdim", tone: "green" },
      ]} />
      <div>
        <span className="label">Kişi</span>
        <CategoryPicker value={person} onChange={setPerson} options={people} placeholder="Seç veya yaz" />
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
        <label className="text-xs">
          <span className="label">Vade (opsiyonel)</span>
          <input type="date" className="input" value={due} onChange={(e) => setDue(e.target.value)} />
        </label>
      </div>
      {asset === "TRY" && (
        <AccountSelect value={accountId} onChange={setAccountId}
          label={direction === "alacak" ? "Hangi hesaptan verdin? (nakitse boş bırak)" : "Hangi hesaba geldi? (nakitse boş bırak)"} />
      )}
      <input className="input" placeholder="Not (opsiyonel)" value={note} onChange={(e) => setNote(e.target.value)} />
      {debt && debt.payments.some((p) => p.entry_id) && (
        <p className="text-xs text-stone-500 dark:text-stone-400">Bu kayda havaleyle bağlanmış ödeme var; kişiyi ya da miktarı değiştirirsen bağlantı korunur, kalan yeniden hesaplanır.</p>
      )}
      {err && <p className="text-xs text-red-600 dark:text-red-400" role="alert">{err}</p>}
      <div className="flex gap-2">
        <button className="btn-primary" disabled={save.isPending}
          onClick={() => { setErr(null); save.mutate(undefined, { onError: (e) => setErr(e.message) }); }}>{debt ? "Güncelle" : "Kaydet"}</button>
        <button className="btn-ghost" onClick={onDone}>Vazgeç</button>
      </div>
    </div>
  );
}

function PayDebt({ d, onDone }: { d: Debt; onDone: () => void }) {
  const ui = useUI();
  const [amount, setAmount] = useState(String(d.remaining).replace(".", ","));
  const [date, setDate] = useState(today());
  const [accountId, setAccountId] = useState<number | null>(d.account_id);
  const save = useSave(() => json("POST", `/api/debts/${d.id}/pay`, { amount: parseAmount(amount), paid_at: date,
    account_id: d.asset === "TRY" ? accountId : null }),
    () => { ui.toast(parseAmount(amount) + 1e-6 < d.remaining ? "Kısmi ödeme kaydedildi" : "Kapandı"); onDone(); }, { inline: true });
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
      <p className="text-xs text-stone-500">Kalanın tamamı kapatır; azını yazarsan kısmi ödeme olarak düşer.</p>
      {save.error && <p className="text-xs text-red-600 dark:text-red-400" role="alert">{(save.error as Error).message}</p>}
      <div className="flex gap-2">
        <button className="btn-primary" disabled={!(parseAmount(amount) > 0) || save.isPending} onClick={() => save.mutate(undefined)}>Kaydet</button>
        <button className="btn-ghost" onClick={onDone}>Vazgeç</button>
      </div>
    </div>
  );
}
