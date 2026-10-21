import { useQuery } from "@tanstack/react-query";
import { ChevronDown, CreditCard, Gift, Plus, Shirt, Trash2, Utensils } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, monthLabel, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";

interface Wallet {
  id: number; name: string; card_type: "yemek" | "hediye" | "giyim" | "diger"; current: number; expires_at: string | null;
  schedule: { id: number; amount: number; day_of_month: number; start_month: string; end_month: string | null } | null;
  recent: { id: number; entry_date: string; description: string; amount: number; kind: "gelir" | "gider" }[];
}

const TYPES = { yemek: "Yemek", hediye: "Hediye", giyim: "Giyim", diger: "Diğer" } as const;
const Icon = ({ t }: { t: Wallet["card_type"] }) =>
  t === "yemek" ? <Utensils size={14} /> : t === "hediye" ? <Gift size={14} /> : t === "giyim" ? <Shirt size={14} /> : <CreditCard size={14} />;

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
};

function WalletRow({ w }: { w: Wallet }) {
  const ui = useUI();
  const [open, setOpen] = useState(false);
  const s = w.schedule;
  const active = s && !s.end_month;
  const save = useSave((a: { method: string; url: string; body?: unknown; msg: string }) => json(a.method, a.url, a.body),
    (_r) => undefined);
  const act = (method: string, url: string, body: unknown, msg: string) =>
    save.mutate({ method, url, body, msg }, { onSuccess: () => ui.toast(msg), onError: (e) => ui.toast(e.message) });
  const ask = (q: string, def = "") => { const v = window.prompt(q, def); return v == null ? null : parseAmount(v); };
  return (
    <li className="py-2">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-stone-400"><Icon t={w.card_type} /></span>
        <button className="min-w-0 flex-1 text-left" onClick={() => setOpen(!open)}>
          {w.name} <span className="text-xs text-stone-500">· {TYPES[w.card_type]}
            {s && (active ? ` · her ay ${s.day_of_month}. gün ${tl(s.amount)}` : ` · yükleme durdu (son: ${monthLabel(s.end_month!)})`)}
            {w.expires_at && ` · son kullanma ${dateTR(w.expires_at)}`}</span>
        </button>
        <button className="tabular-nums underline decoration-dotted" title="Bakiyeyi düzelt"
          onClick={() => { const v = ask(`${w.name} bakiyesi (kart uygulamasında görünen):`, String(w.current).replace(".", ","));
            if (v != null && !Number.isNaN(v)) act("PATCH", `/api/wallets/${w.id}`, { balance: v }, "Bakiye güncellendi"); }}>
          {tl(w.current)}
        </button>
        <ChevronDown size={14} className={cx("text-stone-400 transition-transform", open && "rotate-180")} onClick={() => setOpen(!open)} />
      </div>
      {open && (
        <div className="mt-2 space-y-2 pl-6 text-xs">
          <div className="flex flex-wrap gap-1.5">
            <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
              onClick={() => { const v = ask("Yüklenen tutar:"); if (v && v > 0) act("POST", `/api/wallets/${w.id}/load`, { amount: v }, "Yükleme eklendi"); }}>
              + Yükleme
            </button>
            <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
              onClick={() => {
                const v = ask("Aylık yükleme tutarı:", s ? String(s.amount).replace(".", ",") : "");
                if (!v || v <= 0) return;
                const d = Number(window.prompt("Ayın kaçında yükleniyor?", String(s?.day_of_month ?? 1)) ?? "");
                if (d >= 1 && d <= 31) act("PUT", `/api/wallets/${w.id}/schedule`, { amount: v, day: d }, "Aylık yükleme ayarlandı");
              }}>{s ? "Aylık tutarı değiştir" : "Aylık yükleme ekle"}</button>
            {active && <>
              <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
                onClick={() => act("POST", `/api/wallets/${w.id}/schedule/stop`, { mode: "after_this" }, "Bu ayki yükleme son")}>Bu ay son</button>
              <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
                onClick={() => act("POST", `/api/wallets/${w.id}/schedule/stop`, { mode: "now" }, "Yükleme durduruldu")}>Durdur</button>
            </>}
            {s && !active && (
              <button className="btn-ghost border border-stone-300 px-2 py-1 dark:border-stone-700"
                onClick={() => act("POST", `/api/wallets/${w.id}/schedule/stop`, { mode: "resume" }, "Yükleme devam ediyor")}>Devam et</button>
            )}
            <button className="btn-ghost px-2 py-1 text-red-600"
              onClick={() => window.confirm(`${w.name} silinsin mi? Geçmiş harcamalar kayıtlarda kalır.`) && act("DELETE", `/api/wallets/${w.id}`, undefined, "Kart silindi")}>
              <Trash2 size={13} />
            </button>
          </div>
          <ul className="text-stone-500">
            {w.recent.map((e) => (
              <li key={e.id} className="flex justify-between gap-2">
                <span className="truncate">{dateTR(e.entry_date)} · {e.description}</span>
                <span className={cx("tabular-nums", e.kind === "gelir" && "text-green-700")}>{e.kind === "gelir" ? "+" : "−"}{tl(e.amount)}</span>
              </li>
            ))}
            {!w.recent.length && <li>Henüz hareket yok. Gider eklerken ödeme şeklinde "Yemek/hediye k." seç.</li>}
          </ul>
        </div>
      )}
    </li>
  );
}

