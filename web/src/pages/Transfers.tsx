import { useQuery } from "@tanstack/react-query";
import { ArrowDownLeft, ArrowLeftRight, ArrowUpRight, ChevronDown, ChevronRight, HandCoins } from "lucide-react";
import { useMemo, useState } from "react";
import CategoryPicker from "../components/CategoryPicker";
import { useMeta, useSave } from "../components/hooks";
import { fmtAsset, type Debt } from "../components/Debts";
import Kpi from "../components/Kpi";
import { cx, dateTR, maskText, tl } from "../format";
import { useUI } from "../ui";

interface Person {
  name: string;
  sent: number;
  received: number;
  net: number;
  count: number;
  last_date: string;
  category: string | null;
  open: Record<string, number>; // açık borç/alacak birim birim (+ alacak, − borç)
}
interface Item {
  id: number;
  entry_date: string;
  description: string;
  amount: number;
  kind: "gelir" | "gider";
  category: string;
  counterparty: string;
  note: string | null;
}
interface Own {
  id: number;
  transfer_date: string;
  amount: number;
  account: string | null;
  description: string | null;
}
interface Data { people: Person[]; own: Own[]; items: Item[]; debts: Debt[] }

const shortBank = (b: string | null) => (b ? b.replace(/\s+(KATILIM\s+)?BANKASI.*$/i, "").replace(/\s+A\.?Ş\.?$/i, "") : "Hesap");

