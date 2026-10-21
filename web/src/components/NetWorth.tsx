import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Landmark, Plus, Trash2, Wallet } from "lucide-react";
import { useState } from "react";
import { cx, dateTR, parseAmount, tl } from "../format";
import { useUI } from "../ui";
import { useSave } from "./hooks";
import Wallets from "./Wallets";

interface Account { id: number; name: string; kind: "banka" | "nakit" | "diger"; balance: number; as_of: string | null; source: "dokum" | "elle" }
interface NW {
  accounts: Account[]; cash: number; cards: { title: string; amount: number; due_date: string; estimated: boolean }[];
  card_debt: number; card_estimated: number; receivable: number; payable: number; net: number; net_after_estimates: number;
  wallets: { id: number; name: string; balance: number }[]; in_wallets: number;
  stale: string[];
}

const json = async <T,>(method: string, url: string, body?: unknown): Promise<T> => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
  return j as T;
};

/** Gerçek durum: hesaplardaki para (dökümden ya da elle) + kartlar − kart borcu ± borç/alacak.
 *  compact: Özet'teki tek satırlık özet (dokununca Hesaplar sayfası); değilse sayfanın tamamı. */
export default function NetWorth({ compact = false }: { compact?: boolean }) {
  const ui = useUI();
  const q = useQuery({ queryKey: ["net-worth"], queryFn: () => json<NW>("GET", "/api/net-worth") });
  const [open, setOpen] = useState(!compact);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [bal, setBal] = useState("");
  const [kind, setKind] = useState<"banka" | "nakit" | "diger">("banka");
  const add = useSave(() => json("POST", "/api/accounts", { name, kind, balance: parseAmount(bal) }),
    () => { ui.toast("Hesap eklendi"); setAdding(false); setBal(""); });
  const edit = useSave(({ id, balance }: { id: number; balance: number }) => json("PATCH", `/api/accounts/${id}`, { balance }),
    () => ui.toast("Bakiye güncellendi"));
  const del = useSave((id: number) => json("DELETE", `/api/accounts/${id}`), () => ui.toast("Silindi"));
  const d = q.data;
  if (!d) return null;
  const empty = !d.accounts.length && !d.wallets.length;
  if (compact) {
    return (
      <button className="card flex w-full items-center gap-3 p-4 text-left" onClick={() => ui.go("hesaplar")}>
        <Landmark size={18} className="shrink-0 text-stone-500" />
        {empty ? (
          <span className="flex-1 text-sm text-stone-500">Hesaplarını, nakdini ve yemek/hediye kartlarını ekle: gerçek durumunu görürsün.</span>
        ) : (
          <span className="min-w-0 flex-1">
            <span className="block text-xs text-stone-500">Net durum</span>
            <span className={cx("block text-lg font-semibold tabular-nums", d.net < 0 ? "text-red-600" : "text-green-700 dark:text-green-400")}>{tl(d.net)}</span>
            <span className="block text-xs text-stone-500">
              Bankada {tl(d.cash)}{d.in_wallets ? ` · kartlarda ${tl(d.in_wallets)}` : ""}{d.card_debt ? ` · kart borcu ${tl(d.card_debt)}` : ""}
            </span>
          </span>
        )}
        <ChevronRight size={16} className="shrink-0 text-stone-400" />
      </button>
    );
  }
  return (
    <section className="card">
      <button className="flex w-full items-center gap-3 p-4 text-left" onClick={() => setOpen(!open)}>
        <Landmark size={18} className="shrink-0 text-stone-500" />
        <div className="min-w-0 flex-1">
          <div className="text-xs text-stone-500">Net durum (bankada + kartlarda − kart borcu ± borç/alacak)</div>
          <div className={cx("text-lg font-semibold tabular-nums", d.net < 0 ? "text-red-600" : "text-green-700 dark:text-green-400")}>{tl(d.net)}</div>
          <div className="text-xs text-stone-500">
            Bankada {tl(d.cash)}{d.in_wallets ? ` · kartlarda ${tl(d.in_wallets)}` : ""}{d.card_debt ? ` · kart borcu ${tl(d.card_debt)}` : ""}
            {d.card_estimated ? ` · kesilecek ekstreler ~${tl(d.card_estimated)}` : ""}
          </div>
        </div>
        <ChevronDown size={16} className={cx("text-stone-400 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-stone-100 px-4 pt-3 pb-4 dark:border-stone-800">
          <ul className="space-y-1.5 text-sm">
            {d.accounts.map((a) => (
              <li key={a.id} className="flex items-center gap-2">
                {a.kind === "banka" ? <Landmark size={14} className="text-stone-400" /> : <Wallet size={14} className="text-stone-400" />}
                <span className="flex-1">
                  {a.name}
                  <span className={cx("ml-1 text-xs", d.stale.includes(a.name) ? "text-amber-600" : "text-stone-500")}>
                    {a.as_of ? dateTR(a.as_of) : ""}{a.source === "dokum" ? " · dökümden" : " · elle"}
                    {d.stale.includes(a.name) && " · eski, yeni döküm yükle"}
                  </span>
                </span>
                <button className="tabular-nums underline decoration-dotted" title="Bakiyeyi düzelt"
                  onClick={() => {
                    const v = window.prompt(`${a.name} bakiyesi (bugün):`, String(a.balance).replace(".", ","));
                    if (v != null && !Number.isNaN(parseAmount(v))) edit.mutate({ id: a.id, balance: parseAmount(v) });
                  }}>{tl(a.balance)}</button>
                <button className="btn-ghost p-1 text-stone-400 hover:text-red-600" aria-label="Sil"
                  onClick={() => window.confirm(`${a.name} silinsin mi?`) && del.mutate(a.id)}><Trash2 size={13} /></button>
              </li>
            ))}
            {d.cards.filter((c) => !c.estimated).map((c) => (
              <li key={c.title + c.due_date} className="flex justify-between gap-2 text-stone-600 dark:text-stone-400">
                <span>{c.title} <span className="text-xs">· son ödeme {dateTR(c.due_date)}</span></span>
                <span className="tabular-nums text-red-600">−{tl(c.amount)}</span>
              </li>
            ))}
            {d.receivable > 0 && <li className="flex justify-between"><span>Alacakların</span><span className="tabular-nums text-green-700">+{tl(d.receivable)}</span></li>}
            {d.payable > 0 && <li className="flex justify-between"><span>Borçların</span><span className="tabular-nums text-red-600">−{tl(d.payable)}</span></li>}
          </ul>
          {adding ? (
            <div className="flex gap-2">
              <select className="input w-28" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
                <option value="banka">Banka</option><option value="nakit">Nakit</option><option value="diger">Diğer</option>
              </select>
              <input className="input" placeholder={kind === "banka" ? "ör. Halkbank" : "ör. Cüzdan"} value={name} onChange={(e) => setName(e.target.value)} />
              <input className="input w-32 text-right" inputMode="decimal" placeholder="Bakiye" value={bal} onChange={(e) => setBal(e.target.value)} />
              <button className="btn-primary" disabled={!name.trim() || !(parseAmount(bal) >= 0)} onClick={() => add.mutate(undefined)}>Ekle</button>
            </div>
          ) : (
            <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setAdding(true)}><Plus size={14} /> Banka / nakit hesabı ekle</button>
          )}
          <Wallets />
          {d.card_estimated > 0 && (
            <p className="text-xs text-stone-500">Henüz kesilmemiş ekstreler (tahmini {tl(d.card_estimated)}) düşülünce: <b>{tl(d.net_after_estimates)}</b></p>
          )}
          <p className="text-xs text-stone-500">
            Banka bakiyesini elle girebilirsin; hesap dökümü yüklersen (elle, e-postayla ya da Telegram'dan) kendiliğinden güncellenir.
          </p>
        </div>
      )}
    </section>
  );
}