/** Ön ödemeli kartlar: yemek, hediye, giyim. Bakiye uygulamada tutulur; aylık yükleme kendiliğinden işler. */
export default function Wallets() {
  const ui = useUI();
  const q = useQuery({ queryKey: ["wallets"], queryFn: () => json<Wallet[]>("GET", "/api/wallets") });
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", card_type: "yemek" as Wallet["card_type"], balance: "", monthly: "", day: "1", expires: "" });
  const add = useSave(() => json("POST", "/api/wallets", {
    name: f.name, card_type: f.card_type, balance: parseAmount(f.balance) || 0,
    monthly_amount: parseAmount(f.monthly) > 0 ? parseAmount(f.monthly) : null, load_day: Number(f.day) || 1,
    expires_at: f.expires || null,
  }), () => { ui.toast("Kart eklendi"); setAdding(false); setF({ name: "", card_type: "yemek", balance: "", monthly: "", day: "1", expires: "" }); });
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v });
  return (
    <div>
      <div className="text-xs font-medium text-stone-500">Yemek / hediye / giyim kartları</div>
      <ul className="divide-y divide-stone-100 dark:divide-stone-800">{q.data?.map((w) => <WalletRow key={w.id} w={w} />)}</ul>
      {adding ? (
        <div className="mt-2 space-y-2 rounded-lg border border-stone-200 p-3 dark:border-stone-700">
          <div className="grid grid-cols-2 gap-2">
            <input className="input" placeholder="Ad (ör. Multinet)" value={f.name} onChange={(e) => set("name", e.target.value)} />
            <select className="input" value={f.card_type} onChange={(e) => set("card_type", e.target.value)}>
              {Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v} kartı</option>)}
            </select>
            <label className="text-xs"><span className="label">Şu anki bakiye</span>
              <input className="input text-right" inputMode="decimal" value={f.balance} onChange={(e) => set("balance", e.target.value)} /></label>
            <label className="text-xs"><span className="label">Aylık yükleme (ops.)</span>
              <input className="input text-right" inputMode="decimal" value={f.monthly} onChange={(e) => set("monthly", e.target.value)} /></label>
            {parseAmount(f.monthly) > 0 && (
              <label className="text-xs"><span className="label">Ayın kaçında</span>
                <input className="input" inputMode="numeric" value={f.day} onChange={(e) => set("day", e.target.value)} /></label>
            )}
            {f.card_type !== "yemek" && (
              <label className="text-xs"><span className="label">Son kullanma (ops.)</span>
                <input type="date" className="input" value={f.expires} onChange={(e) => set("expires", e.target.value)} /></label>
            )}
          </div>
          <p className="text-xs text-stone-500">Yüklemeler "Yan Haklar" geliri, karttan harcamalar gider sayılır; bakiye banka bakiyene karışmaz.</p>
          <div className="flex gap-2">
            <button className="btn-primary" disabled={!f.name.trim() || add.isPending}
              onClick={() => add.mutate(undefined, { onError: (e) => ui.toast(e.message) })}>Ekle</button>
            <button className="btn-ghost" onClick={() => setAdding(false)}>Vazgeç</button>
          </div>
        </div>
      ) : (
        <button className="btn-ghost mt-1 px-2 py-1 text-xs" onClick={() => setAdding(true)}><Plus size={14} /> Yemek / hediye kartı ekle</button>
      )}
    </div>
  );
}