const put = (name: string, body: object) =>
  fetch(`/api/transfers/people/${encodeURIComponent(name)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then((r) => r.json() as Promise<{ updated: number }>);

/** Havale/FAST karşı tarafları: kime ne gitti, kimden ne geldi; kişi bazında kategori ve borç takibi. */
export default function Transfers() {
  const ui = useUI();
  const meta = useMeta();
  const q = useQuery({ queryKey: ["transfers"], queryFn: () => fetch("/api/transfers").then((r) => r.json() as Promise<Data>) });
  const [open, setOpen] = useState<string | null>(null);
  const save = useSave(({ name, body }: { name: string; body: object }) => put(name, body), (r) => {
    if (r.updated) ui.toast(`${r.updated} hareketin kategorisi güncellendi`);
  });
  const options = useMemo(() => [...new Set([...(meta.data?.categories ?? []), ...(meta.data?.income_categories ?? []),
    "Aile", "Borç / Alacak", "Kira & Aidat"])], [meta.data]);

  if (!q.data) return <div className="text-sm text-stone-500">Yükleniyor…</div>;
  const { people, own, items, debts } = q.data;
  const sent = people.reduce((s, p) => s + p.sent, 0);
  const received = people.reduce((s, p) => s + p.received, 0);
  const ownIn = own.filter((o) => o.amount > 0).reduce((s, o) => s + o.amount, 0);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Transferler</h1>
        <p className="text-sm text-stone-500">
          Hesap dökümlerindeki havale/FAST'ler kişi kişi. Bir kişiye kategori verirsen o kişiye giden/gelen tüm
          hareketler o kategoriye geçer. Borç/alacaklar ayrı sayfada (Borç / Alacak).
          Kendi hesapların arası aktarımlar gelir/gider sayılmaz, en altta ayrı durur.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Kpi label="Kişilere giden" value={sent} tone="red" />
        <Kpi label="Kişilerden gelen" value={received} tone="green" />
        <Kpi label="Kendi hesapların arası" value={ownIn} sub="toplama katılmaz" />
      </div>

      {debts.some((d) => !d.settled_at) && (
        <button className="card flex w-full items-center gap-3 p-4 text-left" onClick={() => ui.go("borclar")}>
          <HandCoins size={18} className="text-stone-500" />
          <span className="flex-1 text-sm">
            <b>{debts.filter((d) => !d.settled_at).length} açık borç/alacak</b>
            <span className="text-stone-500"> · Borç / Alacak sayfasında takip et</span>
          </span>
          <ChevronRight size={16} className="text-stone-400" />
        </button>
      )}

      <section className="card divide-y divide-stone-100 dark:divide-stone-800">
        {people.length === 0 && (
          <p className="p-4 text-sm text-stone-500">Henüz transfer yok. Ekstreler → Hesap hareketleri'nden banka dökümü (PDF/Excel) yükle.</p>
        )}
        {people.map((p) => (
          <div key={p.name}>
            <button className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={() => setOpen(open === p.name ? null : p.name)}>
              <div className="min-w-0 flex-1">
                <div className="font-medium">{maskText(p.name)}</div>
                <div className="text-xs text-stone-500">{p.count} hareket · son {dateTR(p.last_date)}{p.category && ` · ${p.category}`}</div>
                {Object.entries(p.open ?? {}).map(([asset, v]) => (
                  <span key={asset} className={cx("mt-1 mr-1 inline-block rounded-full px-1.5 py-0.5 text-xs",
                    v < 0 ? "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
                      : "bg-green-50 text-green-800 dark:bg-green-950/40 dark:text-green-300")}>
                    {v < 0 ? "borcun " : "alacağın "}{fmtAsset(Math.abs(v), asset)}
                  </span>
                ))}
              </div>
              <div className="text-right text-sm tabular-nums">
                {p.sent > 0 && <div className="flex items-center justify-end gap-1"><ArrowUpRight size={14} className="text-red-500" />{tl(p.sent)}</div>}
                {p.received > 0 && <div className="flex items-center justify-end gap-1 text-green-700 dark:text-green-400"><ArrowDownLeft size={14} />{tl(p.received)}</div>}
              </div>
              <ChevronDown size={16} className={cx("text-stone-400 transition-transform", open === p.name && "rotate-180")} />
            </button>
            {open === p.name && (
              <div className="space-y-3 px-4 pb-4">
                <div className="sm:w-1/2">
                  <span className="label">Kategori (tüm hareketlerine uygulanır)</span>
                  <CategoryPicker value={p.category ?? ""} options={options}
                    onChange={(v) => save.mutate({ name: p.name, body: { category: v } })} />
                </div>
                <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 text-sm dark:divide-stone-800 dark:border-stone-700">
                  {items.filter((i) => i.counterparty === p.name).map((i) => (
                    <li key={i.id} className="flex items-center gap-2 px-3 py-2">
                      <span className="w-20 shrink-0 text-xs text-stone-500">{dateTR(i.entry_date)}</span>
                      <span className="min-w-0 flex-1 truncate">{maskText(i.description)}</span>
                      <span className="hidden text-xs text-stone-500 sm:inline">{i.category}</span>
                      <span className={cx("tabular-nums", i.kind === "gelir" && "text-green-700 dark:text-green-400")}>
                        {tl(i.kind === "gelir" ? i.amount : -Math.abs(i.amount))}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ))}
      </section>

      {own.length > 0 && (
        <section className="card p-4">
          <h2 className="mb-1 flex items-center gap-2 font-semibold"><ArrowLeftRight size={18} /> Kendi hesapların arası</h2>
          <p className="mb-2 text-xs text-stone-500">Gelir/gider toplamlarına katılmaz; sadece para nereden nereye geçmiş görmen için.</p>
          <ul className="divide-y divide-stone-100 text-sm dark:divide-stone-800">
            {own.map((o) => (
              <li key={o.id} className="flex items-center gap-2 py-1.5">
                <span className="w-20 shrink-0 text-xs text-stone-500">{dateTR(o.transfer_date)}</span>
                <span className="min-w-0 flex-1 truncate">{shortBank(o.account)} {o.amount > 0 ? "← giriş" : "→ çıkış"}</span>
                <span className={cx("tabular-nums", o.amount > 0 && "text-green-700 dark:text-green-400")}>{tl(o.amount)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
