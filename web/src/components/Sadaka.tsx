import { useQuery } from "@tanstack/react-query";
import { detailText } from "../lib/apiError";
import { Eye, EyeOff, HeartHandshake, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { dateTR, parseAmount, tlShown } from "../format";
import { useUI } from "../ui";
import AccountSelect from "./AccountSelect";
import { useSave } from "./hooks";
import { DONATION_KINDS } from "./MarkDonation";
import Modal from "./Modal";
import HolyDays from "./HolyDays";

interface Item { id: number; kind: string; label: string; amount: number; given_date: string; recipient: string | null; note: string | null }
interface ZP { id: number; amount: number; pay_date: string; person: string; note: string | null }
interface Pending { id: number; source: "entry" | "card"; date: string; description: string; amount: number }
interface Data { items: Item[]; zakat_payments: ZP[]; pending: Pending[]; years: { year: string; sadaka: number; zekat: number }[];
  this_month: number; this_year: number }

const call = async (method: string, url: string, body?: unknown) => {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(detailText(j.detail, r.status));
  return j;
};
const today = () => new Date().toISOString().slice(0, 10);
// "Sağ elin verdiğini sol el bilmesin": tutarlar bu sayfada varsayılan gizli (cihaz ayarı değil, her açılışta)
const MASK = "••••";

function AddForm({ onClose }: { onClose: () => void }) {
  const ui = useUI();
  const [kind, setKind] = useState("sadaka");
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(today());
  const [recipient, setRecipient] = useState("");
  const [asExpense, setAsExpense] = useState(true);
  const [account, setAccount] = useState<number | null>(null);
  const save = useSave(() => call("POST", "/api/donations", { kind, amount: parseAmount(amount), given_date: date, recipient: recipient || null,
    as_expense: asExpense, account_id: asExpense ? account : null }), () => { ui.toast("Kaydedildi. Allah kabul etsin."); onClose(); });
  return (
    <Modal title="Sadaka / bağış ekle" onClose={onClose} footer={
      <button className="btn-primary w-full justify-center" disabled={!(parseAmount(amount) > 0) || save.isPending}
        onClick={() => save.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Kaydet</button>}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label" htmlFor="d-kind">Tür</label>
            <select id="d-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
              {Object.entries(DONATION_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="d-amt">Tutar</label>
            <input id="d-amt" className="input text-right" inputMode="decimal" autoFocus value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label" htmlFor="d-date">Tarih</label>
            <input id="d-date" type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="d-to">Kime (ops.)</label>
            <input id="d-to" className="input" placeholder="kişi ya da kurum" value={recipient} onChange={(e) => setRecipient(e.target.value)} />
          </div>
        </div>
        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={asExpense} onChange={(e) => setAsExpense(e.target.checked)} />
          <span>Nakit verdim, gider olarak da yaz
            <span className="block text-xs text-stone-500">Havale/kartla verdiysen işaretleme: döküm gelince o hareketi "sadaka" diye işaretlersin, iki kez sayılmaz.</span>
          </span>
        </label>
        {asExpense && <AccountSelect id="d-acc" label="Hangi hesaptan / cüzdandan (ops.)" value={account} onChange={setAccount} />}
      </div>
    </Modal>
  );
}

/** Sadaka ve bağışlar: elle kayıt, dökümden gelenleri işaretleme, yıllara göre toplam. Tutarlar varsayılan gizli. */
export default function Sadaka() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["donations"], queryFn: () => call("GET", "/api/donations") as Promise<Data> });
  const [show, setShow] = useState(false);
  const [adding, setAdding] = useState(false);
  const mark = useSave((a: { p: Pending; kind: string }) => call("POST", "/api/donations/mark", { source: a.p.source, id: a.p.id, kind: a.kind }),
    () => ui.toast("Kaydedildi"));
  const del = useSave((id: number) => call("DELETE", `/api/donations/${id}`));
  const d = q.data;
  const m = (x: number) => (show ? tlShown(x) : MASK); // bu sayfanın gözü genel gizlemeden bağımsız
  if (!d) return null;

  return (
    <div className="space-y-4">
      <HolyDays />
      <section className="card p-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 className="flex items-center gap-2 font-semibold"><HeartHandshake size={18} /> Sadaka ve bağış</h2>
            <p className="text-xs text-stone-500">Bu ay {m(d.this_month)} · bu yıl {m(d.this_year)}</p>
          </div>
          <div className="flex gap-1">
            <button className="btn-ghost p-2" aria-label={show ? "Tutarları gizle" : "Tutarları göster"} onClick={() => setShow(!show)}>
              {show ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
            <button className="btn-ghost px-2 py-1 text-sm" onClick={() => setAdding(true)}><Plus size={16} /> Ekle</button>
          </div>
        </div>
        <p className="mt-1 text-xs text-stone-500">"Sağ elin verdiğini sol el bilmesin": tutarlar bu sayfada gizli açılır.</p>
      </section>

      {d.pending.length > 0 && (
        <section className="card p-4">
          <h3 className="text-sm font-medium">Dökümde bağış görünen hareketler: sadaka mı, zekât mı?</h3>
          <ul className="mt-2 divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {d.pending.map((p) => (
              <li key={`${p.source}${p.id}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="min-w-0">{dateTR(p.date)} · <span className="text-stone-500">{p.description}</span> · {m(p.amount)}</span>
                <select className="input w-auto px-2 py-1 text-xs" defaultValue="" aria-label="Tür seç"
                  onChange={(e) => e.target.value && mark.mutate({ p, kind: e.target.value }, { onError: (err) => ui.toast(err.message) })}>
                  <option value="" disabled>Seç…</option>
                  <option value="zekat">Zekât (bu yılın)</option>
                  {Object.entries(DONATION_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card p-4">
        <h3 className="text-sm font-medium">Geçmiş</h3>
        {!d.items.length && !d.zakat_payments.length && (
          <p className="mt-1 text-sm text-stone-500">Henüz kayıt yok. Nakit verdiğin sadakayı "Ekle" ile gir; havale/kartla verdiklerin dökümden gelince burada sorulur.</p>
        )}
        <ul className="mt-2 divide-y divide-stone-100 text-sm dark:divide-stone-800">
          {[...d.items.map((i) => ({ key: `d${i.id}`, date: i.given_date, label: i.label, who: i.recipient ?? i.note, amount: i.amount, id: i.id })),
            ...d.zakat_payments.map((z) => ({ key: `z${z.id}`, date: z.pay_date, label: `Zekât · ${z.person}`, who: z.note, amount: z.amount, id: null as number | null }))]
            .sort((a, b) => b.date.localeCompare(a.date)).map((x) => (
              <li key={x.key} className="flex items-center justify-between gap-2 py-2">
                <span className="min-w-0">{dateTR(x.date)} · {x.label}{x.who && <span className="text-xs text-stone-500"> · {x.who}</span>}</span>
                <span className="flex items-center gap-1 tabular-nums">{m(x.amount)}
                  {x.id != null && <button className="btn-ghost p-0.5 text-stone-400 hover:text-red-600" aria-label="Sil"
                    onClick={() => window.confirm("Kayıt silinsin mi?") && del.mutate(x.id!)}><Trash2 size={12} /></button>}
                </span>
              </li>
            ))}
        </ul>
        {d.years.length > 0 && (
          <div className="mt-3 border-t border-stone-100 pt-2 text-xs text-stone-500 dark:border-stone-800">
            {d.years.map((y) => <div key={y.year}>{y.year}: sadaka/bağış {m(y.sadaka)} · zekât {m(y.zekat)}</div>)}
          </div>
        )}
      </section>
      {adding && <AddForm onClose={() => setAdding(false)} />}
    </div>
  );
}
